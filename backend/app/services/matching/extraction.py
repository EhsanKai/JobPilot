"""Deterministic job requirement extraction (V0.6): canonical `Job` ->
`JobFacts` + requirements.

Input is the canonical job only (title, description, requirements,
responsibilities, technologies, locations, salary, employment type, work
model, seniority, industry) -- never a source's raw payload, so the result
does not depend on Greenhouse, Lever or any other source's schema. No AI,
no network: the same job content always gives the same result.

What is extracted, and how explicitly it must be stated:

- **Sections.** The canonical description keeps headings as short lines and
  list items as "• " lines. A heading decides what follows it:
  "Must have" / "Minimum qualifications" / "Required" -> REQUIRED;
  "Nice to have" / "Bonus" / "Preferred qualifications" -> PREFERRED; any
  other requirements heading ("Qualifications", "Your profile", "What you
  bring") -> UNSPECIFIED -- the posting asks for it without saying how
  strictly; responsibilities and everything else -> CONTEXTUAL.
- **Markers** inside a requirement line override the heading: "(preferred)",
  "is a plus", "ideally", "wünschenswert" -> PREFERRED; "required", "must",
  "at least", "mindestens" -> REQUIRED. Nothing else is ever inferred.
- **Skills** are V0.5.1 skill-taxonomy terms found in that text
  (`terms.TermFinder`). "A or B" lists are alternatives (any one
  satisfies). Ambiguous acronyms ("GCP") are kept as UNKNOWN requirements
  (`skill_key` NULL) -- shown, never scored.
- **Experience** needs a number of years *and* the word experience
  ("5+ years of experience", "mindestens 3 Jahre Berufserfahrung").
- **Languages** need a language name *and* a language cue ("German (B2)",
  "fluent English", "Deutschkenntnisse"). A level is only taken from an
  explicit CEFR level or "native" wording; "fluent" stays unknown
  (the V0.5.1 decision).
- **Education** needs a degree term *and* an education cue, so "Scrum
  Master" is not a master's degree. "... or equivalent experience" is kept
  (`level = "or_equivalent"`).
- **Facts** (seniority, employment type, work model, locations, salary,
  industry, title) come from the canonical fields V0.4 already normalized;
  travel only from explicit phrases ("up to 50% travel").

Storage conventions on `job_requirements` (documented in
docs/job-matching.md): SKILL rows in an alternatives group carry
`value_key = "any_of:<n>"`; EDUCATION rows carry the level key in
`value_key` and "or_equivalent" in `level` when an alternative is accepted;
LANGUAGE rows carry the ISO code in `value_key` and the CEFR level / "native"
in `level` (NULL when not stated); EXPERIENCE rows carry `min_years`.
"""

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from app.models.job import Job, SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference, TravelWillingness
from app.models.job_requirement import ExtractionConfidence, RequirementCategory, RequirementKind
from app.models.professional_profile import SeniorityLevel
from app.schemas.document_types import EVIDENCE_MAX_CHARS
from app.schemas.job_facts import JobFacts
from app.services.job_discovery.normalization.text import fold
from app.services.job_discovery.sources.html import classify_heading
from app.services.matching.terms import TermFinder, TermHit
from app.services.taxonomy import (
    EDUCATION_LEVEL_VOCABULARY,
    LANGUAGES,
    SKILLS,
    TAXONOMY_VERSION,
    normalize_location,
    normalize_skill,
)
from app.services.taxonomy.education import EDUCATION_LEVELS

#: Bump whenever extraction rules change: stored with every
#: `job_intelligence` row, and part of match staleness.
JOB_EXTRACTOR_VERSION = "job_extractor_v1"

Section = Literal["required", "preferred", "requirements", "responsibilities", "other", "title"]

_SKILLS = TermFinder(SKILLS)
# Two-letter ISO codes ("de", "en", "da", "el") are ordinary words in prose.
_LANGUAGES = TermFinder(LANGUAGES, min_alias_length=3)
_EDUCATION = TermFinder(EDUCATION_LEVEL_VOCABULARY)

