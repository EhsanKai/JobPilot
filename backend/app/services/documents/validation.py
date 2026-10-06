"""Truth validation of AI-generated documents (V0.8).

The AI's output is *claimed* content. Nothing becomes a document until this
module has checked it, and a document that fails is rejected whole -- text is
never silently repaired, because a repair would be a guess.

Two layers, both deterministic and AI-free (the V0.2 truth-guard approach,
applied to generated rather than extracted text):

1. **Structure and traceability.** Every piece of AI-written text must cite
   facts (`source_fact_ids`); each id must exist; a bullet may only cite
   facts of the role it describes; every work experience entry appears
   exactly once; skills are existing skill facts; a letter has the expected
   shape, the application's language and no pay talk. Employers, titles,
   dates, degrees and skill names are not even writable by the AI (see
   `assembly.py`), so they cannot be invented.

2. **Claims.** The text is checked against the facts *it cites* (`claims.py`):
   numbers, taxonomy terms (skills, languages, degrees), technology-shaped
   tokens, organizations, leadership / seniority / credential / achievement
   claims, and (letters) claims about the company or the writer's life. Each
   must be supported by a cited fact. Support is semantic-free but
   paraphrase-tolerant: wording may change freely, the claims may not.

The job's own title, company and location are blanked out of a text before
claims are checked, so "applying for the Senior Project Manager role at
Example GmbH" is not mistaken for the candidate claiming to be a senior
project manager.

Reports list *why* a document was rejected (`Violation`: code, message,
location, the offending words), never the rejected document.
"""

from collections.abc import Callable
from typing import Protocol

from app.schemas.document_ai import AICoverLetter, AITailoredCV
from app.schemas.document_content import ValidationReport, Violation
from app.services.documents import claims
from app.services.documents.context import GenerationContext
from app.services.documents.facts import Fact, FactCatalog

#: A report lists at most this many violations (the first ones found).
MAX_VIOLATIONS = 20

_TERM_CODES = {
    "skills": "UNSUPPORTED_SKILL",
    "languages": "UNSUPPORTED_LANGUAGE",
    "education_levels": "UNSUPPORTED_DEGREE",
}

_CLAIM_MESSAGES = {
    "UNSUPPORTED_ROLE_CLAIM": "implies a role, level or duty the cited facts do not state",
    "UNSUPPORTED_CREDENTIAL": "implies a certification or degree the cited facts do not state",
    "UNSUPPORTED_ACHIEVEMENT": "implies an achievement or result the cited facts do not state",
    "UNSUPPORTED_EXPERIENCE_CLAIM": "claims an amount of experience the cited facts do not state",
}


class ClaimContext(Protocol):
    """What the claim checks need to know: the facts that may be cited, the job's
    own names (a text may refer to them) and the language. A `GenerationContext`
    is one; the V0.9 answer and Humanize validators build lighter ones."""

    @property
    def language(self) -> str: ...
    @property
    def catalog(self) -> FactCatalog: ...
    @property
    def job_fact_ids(self) -> frozenset[str]: ...
    @property
    def title_variants(self) -> tuple[str, ...]: ...
    @property
    def name_variants(self) -> tuple[str, ...]: ...
    @property
    def company_name(self) -> str: ...


