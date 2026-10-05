"""Stage 3 of the pipeline: validate a `ParsedResume` against the source text.

Pydantic already guarantees the parser output's *shape*. This stage checks
its *truthfulness* deterministically, without any AI:

- Every evidence quote is checked against the extracted text
  (`Evidence.verified`); the parser's own claim is never trusted.
- Personal details (email, phone, URLs, ...) that don't literally appear in
  the resume are dropped -- these are the facts most damaging to get wrong.
- A summary that isn't in the resume (i.e. was written, not extracted) is
  dropped.
- Entities whose names can't be found and that have no verified evidence are
  kept for the user to review but downgraded to low confidence.
- Technologies attached to a role/project that never appear in the text are
  removed; dates whose year never appears are cleared; skill proficiency and
  language levels without supporting text are reset to unknown.
- Obvious skill spelling variants are merged deterministically.

Every change is recorded as a `ProfileWarning` so it's visible in review.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.schemas.resume_profile import (
    PERSONAL_FIELDS,
    AdditionalInfoItem,
    CertificationItem,
    Confidence,
    EducationItem,
    Evidence,
    LanguageItem,
    ParsedEvidence,
    ParsedResume,
    ParsedSkill,
    PersonalInfoItem,
    ProfileWarning,
    ProjectItem,
    ResumeProfileData,
    SectionName,
    SkillItem,
    SummaryItem,
    WorkExperienceItem,
)


class NoResumeContentError(Exception):
    code = "NO_RESUME_CONTENT"
    user_message = (
        "We couldn't find any resume information in this document. "
        "Please check that you uploaded the right file."
    )


# --- Text matching ---------------------------------------------------------

_QUOTES = str.maketrans({"’": "'", "‘": "'", "‚": "'", "“": '"', "”": '"', "„": '"'})
_DASHES = re.compile(r"[‐‑‒–—―−]")
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_ELLIPSIS = re.compile(r"\.{3,}|…")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().translate(_QUOTES)
    text = _DASHES.sub("-", text)
    return _WS.sub(" ", text).strip()


def _alnum(text: str) -> str:
    return _NON_ALNUM.sub("", _norm(text))


class SourceText:
    """Normalized views of the extracted resume text for fact checking."""

    def __init__(self, text: str) -> None:
        self.raw = text
        self.norm = _norm(text)
        self.alnum = _alnum(text)
        self.digits = re.sub(r"\D", "", text)
        self.words = set(re.findall(r"[0-9a-zà-ÿß]+", self.norm))
        self._lines = [line for line in text.split("\n") if line.strip()]

    def contains(self, fragment: str | None) -> bool:
        """Whether `fragment` appears in the text (whitespace/case/punctuation tolerant)."""
        if not fragment or not fragment.strip():
            return False
        segments = [s for s in _ELLIPSIS.split(fragment) if s.strip()]
        if len(segments) > 1:
            return all(self.contains(s) for s in segments if len(s.strip()) >= 8) and any(
                len(s.strip()) >= 8 for s in segments
            )
        normalized = _norm(fragment)
        if normalized in self.norm:
            return True
        squashed = _alnum(fragment)
        return len(squashed) >= 4 and squashed in self.alnum

    def contains_term(self, term: str | None) -> bool:
        """Whole-term match, safe for short skill names like 'C', 'R', 'Go'."""
        if not term or not term.strip():
            return False
        normalized = _norm(term)
        pattern = r"(?<![0-9a-z])" + re.escape(normalized) + r"(?![0-9a-z])"
        if re.search(pattern, self.norm):
            return True
        squashed = _alnum(term)
        # Fallback for punctuation variants ("S/4HANA" vs "S4 HANA"), only for
        # terms long enough not to match by accident.
        return len(squashed) >= 4 and squashed in self.alnum

    def word_overlap(self, text: str) -> float:
        words = [w for w in re.findall(r"[0-9a-zà-ÿß]+", _norm(text)) if len(w) >= 3]
        if not words:
            return 0.0
        return sum(1 for w in words if w in self.words) / len(words)

    def line_containing(self, fragment: str) -> str | None:
        target = _norm(fragment)
        for line in self._lines:
            if target in _norm(line):
                return line.strip()[:600]
        return None

    def has_year(self, partial_date: str | None) -> bool:
        if not partial_date:
            return True
        return re.search(r"(?<!\d)" + re.escape(partial_date[:4]) + r"(?!\d)", self.raw) is not None


# --- Skill normalization ---------------------------------------------------

# Canonical names for unambiguous spelling variants of the same skill. Keys
# are `_skill_key()` forms. Deliberately small and conservative: an entry
# here must never broaden or upgrade meaning (e.g. "sap" is NOT mapped to
# "SAP ABAP").
_SKILL_ALIASES: dict[str, str] = {
    "abap": "SAP ABAP",
    "sapabap": "SAP ABAP",
    "abapoo": "ABAP OO",
    "isu": "SAP IS-U",
    "sapisu": "SAP IS-U",
    "s4hana": "SAP S/4HANA",
    "saps4hana": "SAP S/4HANA",
    "btp": "SAP BTP",
    "sapbtp": "SAP BTP",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "golang": "Go",
    "react": "React",
    "reactjs": "React",
    "nodejs": "Node.js",
    "node": "Node.js",
    "vuejs": "Vue.js",
    "vue": "Vue.js",
    "nextjs": "Next.js",
    "py": "Python",
    "python3": "Python",
    "python": "Python",
    "mssql": "Microsoft SQL Server",
    "sqlserver": "Microsoft SQL Server",
    "microsoftsqlserver": "Microsoft SQL Server",
    "aws": "AWS",
    "amazonwebservices": "AWS",
    "gcp": "Google Cloud",
    "googlecloud": "Google Cloud",
    "googlecloudplatform": "Google Cloud",
    "azure": "Microsoft Azure",
    "microsoftazure": "Microsoft Azure",
    "ci/cd": "CI/CD",
    "cicd": "CI/CD",
}


def _skill_key(name: str) -> str:
    # Keep "+" and "#" so C, C++ and C# stay distinct.
    return re.sub(r"[\s\-_.]", "", _norm(name))


def canonical_skill_name(name: str) -> str:
    key = _skill_key(name)
    return _SKILL_ALIASES.get(key) or _SKILL_ALIASES.get(key.replace("/", "")) or name.strip()


_CONFIDENCE_RANK: dict[Confidence, int] = {"low": 0, "medium": 1, "high": 2}


def _max_confidence(a: Confidence, b: Confidence) -> Confidence:
    return a if _CONFIDENCE_RANK[a] >= _CONFIDENCE_RANK[b] else b


def _min_confidence(a: Confidence, b: Confidence) -> Confidence:
    return a if _CONFIDENCE_RANK[a] <= _CONFIDENCE_RANK[b] else b


def _dedupe_evidence(evidence: Iterable[ParsedEvidence], limit: int = 5) -> list[ParsedEvidence]:
    seen: set[str] = set()
    result: list[ParsedEvidence] = []
    for item in evidence:
        key = _norm(item.source_text)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def merge_skills(skills: list[ParsedSkill]) -> list[ParsedSkill]:
    """Merge spelling variants of the same skill into one canonical entry."""
    merged: dict[str, ParsedSkill] = {}
    order: list[str] = []
    for skill in skills:
        canonical = canonical_skill_name(skill.name)
        key = _skill_key(canonical)
        aliases = list(skill.aliases)
        if canonical != skill.name.strip():
            aliases.append(skill.name.strip())
        if key not in merged:
            merged[key] = skill.model_copy(
                update={"name": canonical, "aliases": _unique(aliases, exclude=canonical)}
            )
            order.append(key)
            continue
        existing = merged[key]
        proficiency = existing.proficiency
        proficiency_evidence = existing.proficiency_evidence
        if proficiency == "unknown" and skill.proficiency != "unknown":
            proficiency, proficiency_evidence = skill.proficiency, skill.proficiency_evidence
        merged[key] = existing.model_copy(
            update={
                "aliases": _unique(existing.aliases + aliases, exclude=canonical),
                "confidence": _max_confidence(existing.confidence, skill.confidence),
                "basis": "explicit" if "explicit" in (existing.basis, skill.basis) else "inferred",
                "category": existing.category or skill.category,
                "proficiency": proficiency,
                "proficiency_evidence": proficiency_evidence,
                "evidence": _dedupe_evidence([*existing.evidence, *skill.evidence]),
            }
        )
    return [merged[key] for key in order]


def _unique(values: Iterable[str], exclude: str | None = None) -> list[str]:
    seen = {_norm(exclude)} if exclude else set()
    result = []
    for value in values:
        key = _norm(value)
        if value.strip() and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return result


# --- Validation ------------------------------------------------------------


@dataclass
class ValidationResult:
    data: ResumeProfileData
    warnings: list[ProfileWarning] = field(default_factory=list)


class _Validator:
    def __init__(self, source: SourceText) -> None:
        self.source = source
        self.warnings: list[ProfileWarning] = []

    def warn(
        self,
        code: str,
        message: str,
        section: SectionName | None = None,
        item_id: str | None = None,
    ) -> None:
        self.warnings.append(
            ProfileWarning(code=code, message=message, section=section, item_id=item_id)
        )

    def evidence(self, items: Iterable[ParsedEvidence]) -> list[Evidence]:
        return [
            Evidence(
                source_section=item.source_section,
                source_text=item.source_text,
                verified=self.source.contains(item.source_text),
            )
            for item in items
        ]

    def ground(
        self,
        item: Any,
        section: SectionName,
        label: str,
        names: Iterable[str | None],
        *,
        term_match: bool = False,
    ) -> None:
        """Downgrade confidence of an entity the resume text doesn't support."""
        check = self.source.contains_term if term_match else self.source.contains
        name_found = any(check(name) for name in names if name)
        has_verified = any(e.verified for e in item.evidence)
        has_evidence = bool(item.evidence)

        if not name_found and not has_verified:
            item.confidence = "low"
            self.warn(
                "not_found_in_resume",
                f"{label} could not be found in the resume text. Please verify it.",
                section,
                item.id,
            )
            return
        if not name_found:
            # Supported by a real quote but named differently (e.g. a
            # canonical skill name): an inference, not a verbatim fact.
            item.basis = "inferred"
        if has_evidence and not has_verified:
            item.confidence = _min_confidence(item.confidence, "medium")
            self.warn(
                "evidence_not_verified",
                f"The quoted evidence for {label} doesn't match the resume text exactly.",
                section,
                item.id,
            )

    def dates(self, item: Any, section: SectionName, label: str, fields: Iterable[str]) -> None:
        for name in fields:
            value = getattr(item, name)
            if value and not self.source.has_year(value):
                setattr(item, name, None)
                self.warn(
                    "date_not_in_resume",
                    f"Removed {name.replace('_', ' ')} '{value}' for {label}: "
                    "that year doesn't appear in the resume.",
                    section,
                    item.id,
                )

    def technologies(self, item: Any, section: SectionName, label: str) -> None:
        kept = [t for t in item.technologies if self.source.contains_term(t)]
        removed = [t for t in item.technologies if t not in kept]
        if removed:
            item.technologies = kept
            self.warn(
                "technology_not_in_resume",
                f"Removed technologies not mentioned in the resume for {label}: "
                + ", ".join(removed),
                section,
                item.id,
            )

    # -- sections --

    def personal_information(self, parsed: ParsedResume) -> list[PersonalInfoItem]:
        items: list[PersonalInfoItem] = []
        info = parsed.personal_information
        for name in PERSONAL_FIELDS:
            value = getattr(info, name)
            if value is None:
                continue
            found = self._personal_value_found(name, value.value)
            if not found:
                self.warn(
                    "personal_field_not_in_resume",
                    f"Ignored {name.replace('_', ' ')}: it doesn't appear in the resume.",
                    "personal_information",
                )
                continue
            evidence = self.evidence([value.evidence] if value.evidence else [])
            if not any(e.verified for e in evidence):
                line = self.source.line_containing(value.value)
                if line:
                    evidence = [Evidence(source_text=line, verified=True)]
            items.append(
                PersonalInfoItem(
                    field=name,
                    value=value.value,
                    confidence=value.confidence,
                    basis="explicit",
                    evidence=evidence,
                )
            )
        return items

    def _personal_value_found(self, name: str, value: str) -> bool:
        if name == "phone":
            digits = re.sub(r"\D", "", value)
            # Compare the trailing digits so the international prefix may
            # differ ("+49 170 1234567" vs "0170 1234567").
            return len(digits) >= 6 and digits[-9:] in self.source.digits
        if name.endswith("_url"):
            stripped = re.sub(r"^(https?://)?(www\.)?", "", value.strip(), flags=re.I).rstrip("/")
            return self.source.contains(stripped)
        return self.source.contains(value)

    def summary(self, parsed: ParsedResume) -> SummaryItem | None:
        if parsed.summary is None:
            return None
        value = parsed.summary.value
        if not self.source.contains(value) and self.source.word_overlap(value) < 0.85:
            self.warn(
                "summary_not_in_resume",
                "Ignored a summary that isn't written in the resume. JobPilot never writes "
                "summaries during analysis.",
                "summary",
            )
            return None
        evidence = self.evidence([parsed.summary.evidence] if parsed.summary.evidence else [])
        if not evidence:
            evidence = [Evidence(source_section="Summary", source_text=value[:600], verified=True)]
        confidence = parsed.summary.confidence
        if not self.source.contains(value):
            confidence = _min_confidence(confidence, "medium")
        return SummaryItem(
            value=value, confidence=confidence, basis="explicit", evidence=evidence
        )

    def work_experience(self, parsed: ParsedResume) -> list[WorkExperienceItem]:
        items = []
        for exp in parsed.work_experience:
            item = WorkExperienceItem(
                **exp.model_dump(exclude={"evidence"}), evidence=self.evidence(exp.evidence)
            )
            label = " at ".join(filter(None, [item.job_title, item.company])) or "a role"
            self.ground(item, "work_experience", label, [item.company, item.job_title])
            self.dates(item, "work_experience", label, ["start_date", "end_date"])
            self.technologies(item, "work_experience", label)
            if item.is_current and item.end_date:
                item.is_current = None
                self.warn(
                    "inconsistent_dates",
                    f"{label} is marked current but has an end date; please check.",
                    "work_experience",
                    item.id,
                )
            items.append(item)
        return items

    def education(self, parsed: ParsedResume) -> list[EducationItem]:
        items = []
        for edu in parsed.education:
            item = EducationItem(
                **edu.model_dump(exclude={"evidence"}), evidence=self.evidence(edu.evidence)
            )
            label = item.degree or item.institution or "an education entry"
            self.ground(
                item, "education", label, [item.institution, item.degree, item.field_of_study]
            )
            self.dates(item, "education", label, ["start_date", "end_date"])
            items.append(item)
        return items

    def skills(self, parsed: ParsedResume) -> list[SkillItem]:
        items = []
        for skill in merge_skills(parsed.skills):
            item = SkillItem(
                **skill.model_dump(exclude={"evidence", "proficiency_evidence"}),
                evidence=self.evidence(skill.evidence),
                proficiency_evidence=(
                    self.evidence([skill.proficiency_evidence])[0]
                    if skill.proficiency_evidence
                    else None
                ),
            )
            self.ground(
                item, "skills", f"Skill '{item.name}'", [item.name, *item.aliases], term_match=True
            )
            if item.proficiency != "unknown" and not (
                item.proficiency_evidence and item.proficiency_evidence.verified
            ):
                self.warn(
                    "proficiency_not_supported",
                    f"Proficiency for '{item.name}' was reset to unknown: the resume doesn't "
                    "state it.",
                    "skills",
                    item.id,
                )
                item.proficiency = "unknown"
                item.proficiency_evidence = None
            items.append(item)
        return items

    def languages(self, parsed: ParsedResume) -> list[LanguageItem]:
        items = []
        for lang in parsed.languages:
            item = LanguageItem(
                **lang.model_dump(exclude={"evidence"}), evidence=self.evidence(lang.evidence)
            )
            label = f"Language '{item.language}'"
            self.ground(
                item, "languages", label, [item.language, item.as_written], term_match=True
            )
            context = " ".join(e.source_text for e in item.evidence if e.verified)
            for name in (item.language, item.as_written):
                line = self.source.line_containing(name) if name else None
                if line:
                    context += " " + line
            context_source = SourceText(context)
            if item.cefr_level and not context_source.contains_term(item.cefr_level):
                self.warn(
                    "level_not_supported",
                    f"Removed CEFR level {item.cefr_level} for {item.language}: "
                    "the resume doesn't state it.",
                    "languages",
                    item.id,
                )
                item.cefr_level = None
            if item.proficiency and not self.source.contains(item.proficiency):
                self.warn(
                    "level_not_supported",
                    f"Removed proficiency '{item.proficiency}' for {item.language}: "
                    "the resume doesn't state it.",
                    "languages",
                    item.id,
                )
                item.proficiency = None
            items.append(item)
        return items

    def certifications(self, parsed: ParsedResume) -> list[CertificationItem]:
        items = []
        for cert in parsed.certifications:
            item = CertificationItem(
                **cert.model_dump(exclude={"evidence"}), evidence=self.evidence(cert.evidence)
            )
            label = f"Certification '{item.name}'"
            self.ground(item, "certifications", label, [item.name])
            self.dates(item, "certifications", label, ["date"])
            for name in ("credential_id", "url"):
                value = getattr(item, name)
                if value and not self.source.contains(value):
                    setattr(item, name, None)
                    self.warn(
                        "field_not_in_resume",
                        f"Removed {name.replace('_', ' ')} for {label}: "
                        "it doesn't appear in the resume.",
                        "certifications",
                        item.id,
                    )
            items.append(item)
        return items

    def projects(self, parsed: ParsedResume) -> list[ProjectItem]:
        items = []
        for project in parsed.projects:
            item = ProjectItem(
                **project.model_dump(exclude={"evidence"}),
                evidence=self.evidence(project.evidence),
            )
            label = f"Project '{item.name}'"
            self.ground(item, "projects", label, [item.name])
            self.dates(item, "projects", label, ["start_date", "end_date"])
            self.technologies(item, "projects", label)
            items.append(item)
        return items

    def additional_information(self, parsed: ParsedResume) -> list[AdditionalInfoItem]:
        items = []
        for extra in parsed.additional_information:
            item = AdditionalInfoItem(
                **extra.model_dump(exclude={"evidence"}), evidence=self.evidence(extra.evidence)
            )
            found = self.source.contains(item.value) or self.source.word_overlap(item.value) >= 0.85
            self.ground(
                item,
                "additional_information",
                f"'{item.value[:60]}'",
                [item.value] if found else [],
            )
            items.append(item)
        return items


def build_profile_data(parsed: ParsedResume, source_text: str) -> ValidationResult:
    """Validate parser output against the resume text and build the stored document.

    Raises `NoResumeContentError` if nothing usable was extracted -- an empty
    profile is never stored silently.
    """
    validator = _Validator(SourceText(source_text))
    data = ResumeProfileData(
        document_language=parsed.document_language,
        personal_information=validator.personal_information(parsed),
        summary=validator.summary(parsed),
        work_experience=validator.work_experience(parsed),
        education=validator.education(parsed),
        skills=validator.skills(parsed),
        languages=validator.languages(parsed),
        certifications=validator.certifications(parsed),
        projects=validator.projects(parsed),
        additional_information=validator.additional_information(parsed),
    )
    if next(data.iter_items(), None) is None:
        raise NoResumeContentError("parser returned no entities")
    # Round-trip through validation so the stored document is guaranteed to
    # match the schema even after the in-place adjustments above.
    data = ResumeProfileData.model_validate(data.model_dump())
    return ValidationResult(data=data, warnings=validator.warnings)