# --- Section and marker vocabularies (English + German, folded) -----------------

_PREFERRED_HEADING = re.compile(
    r"\b(nice to have|nice to haves|good to have|bonus|preferred|plus|desirable|ideally|"
    r"wunschenswert|von vorteil|pluspunkt\w*|optional)\b"
)
_REQUIRED_HEADING = re.compile(
    r"\b(must have|must haves|required|minimum|mandatory|essential|basic qualifications|"
    r"voraussetzung\w*|zwingend|erforderlich)\b"
)
_PREFERRED_MARKER = re.compile(
    r"\b(nice to have|nice to haves|good to have|is a plus|are a plus|a plus|big plus|"
    r"strong plus|would be a plus|plus point\w*|bonus|preferred|preferably|ideally|desirable|"
    r"advantageous|an advantage|beneficial|optional|wunschenswert|von vorteil|idealerweise|"
    r"ware ein plus|ist ein plus|pluspunkt\w*)\b"
)
_REQUIRED_MARKER = re.compile(
    r"\b(required|requirement|must|must have|mandatory|essential|necessary|at least|minimum|"
    r"erforderlich|zwingend|vorausgesetzt|voraussetzung|unabdingbar|notwendig|mindestens)\b"
)
# A whole line that is a heading on its own (no colon, no list after it).
_STANDALONE_HEADING = re.compile(
    r"^(?:nice to haves?|good to have|bonus(?: points)?|preferred(?: qualifications| skills| "
    r"experience)?|desirable|plus|wunschenswert|von vorteil|must haves?|required(?: "
    r"qualifications| skills)?|minimum qualifications|basic qualifications|requirements|"
    r"qualifications|your profile|about you|what you bring|what you ll bring|who you are|"
    r"what we re looking for|what we are looking for|dein profil|ihr profil|anforderungen|"
    r"responsibilities|your responsibilities|your tasks|what you ll do|what you will do|"
    r"the role|aufgaben|deine aufgaben|ihre aufgaben|das bringst du mit|das bringen sie mit)$"
)
_ALTERNATIVES = re.compile(r"\b(or|oder|either|and or)\b")
# Outside requirement sections, only text describing what the candidate
# *has* is a requirement ("You have 5+ years of experience"), not what they
# will do ("You will use Kafka").
_CANDIDATE_CUE = re.compile(
    r"\b(you have|you bring|you are|you ve|you possess|you should have|you need|"
    r"your experience|your background|your profile|we re looking for|we are looking for|"
    r"ideal candidate|du hast|du bringst|du verfugst|sie haben|sie bringen|sie verfugen)\b"
)
_LANGUAGE_CUE = re.compile(
    r"\b(speak\w*|spoken|written|language\w*|fluen\w*|proficien\w*|skills?|kenntnisse|"
    r"sprach\w*|level|niveau|native|mother tongue|muttersprach\w*|verhandlungssicher|"
    r"business|communicat\w*|cefr|[abc][12]|wort und schrift|bilingual|required|mandatory)\b"
)
_CEFR = re.compile(r"\b([abc][12])\b")
_NATIVE = re.compile(r"\b(native|muttersprach\w*|mother tongue)\b")
_EDUCATION_CUE = re.compile(
    r"\b(degree|degrees|abschluss|hochschulabschluss|studium|graduate|graduated|graduation|"
    r"diploma|qualification|education|university|universitat|hochschule|college|related field|"
    r"computer science|engineering|informatik|of science|of arts|business administration)\b"
)
_EQUIVALENT = re.compile(
    r"\b(or equivalent|or comparable|or similar|equivalent (?:practical |work |professional )?"
    r"experience|oder vergleichbar\w*|oder eine vergleichbare)\b"
)
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "ein": 1, "eine": 1, "einem": 1, "zwei": 2, "drei": 3, "vier": 4,
    "funf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}