class _Run:
    """The state of validating one document."""

    def __init__(self, context: ClaimContext) -> None:
        self.context = context
        self.violations: list[Violation] = []
        self.units = 0
        self._seen: set[tuple[str, str, str]] = set()
        self.text_pieces: list[str] = []

    # --- Reporting -------------------------------------------------------------------------

    def add(self, code: str, message: str, location: str, excerpt: str | None = None) -> None:
        key = (code, location, (excerpt or "").lower())
        if key in self._seen or len(self.violations) >= MAX_VIOLATIONS:
            return
        self._seen.add(key)
        self.violations.append(
            Violation(
                code=code,
                message=message[:600],
                location=location,
                excerpt=excerpt[:300] if excerpt else None,
            )
        )

    def report(self) -> ValidationReport:
        return ValidationReport(
            status="failed" if self.violations else "passed",
            text_units_checked=self.units,
            violations=self.violations,
        )

    # --- Citations -------------------------------------------------------------------------

    def cited(
        self,
        location: str,
        ids: list[str],
        *,
        allow: Callable[[Fact], bool] = lambda fact: True,
        scope: str = "the candidate facts",
        required: bool = True,
    ) -> list[Fact]:
        """The facts `ids` name, reporting unknown ids, ids outside `scope`,
        and a missing citation."""
        if required and not ids:
            self.add(
                "MISSING_SOURCE",
                "This text does not say which resume facts it is based on.",
                location,
            )
        facts: list[Fact] = []
        for fact_id in dict.fromkeys(ids):
            fact = self.context.catalog.get(fact_id)
            if fact is None:
                if fact_id in self.context.job_fact_ids:
                    self.add(
                        "JOB_FACT_AS_SOURCE",
                        "A fact about the job was cited as a fact about the candidate.",
                        location,
                        fact_id,
                    )
                else:
                    self.add(
                        "UNKNOWN_FACT_ID",
                        "The text cites a fact that does not exist.",
                        location,
                        fact_id,
                    )
            elif not allow(fact):
                self.add(
                    "FOREIGN_FACT",
                    f"The text cites a fact outside {scope}.",
                    location,
                    fact_id,
                )
            else:
                facts.append(fact)
        return facts

    # --- Claims ----------------------------------------------------------------------------

    def check_text(
        self, location: str, text: str, facts: list[Fact], *, letter: bool = False
    ) -> None:
        """Check one piece of AI-written text against the facts it cites."""
        self.units += 1
        self.text_pieces.append(text)
        if not text.strip():
            self.add("EMPTY_TEXT", "This text is empty.", location)
            return
        support = claims.Support.of([fact.text for fact in facts])
        if letter:
            # A letter may name the position, the company and the place it
            # applies to. A CV never refers to the job, so nothing is exempt.
            masked = claims.mask_job_references(
                text,
                titles=self.context.title_variants,
                names=self.context.name_variants,
            )
            self.check_past_employment(location, text, support)
        else:
            masked = text

        if word := claims.salary_mention(masked):
            self.add(
                "SALARY_MENTION_NOT_ALLOWED",
                "Generated text may not mention salary or pay: JobPilot adds the salary "
                "sentence itself, and only when you chose to.",
                location,
                word,
            )

        for written, forms in claims.number_forms(masked).items():
            if forms.isdisjoint(support.numbers):
                self.add(
                    "UNSUPPORTED_NUMBER",
                    "A number appears that the cited facts do not state.",
                    location,
                    written,
                )

        flagged: set[str] = set()
        for key, alias in claims.term_keys(masked).items():
            if key not in support.terms:
                vocabulary = key.split(":", 1)[0]
                self.add(
                    _TERM_CODES.get(vocabulary, "UNSUPPORTED_SKILL"),
                    "A skill, language or degree is named that the cited facts do not state.",
                    location,
                    alias,
                )
                flagged.add(alias)

        for token in claims.technology_tokens(masked):
            if token.lower() in flagged or support.has_word(token):
                continue
            keys = set(claims.term_keys(token))
            if keys and keys <= support.terms:
                continue
            self.add(
                "UNSUPPORTED_TECHNOLOGY",
                "A tool, technology or acronym is named that the cited facts do not state.",
                location,
                token,
            )

        for organization in claims.organizations(masked):
            if not support.has_name(organization):
                self.add(
                    "UNSUPPORTED_ORGANIZATION",
                    "An organization is named that the cited facts do not state.",
                    location,
                    organization,
                )

        for code, groups in claims.claim_groups(masked).items():
            supported = support.claim_groups.get(code, frozenset())
            for group, word in groups.items():
                if group not in supported:
                    self.add(code, f"This text {_CLAIM_MESSAGES[code]}.", location, word)

        if self.context.language == "en":
            for name in claims.midsentence_names(masked):
                if name.lower() in flagged or support.has_word(name):
                    continue
                self.add(
                    "UNSUPPORTED_NAME",
                    "A name is used that the cited facts do not contain.",
                    location,
                    name,
                )

        if letter:
            for code, word in claims.letter_claims(masked).items():
                self.add(
                    code,
                    "A letter may not claim knowledge of the company or personal "
                    "circumstances that no resume fact supports.",
                    location,
                    word,
                )

    def check_past_employment(self, location: str, text: str, support: claims.Support) -> None:
        """ "I worked at <the company I am applying to>" is an invented
        employer unless a cited fact says so."""
        company = self.context.company_name
        if not company.strip() or support.has_name(company):
            return
        for sentence in claims.sentences_naming(text, self.context.name_variants):
            if claims.PAST_EMPLOYMENT.search(claims.fold(sentence)):
                self.add(
                    "UNSUPPORTED_ORGANIZATION",
                    "The text claims past employment at the company applied to, which the "
                    "cited facts do not state.",
                    location,
                    company,
                )

    def check_language(self) -> None:
        if claims.language_mismatch("\n".join(self.text_pieces), self.context.language):
            self.add(
                "WRONG_LANGUAGE",
                "The text is not written in the application language "
                f"({self.context.language}).",
                "document",
            )


