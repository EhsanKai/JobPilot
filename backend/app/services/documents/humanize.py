"""Humanize: reword a validated Anschreiben for a natural tone (V0.9).

    existing letter version  ->  Humanize AI  ->  structured output
         ->  truth validation  ->  a NEW validated version (the original untouched)

Humanize is **not a validator bypass**. The reworded letter goes through the same
claim checks as a freshly generated one (`documents.validation.ValidationRun`: every
number, skill, language, tool, organization, role / credential / achievement
claim, company-knowledge claim and any pay talk must be supported by a cited
fact), plus the rules of rewording:

* the same paragraphs, roles and order -- nothing merged, split, added or dropped;
* it may cite only facts the original paragraph cited -- no new facts;
* nothing the original claimed disappears: its numbers, known skills / tools /
  languages and organizations are still there, and the position and company are
  still named where they were;
* the language is unchanged and the output must differ from the input.

A failure rejects the whole result: the existing letter is untouched and nothing is
repaired. The server -- not the AI -- carries over everything that is not
prose: sender, recipient, subject, salutation, the salary sentence, the closing
formula, the signature and each paragraph's source fact ids.

A letter the user edited is humanized too: the user's own words may stay (they
are their own responsibility, and are support for what they say); the AI may
not add anything to them either.
"""

import json
from dataclasses import dataclass

from app.schemas.document_ai import AIHumanizedLetter
from app.schemas.document_content import (
    CoverLetterContent,
    LetterParagraph,
    SourceFact,
    ValidationReport,
)
from app.services.documents import claims
from app.services.documents.context import mask_variants
from app.services.documents.facts import Fact, FactCatalog
from app.services.documents.validation import ValidationRun


@dataclass(frozen=True)
class HumanizeContext:
    """A `ClaimContext` built from a stored letter: its language, the facts it
    cited (as stored with it, so a resume edited since changes nothing) and
    the job's own names."""

    language: str
    catalog: FactCatalog
    job_fact_ids: frozenset[str]
    title_variants: tuple[str, ...]
    name_variants: tuple[str, ...]
    company_name: str
    title: str


def build_context(
    content: CoverLetterContent, source_facts: dict[str, SourceFact]
) -> HumanizeContext:
    catalog = FactCatalog()
    for fact_id, fact in source_facts.items():
        kind = "application" if fact_id.startswith("app.") else "resume"
        catalog.add(Fact(fact_id, fact.text, kind, ref=fact.ref))
    title = content.recipient.job_title or ""
    company = content.recipient.company_name or ""
    job_ids = {fact_id for p in content.paragraphs for fact_id in p.job_fact_ids}
    return HumanizeContext(
        language=content.language,
        catalog=catalog,
        job_fact_ids=frozenset(job_ids),
        title_variants=tuple(mask_variants(title)),
        name_variants=tuple(mask_variants(company)),
        company_name=company,
        title=title,
    )


def payload(content: CoverLetterContent, source_facts: dict[str, SourceFact]) -> dict[str, object]:
    """What the AI is shown: the letter body and the facts it cites. Not the
    salary sentence, the sender, the resume or anything else."""
    cited = [fact_id for p in content.paragraphs for fact_id in p.source_fact_ids]
    return {
        "context_version": "humanize_context_v1",
        "task": "humanize_cover_letter",
        "language": content.language,
        "target_job": {
            "title": content.recipient.job_title,
            "company": content.recipient.company_name,
        },
        "facts": [
            {"id": fact_id, "text": source_facts[fact_id].text}
            for fact_id in dict.fromkeys(cited)
            if fact_id in source_facts
        ],
        "paragraphs": [
            {"role": p.role, "text": p.text, "source_fact_ids": p.source_fact_ids}
            for p in content.paragraphs
        ],
    }


def user_prompt(data: dict[str, object]) -> str:
    return (
        "Reword the cover letter paragraphs between the <humanize_context> tags so they sound "
        "natural, without adding or removing any fact.\n\n<humanize_context>\n"
        f"{json.dumps(data, ensure_ascii=False, indent=2)}\n</humanize_context>"
    )