_NUMBER = r"(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"
_YEARS = r"(?:years?|yrs?|jahre?n?)"
_EXPERIENCE_WORD = r"(?:experience|erfahrung|berufserfahrung|work experience|praxiserfahrung)"
# "5+ years of (relevant/professional ...) experience", "3 5 years experience",
# "mindestens 3 Jahre Berufserfahrung".
_EXPERIENCE_BEFORE = re.compile(
    rf"\b{_NUMBER}\+?(?: (?:to|bis)? ?{_NUMBER}\+?)? (?:\+ )?{_YEARS}\b(?: of| an)?"
    rf"(?: [a-z0-9+#]+){{0,4}}? {_EXPERIENCE_WORD}\b"
)
# "experience of at least 3 years", "Berufserfahrung von mindestens 2 Jahren".
_EXPERIENCE_AFTER = re.compile(
    rf"\b{_EXPERIENCE_WORD}(?: [a-z0-9+#]+){{0,3}}? (?:of |von )?(?:at least |mindestens |min )?"
    rf"{_NUMBER}\+? {_YEARS}\b"
)
# Years attached to one skill: "3+ years Python", "2 years of experience with Go".
_SKILL_YEARS = re.compile(rf"\b{_NUMBER}\+?(?: (?:to|bis)? ?{_NUMBER}\+?)? {_YEARS}\b")
_MAX_YEARS = 40

# Travel: only explicit phrases, read from the lower-cased original text.
_TRAVEL_PERCENT = re.compile(
    r"travel(?:ling|ing)?\s+(?:up to\s+|of up to\s+|about\s+|approximately\s+|approx\.?\s+|"
    r"around\s+|roughly\s+)?(\d{1,3})\s?%|(\d{1,3})\s?%\s+(?:of\s+(?:the\s+)?time\s+)?"
    r"(?:travel|reisetätigkeit)|reisetätigkeit\s+(?:von\s+)?(?:bis\s+zu\s+)?(\d{1,3})\s?%"
)
_TRAVEL_FREQUENT = re.compile(
    r"\b(?:frequent|extensive|regular|significant|heavy|constant)\s+(?:business\s+)?travel"
    r"|\bhohe\s+reisebereitschaft|\bhäufige\s+reisetätigkeit"
)
_TRAVEL_OCCASIONAL = re.compile(
    r"\b(?:occasional|some|limited|light|infrequent|minimal)\s+(?:business\s+)?travel"
    r"|\bgelegentliche\s+reise\w*"
)
_TRAVEL_NONE = re.compile(r"\bno\s+travel(?:\s+is)?\s+required\b|\bkeine\s+reisetätigkeit")
_FREQUENT_TRAVEL_PERCENT = 30

_HEADING_MAX_CHARS = 80
_BULLET = "• "


# --- Input ------------------------------------------------------------------------


@dataclass(frozen=True)
class JobContent:
    """The canonical job fields the extractor reads -- nothing else."""

    title: str
    description: str | None = None
    requirements: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()
    technologies: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = None
    salary_period: SalaryPeriod | None = None
    employment_type: EmploymentTypeValue | None = None
    work_model: RemotePreference | None = None
    seniority: SeniorityLevel | None = None
    industry: str | None = None

    @classmethod
    def from_job(cls, job: Job) -> "JobContent":
        locations = list(job.locations or [])
        if job.location and job.location not in locations:
            locations.insert(0, job.location)
        return cls(
            title=job.title,
            description=job.description,
            requirements=tuple(job.requirements or ()),
            responsibilities=tuple(job.responsibilities or ()),
            technologies=tuple(job.technologies or ()),
            locations=tuple(locations),
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            employment_type=job.employment_type,
            work_model=job.work_model,
            seniority=job.seniority,
            industry=job.industry,
        )

    def input_hash(self) -> str:
        """SHA-256 of exactly what the extractor reads (`job_intelligence.input_hash`)."""

        def scalar(value: object) -> object:
            if isinstance(value, Decimal):
                return str(value.normalize())
            return getattr(value, "value", value)

        payload = {name: scalar(value) for name, value in self.__dict__.items()}
        return _sha256(payload)