#: The claim checker, for other generated content that must meet the same truth
#: standard (V0.9: answers to application questions, humanized letters).
ValidationRun = _Run


def _belongs_to(entry_id: str) -> Callable[[Fact], bool]:
    def allowed(fact: Fact) -> bool:
        return fact.group == entry_id

    return allowed


# --- Tailored CV ----------------------------------------------------------------------------------


def validate_cv(output: AITailoredCV, context: GenerationContext) -> ValidationReport:
    run = _Run(context)
    candidate = context.candidate

    run.check_text(
        "summary",
        output.summary.text,
        run.cited("summary", output.summary.source_fact_ids),
    )

    expected = candidate.experience_ids()
    seen: set[str] = set()
    for i, entry in enumerate(output.experience):
        location = f"experience[{i}]"
        entry_id = entry.source_experience_id
        if entry_id not in expected:
            run.add(
                "UNKNOWN_EXPERIENCE",
                "The CV contains a role that is not in the confirmed resume.",
                location,
                entry_id,
            )
            continue
        if entry_id in seen:
            run.add("DUPLICATE_EXPERIENCE", "A role appears more than once.", location, entry_id)
            continue
        seen.add(entry_id)
        for j, bullet in enumerate(entry.bullets):
            bullet_location = f"{location}.bullets[{j}]"
            facts = run.cited(
                bullet_location,
                bullet.source_fact_ids,
                allow=_belongs_to(entry_id),
                scope="the role the bullet describes",
            )
            # The role's own header (title, employer, dates) always supports its bullets.
            header = context.catalog.get(entry_id)
            run.check_text(bullet_location, bullet.text, [*facts, *([header] if header else [])])
    for entry_id in expected:
        if entry_id not in seen:
            run.add(
                "MISSING_EXPERIENCE",
                "A role from the resume is missing: a tailored CV keeps every role.",
                "experience",
                entry_id,
            )

    skill_ids = {fact.id for fact in candidate.catalog.of_kind("skill")}
    chosen: set[str] = set()
    for i, skill in enumerate(output.skills):
        location = f"skills[{i}]"
        if skill.source_fact_id not in skill_ids:
            run.add(
                "UNKNOWN_SKILL",
                "The CV lists a skill that is not in the confirmed resume.",
                location,
                skill.source_fact_id,
            )
        elif skill.source_fact_id in chosen:
            run.add(
                "DUPLICATE_SKILL", "A skill appears more than once.", location, skill.source_fact_id
            )
        chosen.add(skill.source_fact_id)

    run.check_language()
    return run.report()


# --- Cover letter ---------------------------------------------------------------------------------


def validate_letter(output: AICoverLetter, context: GenerationContext) -> ValidationReport:
    run = _Run(context)
    paragraphs = output.paragraphs

    if output.language != context.language:
        run.add(
            "WRONG_LANGUAGE",
            f"The letter is declared as '{output.language}' but the application language is "
            f"'{context.language}'.",
            "language",
            output.language,
        )

    roles = [p.role for p in paragraphs]
    if not paragraphs or roles[0] != "opening" or roles.count("opening") != 1:
        run.add(
            "INVALID_STRUCTURE", "A letter starts with exactly one opening paragraph.", "paragraphs"
        )
    if not paragraphs or roles[-1] != "closing" or roles.count("closing") != 1:
        run.add(
            "INVALID_STRUCTURE", "A letter ends with exactly one closing paragraph.", "paragraphs"
        )
    if "experience" not in roles:
        run.add(
            "INVALID_STRUCTURE",
            "A letter needs a paragraph about the candidate's experience.",
            "paragraphs",
        )

    for i, paragraph in enumerate(paragraphs):
        location = f"paragraphs[{i}]"
        needs_facts = paragraph.role in ("experience", "skills", "motivation")
        facts = run.cited(location, paragraph.source_fact_ids, required=needs_facts)
        if paragraph.role in ("experience", "skills") and not any(
            fact.kind != "application" for fact in facts
        ):
            run.add(
                "MISSING_SOURCE",
                "This paragraph makes claims about the candidate but cites no resume fact.",
                location,
            )
        for job_fact_id in dict.fromkeys(paragraph.job_fact_ids):
            if job_fact_id not in context.job_fact_ids:
                run.add(
                    "UNKNOWN_FACT_ID",
                    "The paragraph refers to a job fact that does not exist.",
                    location,
                    job_fact_id,
                )
        run.check_text(location, paragraph.text, facts, letter=True)

    run.check_language()
    return run.report()