def _mentions(text: str, names: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(name.lower() in lowered for name in names if name.strip())


def validate(
    output: AIHumanizedLetter, content: CoverLetterContent, context: HumanizeContext
) -> ValidationReport:
    run = ValidationRun(context)
    base = content.paragraphs

    if output.language != content.language:
        run.add(
            "WRONG_LANGUAGE",
            f"The letter is declared as '{output.language}' but was written in "
            f"'{content.language}'.",
            "language",
            output.language,
        )
    if [p.role for p in output.paragraphs] != [p.role for p in base]:
        run.add(
            "INVALID_STRUCTURE",
            "The reworded letter must keep the same paragraphs, in the same order.",
            "paragraphs",
        )
        return run.report()

    for i, (original, new) in enumerate(zip(base, output.paragraphs, strict=True)):
        location = f"paragraphs[{i}]"
        allowed = set(original.source_fact_ids)
        ids = list(dict.fromkeys(new.source_fact_ids))
        for fact_id in ids:
            if fact_id not in allowed:
                run.add(
                    "FOREIGN_FACT",
                    "The reworded paragraph cites a fact the original did not.",
                    location,
                    fact_id,
                )
        if allowed and not ids:
            run.add(
                "MISSING_SOURCE",
                "The reworded paragraph does not say which facts it is based on.",
                location,
            )
        facts = run.cited(
            location, [fact_id for fact_id in ids if fact_id in allowed], required=False
        )
        if original.origin == "user":
            # The user's own words are theirs: they may stay, and support themselves.
            facts = [*facts, Fact(f"user.p{i}", original.text, "user_text")]
        run.check_text(location, new.text, facts, letter=True)
        _check_preserved(run, location, original, new.text, context)

    if all(
        " ".join(new.text.split()) == " ".join(old.text.split())
        for new, old in zip(output.paragraphs, base, strict=True)
    ):
        run.add("UNCHANGED", "The AI returned the letter without rewording anything.", "paragraphs")
    run.check_language()
    return run.report()


def _check_preserved(
    run: ValidationRun,
    location: str,
    original: LetterParagraph,
    new_text: str,
    context: HumanizeContext,
) -> None:
    """Nothing the original paragraph claimed may disappear."""
    new_numbers: set[str] = set()
    for forms in claims.number_forms(new_text).values():
        new_numbers |= forms
    for written, forms in claims.number_forms(original.text).items():
        if forms.isdisjoint(new_numbers):
            run.add(
                "FACT_DROPPED",
                "A number or date of the original is missing from the reworded paragraph.",
                location,
                written,
            )
    new_terms = set(claims.term_keys(new_text))
    for key, alias in claims.term_keys(original.text).items():
        if key not in new_terms:
            run.add(
                "FACT_DROPPED",
                "A skill, language or degree of the original is missing from the reworded "
                "paragraph.",
                location,
                alias,
            )
    for token in claims.technology_tokens(original.text):
        if token.lower() not in new_text.lower():
            run.add(
                "FACT_DROPPED",
                "A tool or technology named in the original is missing from the reworded "
                "paragraph.",
                location,
                token,
            )
    for organization in claims.organizations(original.text):
        if organization.lower() not in new_text.lower():
            run.add(
                "FACT_DROPPED",
                "An organization named in the original is missing from the reworded paragraph.",
                location,
                organization,
            )
    for names, label in ((context.title_variants, "position"), (context.name_variants, "company")):
        if _mentions(original.text, names) and not _mentions(new_text, names):
            run.add(
                "IDENTITY_NOT_PRESERVED",
                f"The original names the {label} this letter is for; the reworded paragraph "
                "does not.",
                location,
            )


def assemble(output: AIHumanizedLetter, content: CoverLetterContent) -> CoverLetterContent:
    """The new letter: the AI's wording, everything else carried over from the
    original by the server."""
    paragraphs = [
        LetterParagraph(
            role=original.role,
            text=new.text,
            source_fact_ids=list(original.source_fact_ids),
            job_fact_ids=list(original.job_fact_ids),
            origin="ai",
        )
        for original, new in zip(content.paragraphs, output.paragraphs, strict=True)
    ]
    return content.model_copy(update={"paragraphs": paragraphs})