# --- Output -----------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedRequirement:
    kind: RequirementKind
    category: RequirementCategory
    source_field: str
    extraction_method: str
    confidence: ExtractionConfidence
    skill_key: str | None = None
    value_key: str | None = None
    value_text: str | None = None
    level: str | None = None
    min_years: Decimal | None = None
    section_kind: str | None = None
    evidence_text: str | None = None
    evidence_offset: int | None = None

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "category": self.category.value,
            "skill_key": self.skill_key,
            "value_key": self.value_key,
            "value_text": self.value_text,
            "level": self.level,
            "min_years": str(self.min_years) if self.min_years is not None else None,
            "source_field": self.source_field,
            "section_kind": self.section_kind,
            "extraction_method": self.extraction_method,
            "confidence": self.confidence.value,
            "evidence_text": self.evidence_text,
            "evidence_offset": self.evidence_offset,
        }


@dataclass(frozen=True)
class Extraction:
    facts: JobFacts
    requirements: tuple[ExtractedRequirement, ...]
    extractor_version: str = JOB_EXTRACTOR_VERSION
    taxonomy_version: str = TAXONOMY_VERSION

    def result_hash(self) -> str:
        """SHA-256 of the facts and requirements (`job_intelligence.result_hash`)."""
        return _sha256(
            {
                "facts": self.facts.model_dump(mode="json"),
                "requirements": [r.canonical() for r in self.requirements],
            }
        )


def _sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


# --- Passages ---------------------------------------------------------------------


@dataclass(frozen=True)
class Passage:
    """One piece of posting text and the section it belongs to."""

    text: str
    source_field: str
    section: Section
    offset: int | None = None

    @property
    def is_requirement(self) -> bool:
        return self.section in ("required", "preferred", "requirements", "title")


def classify_section_heading(heading: str) -> Section | None:
    """What a heading line introduces, or None if it isn't a known heading."""
    folded = fold(heading)
    if not folded:
        return None
    if _PREFERRED_HEADING.search(folded):
        return "preferred"
    if _REQUIRED_HEADING.search(folded):
        return "required"
    kind = classify_heading(heading)
    if kind == "requirements":
        return "requirements"
    if kind == "responsibilities":
        return "responsibilities"
    return None


def _is_heading(line: str, next_line: str | None) -> bool:
    """A short line ending with ":", a short line introducing a list, or a
    line that is nothing but a known heading ("Nice to have")."""
    text = line.strip()
    if not text or text.startswith(_BULLET) or len(text) > _HEADING_MAX_CHARS:
        return False
    if text.endswith(":"):
        return True
    if text[-1] in ".!?":
        return False
    return (next_line or "").startswith(_BULLET) or bool(_STANDALONE_HEADING.match(fold(text)))


def description_passages(description: str | None) -> list[Passage]:
    """Split the canonical description into passages under their headings."""
    if not description:
        return []
    lines: list[tuple[int, str]] = []
    offset = 0
    for raw in description.split("\n"):
        lines.append((offset, raw))
        offset += len(raw) + 1
    content = [(o, line) for o, line in lines if line.strip()]
    passages: list[Passage] = []
    section: Section = "other"
    for index, (start, line) in enumerate(content):
        next_line = content[index + 1][1] if index + 1 < len(content) else None
        text = line.strip()
        heading, colon, rest = text.partition(":")
        if _is_heading(text, next_line):
            section = classify_section_heading(heading) or "other"
            continue
        if colon and rest.strip() and len(heading) <= 40:
            # "Must have: Python, SQL" -- an inline heading.
            inline = classify_section_heading(heading)
            if inline is not None:
                body = rest.strip()
                passages.append(Passage(body, "description", inline, start + line.index(body)))
                continue
        body = text[len(_BULLET) :] if text.startswith(_BULLET) else text
        passages.append(Passage(body, "description", section, start + line.index(body)))
    return passages


def job_passages(content: JobContent) -> list[Passage]:
    """Every passage the extractor reads, description first (it carries the
    headings), then list items the description didn't contain."""
    passages = [Passage(content.title, "title", "title")]
    from_description = description_passages(content.description)
    passages.extend(from_description)
    seen = {fold(p.text) for p in from_description}
    for item in content.requirements:
        if fold(item) not in seen:
            seen.add(fold(item))
            passages.append(Passage(item, "requirements", "requirements"))
    for item in content.responsibilities:
        if fold(item) not in seen:
            seen.add(fold(item))
            passages.append(Passage(item, "responsibilities", "responsibilities"))
    return passages


# --- Importance -------------------------------------------------------------------

_SECTION_KIND: dict[Section, RequirementKind] = {
    "required": RequirementKind.REQUIRED,
    "preferred": RequirementKind.PREFERRED,
    "requirements": RequirementKind.UNSPECIFIED,
    "title": RequirementKind.UNSPECIFIED,
    "responsibilities": RequirementKind.CONTEXTUAL,
    "other": RequirementKind.CONTEXTUAL,
}
#: Strongest first: when one thing is stated several times, the most
#: explicit statement wins.
KIND_STRENGTH: dict[RequirementKind, int] = {
    RequirementKind.REQUIRED: 4,
    RequirementKind.UNSPECIFIED: 3,
    RequirementKind.PREFERRED: 2,
    RequirementKind.CONTEXTUAL: 1,
}


@dataclass(frozen=True)
class Clause:
    """A clause of a passage, with the importance its words give it."""

    text: str
    passage: Passage
    kind: RequirementKind


def _marker_kind(folded: str) -> RequirementKind | None:
    preferred = bool(_PREFERRED_MARKER.search(folded))
    required = bool(_REQUIRED_MARKER.search(folded))
    if preferred and not required:
        return RequirementKind.PREFERRED
    if required and not preferred:
        return RequirementKind.REQUIRED
    return None


def clauses(passage: Passage) -> Iterator[Clause]:
    """Clauses of a passage. Requirement passages get the importance of
    their markers, else of their section; other passages stay CONTEXTUAL --
    unless the clause explicitly addresses the candidate ("you have 5+
    years of experience"), which makes it UNSPECIFIED."""
    base = _SECTION_KIND[passage.section]
    for sentence in re.split(r";|(?<=[.!?])\s+", passage.text):
        if not sentence.strip():
            continue
        folded = fold(sentence)
        has_both = bool(_PREFERRED_MARKER.search(folded) and _REQUIRED_MARKER.search(folded))
        parts = sentence.split(",") if has_both else [sentence]
        for part in parts:
            if not part.strip():
                continue
            folded_part = fold(part)
            if passage.is_requirement:
                kind = _marker_kind(folded_part) or base
            elif passage.section == "other" and _CANDIDATE_CUE.search(folded_part):
                kind = _marker_kind(folded_part) or RequirementKind.UNSPECIFIED
            else:
                kind = base
            yield Clause(part.strip(), passage, kind)


# --- Extraction -------------------------------------------------------------------


@dataclass
class _Found:
    """A requirement being collected (the strongest statement wins)."""

    requirement: ExtractedRequirement
    groups: set[str] = field(default_factory=set)
    standalone: bool = False


def _evidence(clause: Clause) -> tuple[str, int | None]:
    passage = clause.passage
    text = passage.text[:EVIDENCE_MAX_CHARS]
    offset = passage.offset if passage.source_field == "description" else None
    return text, offset


def _requirement(
    clause: Clause,
    category: RequirementCategory,
    method: str,
    confidence: ExtractionConfidence,
    **values: Any,
) -> ExtractedRequirement:
    text, offset = _evidence(clause)
    return ExtractedRequirement(
        kind=clause.kind,
        category=category,
        source_field=clause.passage.source_field,
        section_kind=clause.passage.section,
        extraction_method=method,
        confidence=confidence,
        evidence_text=text,
        evidence_offset=offset,
        **values,
    )


def _number(value: str) -> int:
    return int(value) if value.isdigit() else _NUMBER_WORDS[value]


def _years_in(folded: str, pattern: re.Pattern[str]) -> list[int]:
    found = []
    for match in pattern.finditer(folded):
        numbers = [g for g in match.groups() if g]
        if numbers:
            years = _number(numbers[0])
            if 0 < years <= _MAX_YEARS:
                found.append(years)
    return found


def _level_tokens(window: str) -> list[tuple[int, int, str]]:
    found = [(m.start(), m.end(), m.group(1).upper()) for m in _CEFR.finditer(window)]
    found += [(m.start(), m.end(), "native") for m in _NATIVE.finditer(window)]
    return sorted(found)


def language_levels(folded: str, hits: list[TermHit]) -> list[str | None]:
    """The explicit level stated for each language hit: the nearest CEFR
    level or "native" wording after it (up to the next language), else the
    closest one just before it -- never one the previous language already
    claimed ("German (B2) and fluent English": English has no level). None
    when no explicit level is stated."""
    levels: list[str | None] = []
    claimed_until = 0
    for index, hit in enumerate(hits):
        after_end = hits[index + 1].start if index + 1 < len(hits) else len(folded)
        after_end = min(after_end, hit.end + 40)
        after = _level_tokens(folded[hit.end : after_end])
        if after:
            start, end, level = after[0]
            claimed_until = hit.end + end
            levels.append(level)
            continue
        before_start = max(claimed_until, hits[index - 1].end if index else 0, hit.start - 25)
        before = _level_tokens(folded[before_start : hit.start])
        if before:
            claimed_until = hit.start
            levels.append(before[-1][2])
        else:
            levels.append(None)
    return levels


def extract(content: JobContent) -> Extraction:
    """Extract facts and requirements from canonical job content."""
    skills: dict[str, _Found] = {}
    unknown_skills: dict[str, _Found] = {}
    languages: dict[tuple[str, RequirementKind], _Found] = {}
    education: dict[tuple[str, RequirementKind], _Found] = {}
    experience: dict[tuple[Decimal, RequirementKind], ExtractedRequirement] = {}
    group_count = 0

    def keep(store: dict[Any, _Found], key: Any, requirement: ExtractedRequirement) -> _Found:
        found = store.get(key)
        if found is None:
            found = store[key] = _Found(requirement)
        elif KIND_STRENGTH[requirement.kind] > KIND_STRENGTH[found.requirement.kind]:
            found.requirement = requirement
        return found

    for passage in job_passages(content):
        for clause in clauses(passage):
            folded = fold(clause.text)
            skill_hits = _SKILLS.find(clause.text)
            known = [h for h in skill_hits if h.key is not None]

            # Skills (and alternatives).
            distinct = list(dict.fromkeys(h.key for h in known if h.key))
            group = None
            if len(distinct) >= 2 and _ALTERNATIVES.search(folded):
                group_count += 1
                group = f"any_of:{group_count}"
            skill_years = _years_in(folded, _SKILL_YEARS) if len(distinct) == 1 else []
            for key in distinct:
                requirement = _requirement(
                    clause,
                    RequirementCategory.SKILL,
                    "skill_taxonomy",
                    ExtractionConfidence.HIGH,
                    skill_key=key,
                    value_text=SKILLS.entry(key).label,
                    min_years=Decimal(max(skill_years)) if skill_years else None,
                )
                found = keep(skills, key, requirement)
                if group is None:
                    found.standalone = True
                else:
                    found.groups.add(group)
            for hit in skill_hits:
                if hit.key is None:
                    keep(
                        unknown_skills,
                        hit.alias,
                        _requirement(
                            clause,
                            RequirementCategory.SKILL,
                            "skill_taxonomy_ambiguous",
                            ExtractionConfidence.LOW,
                            value_text=hit.alias.upper(),
                        ),
                    ).standalone = True

            if clause.kind is RequirementKind.CONTEXTUAL:
                continue

            # Experience.
            years = _years_in(folded, _EXPERIENCE_BEFORE) + _years_in(folded, _EXPERIENCE_AFTER)
            if years:
                value = Decimal(max(years))
                experience.setdefault(
                    (value, clause.kind),
                    _requirement(
                        clause,
                        RequirementCategory.EXPERIENCE,
                        "years_pattern",
                        ExtractionConfidence.HIGH,
                        min_years=value,
                    ),
                )

            # Languages.
            language_hits = [h for h in _LANGUAGES.find(clause.text) if h.key]
            if language_hits and (
                _LANGUAGE_CUE.search(folded)
                or any(h.alias.endswith("kenntnisse") for h in language_hits)
            ):
                levels = language_levels(folded, language_hits)
                for hit, level in zip(language_hits, levels, strict=True):
                    assert hit.key is not None
                    keep(
                        languages,
                        (hit.key, clause.kind),
                        _requirement(
                            clause,
                            RequirementCategory.LANGUAGE,
                            "language_taxonomy",
                            ExtractionConfidence.HIGH if level else ExtractionConfidence.MEDIUM,
                            value_key=hit.key,
                            value_text=LANGUAGES.entry(hit.key).label,
                            level=level,
                        ),
                    )

            # Education: the lowest level the clause accepts.
            education_hits = [h for h in _EDUCATION.find(clause.text) if h.key]
            if education_hits and _EDUCATION_CUE.search(folded):
                level_key = min(
                    (h.key for h in education_hits if h.key),
                    key=EDUCATION_LEVELS.index,
                )
                keep(
                    education,
                    (level_key, clause.kind),
                    _requirement(
                        clause,
                        RequirementCategory.EDUCATION,
                        "education_taxonomy",
                        ExtractionConfidence.HIGH,
                        value_key=level_key,
                        value_text=EDUCATION_LEVEL_VOCABULARY.entry(level_key).label,
                        level="or_equivalent" if _EQUIVALENT.search(folded) else None,
                    ),
                )

    # V0.4's technology list: anything it found that the text scan didn't
    # is kept as context (never scored).
    for name in content.technologies:
        result = normalize_skill(name)
        if result is not None and result.is_match and result.key and result.key not in skills:
            skills[result.key] = _Found(
                ExtractedRequirement(
                    kind=RequirementKind.CONTEXTUAL,
                    category=RequirementCategory.SKILL,
                    source_field="technologies",
                    extraction_method="v04_technology_vocabulary",
                    confidence=ExtractionConfidence.MEDIUM,
                    skill_key=result.key,
                    value_text=SKILLS.entry(result.key).label,
                    evidence_text=name[:EVIDENCE_MAX_CHARS],
                ),
                standalone=True,
            )

    requirements: list[ExtractedRequirement] = []
    for found in skills.values():
        requirement = found.requirement
        # A skill also asked for on its own is not an alternative.
        if found.groups and not found.standalone:
            requirement = _with(requirement, value_key=sorted(found.groups)[0])
        requirements.append(requirement)
    requirements.extend(f.requirement for f in unknown_skills.values())
    requirements.extend(experience.values())
    requirements.extend(f.requirement for f in languages.values())
    requirements.extend(f.requirement for f in education.values())
    requirements.sort(key=_sort_key)
    return Extraction(facts=_facts(content, requirements), requirements=tuple(requirements))


def _with(requirement: ExtractedRequirement, **changes: Any) -> ExtractedRequirement:
    values = {**requirement.__dict__, **changes}
    return ExtractedRequirement(**values)


def _sort_key(requirement: ExtractedRequirement) -> tuple[str, int, str, str]:
    return (
        requirement.category.value,
        -KIND_STRENGTH[requirement.kind],
        requirement.skill_key or requirement.value_key or "",
        requirement.value_text or "",
    )


# --- Facts ------------------------------------------------------------------------


def _fact(value: Any, source_field: str, method: str, confidence: str, **extra: Any) -> dict:
    return {
        "value": value,
        "source_field": source_field,
        "extraction_method": method,
        "confidence": confidence,
        **extra,
    }


def location_value(text: str) -> dict[str, Any]:
    """A `LocationValue` for one location string, via the V0.5.1 taxonomy."""
    value: dict[str, Any] = {"text": text[:500]}
    result = normalize_location(text)
    if result is not None and result.is_match and result.key:
        if result.category == "city":
            value["city"] = result.label
        country = result.detail("country") or (
            result.key.split(":")[1].upper() if result.category == "country" else None
        )
        if country:
            value["country"] = country
        value["is_remote"] = result.key == "remote" or result.detail("remote") == "true"
    elif result is not None:
        value["is_remote"] = result.detail("remote") == "true"
    return value


def _travel(content: JobContent) -> dict[str, Any] | None:
    for passage in job_passages(content):
        text = passage.text.lower()
        value: TravelWillingness | None = None
        match = _TRAVEL_PERCENT.search(text)
        if match:
            percent = int(next(g for g in match.groups() if g))
            value = (
                TravelWillingness.NONE
                if percent == 0
                else TravelWillingness.FREQUENT
                if percent >= _FREQUENT_TRAVEL_PERCENT
                else TravelWillingness.OCCASIONAL
            )
        elif match := _TRAVEL_FREQUENT.search(text):
            value = TravelWillingness.FREQUENT
        elif match := _TRAVEL_NONE.search(text):
            value = TravelWillingness.NONE
        elif match := _TRAVEL_OCCASIONAL.search(text):
            value = TravelWillingness.OCCASIONAL
        if value is not None:
            return _fact(
                value.value,
                passage.source_field,
                "travel_phrases",
                "medium",
                evidence_text=passage.text[:EVIDENCE_MAX_CHARS],
            )
    return None


def _facts(content: JobContent, requirements: list[ExtractedRequirement]) -> JobFacts:
    facts: dict[str, Any] = {
        "title": _fact(content.title[:500], "title", "canonical_field", "high"),
    }
    if content.seniority is not None:
        facts["seniority"] = _fact(
            content.seniority.value,
            "title",
            "v04_title_vocabulary",
            "medium",
            evidence_text=content.title[:EVIDENCE_MAX_CHARS],
        )
    if content.employment_type is not None:
        facts["employment_type"] = _fact(
            content.employment_type.value, "employment_type", "canonical_field", "high"
        )
    if content.work_model is not None:
        facts["work_model"] = _fact(
            content.work_model.value, "work_model", "canonical_field", "high"
        )
    locations = [location_value(text) for text in content.locations if text.strip()]
    if locations:
        facts["location"] = _fact(locations, "locations", "location_taxonomy", "high")
    if content.salary_min is not None or content.salary_max is not None:
        facts["salary"] = _fact(
            {
                "minimum": content.salary_min,
                "maximum": content.salary_max,
                "currency": content.salary_currency,
                "period": content.salary_period.value if content.salary_period else None,
            },
            "salary",
            "canonical_field",
            "high",
        )
    spoken = sorted(
        {
            r.value_key
            for r in requirements
            if r.category is RequirementCategory.LANGUAGE and r.value_key
        }
    )
    if spoken:
        facts["language"] = _fact(spoken, "description", "language_taxonomy", "medium")
    if travel := _travel(content):
        facts["travel"] = travel
    if content.industry:
        facts["industry"] = _fact(content.industry[:255], "industry", "canonical_field", "high")
    return JobFacts.model_validate(facts)
