"""Resume Intelligence data contracts.

Two layers, deliberately separate:

1. `ParsedResume` and its `Parsed*` parts: the structured output an AI
   resume parser must return. Its JSON schema (including the field
   descriptions below, which double as extraction instructions) is sent to
   the AI provider, and every response is validated against it before
   anything is stored. Contains no ids or review state.

2. `ResumeProfileData` and its `*Item` parts: what is persisted. Each
   extracted entity gets an `id`, a `review_status` (pending / accepted /
   edited / rejected) and server-verified evidence (`Evidence.verified`).

Missing information is always `null` (or an empty list) and unknown skill
proficiency is the literal "unknown" -- never a guess.
"""

import re
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Confidence = Literal["high", "medium", "low"]
# "explicit": stated in the resume. "inferred": derived by the parser from
# what's stated (e.g. a skill category, a canonical skill name).
Basis = Literal["explicit", "inferred"]
SkillProficiencyLevel = Literal["beginner", "intermediate", "advanced", "expert", "unknown"]
SkillCategory = Literal[
    "programming_language",
    "framework_or_library",
    "platform",
    "tool",
    "database",
    "cloud",
    "methodology",
    "domain_knowledge",
    "soft_skill",
    "other",
]
CefrLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2"]
PersonalField = Literal[
    "full_name", "email", "phone", "location", "linkedin_url", "github_url", "portfolio_url"
]
AdditionalCategory = Literal[
    "award",
    "publication",
    "volunteering",
    "interest",
    "driving_license",
    "work_authorization",
    "availability",
    "other",
]
ReviewStatus = Literal["pending", "accepted", "edited", "rejected"]

PERSONAL_FIELDS: tuple[PersonalField, ...] = (
    "full_name",
    "email",
    "phone",
    "location",
    "linkedin_url",
    "github_url",
    "portfolio_url",
)

_EVIDENCE_MAX_CHARS = 600
_YEAR_MONTH = re.compile(r"^(\d{4})-(\d{1,2})(?:-\d{1,2})?$")
_MONTH_YEAR = re.compile(r"^(\d{1,2})[./-](\d{4})$")
_YEAR = re.compile(r"^(\d{4})$")


def normalize_partial_date(value: Any) -> str | None:
    """Normalize a date to "YYYY" or "YYYY-MM"; anything unparseable -> None.

    This only re-formats what was given (e.g. "03/2021" -> "2021-03"); it
    never fills in a missing month or year. The original wording is kept
    separately in `date_text`.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if match := _YEAR.match(text):
        return match.group(1)
    if match := _YEAR_MONTH.match(text):
        year, month = match.group(1), int(match.group(2))
        return f"{year}-{month:02d}" if 1 <= month <= 12 else year
    if match := _MONTH_YEAR.match(text):
        month, year = int(match.group(1)), match.group(2)
        return f"{year}-{month:02d}" if 1 <= month <= 12 else year
    return None


def _clean_str_list(values: Any) -> list[str]:
    if not values:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            cleaned.append(text)
    return cleaned


class _Strict(BaseModel):
    """Base for parser-output models: whitespace-stripped strings, empty -> None."""

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _empty_string_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


# ---------------------------------------------------------------------------
# 1. Parser output contract (what the AI must return)
# ---------------------------------------------------------------------------


class ParsedEvidence(_Strict):
    source_section: str | None = Field(
        default=None,
        description=(
            "Resume section the quote comes from, with context where useful, e.g. "
            "'Experience — Example GmbH', 'Skills', 'Education'."
        ),
    )
    source_text: str = Field(
        description=(
            "A short VERBATIM quote copied character-for-character from the resume text "
            "that supports this fact (ideally one line or sentence, at most ~300 "
            "characters). Never paraphrase."
        )
    )

    @field_validator("source_text")
    @classmethod
    def _truncate(cls, value: str) -> str:
        return value[:_EVIDENCE_MAX_CHARS]


def _drop_blank(values: Any, key: str) -> Any:
    """Drop list entries whose required `key` is blank (e.g. an evidence quote
    of ""), so one cosmetic slip doesn't invalidate a whole resume. Anything
    structurally wrong still fails validation."""
    if not isinstance(values, list):
        return values
    return [
        v
        for v in values
        if not (isinstance(v, dict) and not str(v.get(key) or "").strip())
    ]


class _ParsedEntity(_Strict):
    confidence: Confidence = Field(
        description=(
            "Extraction certainty (NOT how skilled the person is). 'high' when explicitly "
            "and unambiguously stated; 'medium' when stated but ambiguous; 'low' when "
            "only weakly implied."
        )
    )
    basis: Basis = Field(
        default="explicit",
        description="'explicit' if directly stated in the resume, 'inferred' if derived.",
    )

    @field_validator("evidence", mode="before", check_fields=False)
    @classmethod
    def _drop_blank_evidence(cls, value: Any) -> Any:
        return _drop_blank(value, "source_text")


class ParsedValue(_ParsedEntity):
    value: str = Field(description="The value exactly as it appears in the resume.")
    evidence: ParsedEvidence | None = None


class ParsedPersonalInformation(_Strict):
    @field_validator("*", mode="before")
    @classmethod
    def _blank_value_to_none(cls, value: Any) -> Any:
        if isinstance(value, dict) and not str(value.get("value") or "").strip():
            return None
        return value

    full_name: ParsedValue | None = None
    email: ParsedValue | None = None
    phone: ParsedValue | None = None
    location: ParsedValue | None = Field(
        default=None, description="The candidate's own location/address, if stated."
    )
    linkedin_url: ParsedValue | None = None
    github_url: ParsedValue | None = None
    portfolio_url: ParsedValue | None = Field(
        default=None, description="Personal website or portfolio URL, if stated."
    )


class _Dated(_Strict):
    start_date: str | None = Field(
        default=None,
        description="'YYYY-MM' or 'YYYY' exactly as precise as the resume states; null if absent.",
    )
    end_date: str | None = Field(
        default=None,
        description="'YYYY-MM' or 'YYYY'; null if absent or if the entry is ongoing.",
    )
    date_text: str | None = Field(
        default=None, description="The date range exactly as written, e.g. '03/2021 – heute'."
    )

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _normalize_date(cls, value: Any) -> str | None:
        return normalize_partial_date(value)


class ParsedWorkExperience(_Dated, _ParsedEntity):
    company: str | None = None
    job_title: str | None = None
    location: str | None = None
    is_current: bool | None = Field(
        default=None,
        description=(
            "true only if the resume marks the role as ongoing (e.g. 'present', 'current', "
            "'heute', 'seit 2023'); null if unclear."
        ),
    )
    description: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(
        default_factory=list,
        description="Technologies explicitly named in this role's text. Never add unnamed ones.",
    )
    achievements: list[str] = Field(default_factory=list)
    evidence: list[ParsedEvidence] = Field(default_factory=list)

    @field_validator("responsibilities", "technologies", "achievements", mode="before")
    @classmethod
    def _clean_lists(cls, value: Any) -> list[str]:
        return _clean_str_list(value)


class ParsedEducation(_Dated, _ParsedEntity):
    institution: str | None = None
    degree: str | None = Field(
        default=None,
        description=(
            "Degree exactly as named (e.g. 'Diplom-Informatiker', 'B.Sc.'). Do not translate "
            "or convert to a foreign equivalent."
        ),
    )
    field_of_study: str | None = None
    location: str | None = None
    description: str | None = None
    evidence: list[ParsedEvidence] = Field(default_factory=list)


class ParsedSkill(_ParsedEntity):
    name: str = Field(
        description=(
            "Canonical skill name. Only merge obvious spelling variants of the SAME skill "
            "(e.g. 'ABAP', 'SAP-ABAP' -> 'SAP ABAP'); never broaden or upgrade meaning."
        )
    )
    aliases: list[str] = Field(
        default_factory=list, description="The variants exactly as written in the resume."
    )
    category: SkillCategory | None = None
    proficiency: SkillProficiencyLevel = Field(
        default="unknown",
        description=(
            "Only if the resume explicitly states a level for this skill (e.g. 'Expert', "
            "'Advanced', 'Grundkenntnisse'); otherwise 'unknown'. Never infer from years."
        ),
    )
    proficiency_evidence: ParsedEvidence | None = Field(
        default=None, description="Verbatim quote stating the proficiency, if one is set."
    )
    evidence: list[ParsedEvidence] = Field(
        default_factory=list,
        description="Where the skill appears: skills section, experience, projects, education.",
    )

    @field_validator("aliases", mode="before")
    @classmethod
    def _clean_aliases(cls, value: Any) -> list[str]:
        return _clean_str_list(value)


class ParsedLanguage(_ParsedEntity):
    language: str = Field(description="Language name in English, e.g. 'German'.")
    as_written: str | None = Field(
        default=None, description="The language name exactly as written, e.g. 'Deutsch'."
    )
    proficiency: str | None = Field(
        default=None,
        description="Proficiency exactly as stated (e.g. 'C1', 'native', 'fließend'); else null.",
    )
    cefr_level: CefrLevel | None = Field(
        default=None,
        description="Only if a CEFR level (A1-C2) is literally stated. Never derive one.",
    )
    evidence: list[ParsedEvidence] = Field(default_factory=list)


class ParsedCertification(_ParsedEntity):
    name: str
    issuer: str | None = None
    date: str | None = Field(default=None, description="'YYYY-MM' or 'YYYY' if stated.")
    credential_id: str | None = None
    url: str | None = None
    evidence: list[ParsedEvidence] = Field(default_factory=list)

    @field_validator("date", mode="before")
    @classmethod
    def _normalize_date(cls, value: Any) -> str | None:
        return normalize_partial_date(value)


class ParsedProject(_Dated, _ParsedEntity):
    name: str
    description: str | None = None
    role: str | None = None
    technologies: list[str] = Field(default_factory=list)
    url: str | None = None
    evidence: list[ParsedEvidence] = Field(default_factory=list)

    @field_validator("technologies", mode="before")
    @classmethod
    def _clean_lists(cls, value: Any) -> list[str]:
        return _clean_str_list(value)


class ParsedAdditionalInformation(_ParsedEntity):
    category: AdditionalCategory
    value: str
    evidence: list[ParsedEvidence] = Field(default_factory=list)


class ParsedResume(_Strict):
    """Root of the parser output contract."""

    document_language: str | None = Field(
        default=None, description="ISO 639-1 code of the resume's main language, e.g. 'en', 'de'."
    )
    personal_information: ParsedPersonalInformation = Field(
        default_factory=ParsedPersonalInformation
    )
    summary: ParsedValue | None = Field(
        default=None,
        description=(
            "The resume's own summary/profile/objective section, copied verbatim. null if the "
            "resume has none -- never write one."
        ),
    )
    work_experience: list[ParsedWorkExperience] = Field(default_factory=list)
    education: list[ParsedEducation] = Field(default_factory=list)
    skills: list[ParsedSkill] = Field(default_factory=list)
    languages: list[ParsedLanguage] = Field(default_factory=list)
    certifications: list[ParsedCertification] = Field(default_factory=list)
    projects: list[ParsedProject] = Field(default_factory=list)
    additional_information: list[ParsedAdditionalInformation] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _blank_summary_to_none(cls, value: Any) -> Any:
        if isinstance(value, dict) and not str(value.get("value") or "").strip():
            return None
        return value

    @field_validator("skills", "certifications", "projects", mode="before")
    @classmethod
    def _drop_unnamed(cls, value: Any) -> Any:
        return _drop_blank(value, "name")

    @field_validator("languages", mode="before")
    @classmethod
    def _drop_blank_languages(cls, value: Any) -> Any:
        return _drop_blank(value, "language")

    @field_validator("additional_information", mode="before")
    @classmethod
    def _drop_blank_additional(cls, value: Any) -> Any:
        return _drop_blank(value, "value")


# ---------------------------------------------------------------------------
# 2. Persisted, reviewable profile document
# ---------------------------------------------------------------------------


class Evidence(ParsedEvidence):
    # Set by the server after checking the quote against the extracted text;
    # never taken from the parser.
    verified: bool = False


def new_item_id() -> str:
    return uuid.uuid4().hex


class _ReviewFields(BaseModel):
    id: str = Field(default_factory=new_item_id)
    review_status: ReviewStatus = "pending"
    # Fields the user changed during review ("Edited by you" in the UI). The
    # parser's original values remain in ResumeProfile.raw_data.
    user_edited_fields: list[str] = Field(default_factory=list)


class PersonalInfoItem(_ReviewFields, _ParsedEntity):
    field: PersonalField
    value: str
    evidence: list[Evidence] = Field(default_factory=list)


class SummaryItem(_ReviewFields, _ParsedEntity):
    value: str
    evidence: list[Evidence] = Field(default_factory=list)


class WorkExperienceItem(_ReviewFields, ParsedWorkExperience):
    evidence: list[Evidence] = Field(default_factory=list)  # type: ignore[assignment]


class EducationItem(_ReviewFields, ParsedEducation):
    evidence: list[Evidence] = Field(default_factory=list)  # type: ignore[assignment]


class SkillItem(_ReviewFields, ParsedSkill):
    evidence: list[Evidence] = Field(default_factory=list)  # type: ignore[assignment]
    proficiency_evidence: Evidence | None = None  # type: ignore[assignment]


class LanguageItem(_ReviewFields, ParsedLanguage):
    evidence: list[Evidence] = Field(default_factory=list)  # type: ignore[assignment]


class CertificationItem(_ReviewFields, ParsedCertification):
    evidence: list[Evidence] = Field(default_factory=list)  # type: ignore[assignment]


class ProjectItem(_ReviewFields, ParsedProject):
    evidence: list[Evidence] = Field(default_factory=list)  # type: ignore[assignment]


class AdditionalInfoItem(_ReviewFields, ParsedAdditionalInformation):
    evidence: list[Evidence] = Field(default_factory=list)  # type: ignore[assignment]


SectionName = Literal[
    "personal_information",
    "summary",
    "work_experience",
    "education",
    "skills",
    "languages",
    "certifications",
    "projects",
    "additional_information",
]
SECTION_NAMES: tuple[SectionName, ...] = (
    "personal_information",
    "summary",
    "work_experience",
    "education",
    "skills",
    "languages",
    "certifications",
    "projects",
    "additional_information",
)

ReviewableItem = (
    PersonalInfoItem
    | SummaryItem
    | WorkExperienceItem
    | EducationItem
    | SkillItem
    | LanguageItem
    | CertificationItem
    | ProjectItem
    | AdditionalInfoItem
)


class ResumeProfileData(BaseModel):
    """The persisted, reviewable Resume Profile document."""

    schema_version: int = 1
    document_language: str | None = None
    personal_information: list[PersonalInfoItem] = Field(default_factory=list)
    summary: SummaryItem | None = None
    work_experience: list[WorkExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[SkillItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    additional_information: list[AdditionalInfoItem] = Field(default_factory=list)

    def iter_items(self) -> Iterator[tuple[SectionName, ReviewableItem]]:
        for section in SECTION_NAMES:
            value = getattr(self, section)
            if value is None:
                continue
            items = value if isinstance(value, list) else [value]
            for item in items:
                yield section, item

    def find_item(self, item_id: str) -> tuple[SectionName, ReviewableItem] | None:
        for section, item in self.iter_items():
            if item.id == item_id:
                return section, item
        return None

    def replace_item(self, section: SectionName, updated: ReviewableItem) -> None:
        if section == "summary":
            self.summary = updated  # type: ignore[assignment]
            return
        items: list[Any] = getattr(self, section)
        for index, item in enumerate(items):
            if item.id == updated.id:
                items[index] = updated
                return

    def active_items(self, section: SectionName) -> list[Any]:
        """Items in `section` that the user hasn't rejected."""
        value = getattr(self, section)
        items = value if isinstance(value, list) else ([value] if value is not None else [])
        return [item for item in items if item.review_status != "rejected"]


# ---------------------------------------------------------------------------
# 3. Review edit payloads (only fact fields are editable -- never evidence,
#    confidence, or ids)
# ---------------------------------------------------------------------------


class _EditBase(_Strict):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ValueEdit(_EditBase):
    value: str = Field(min_length=1, max_length=5000)


class WorkExperienceEdit(_EditBase):
    company: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool | None = None
    description: str | None = Field(default=None, max_length=5000)
    responsibilities: list[str] | None = None
    technologies: list[str] | None = None
    achievements: list[str] | None = None


class EducationEdit(_EditBase):
    institution: str | None = Field(default=None, max_length=255)
    degree: str | None = Field(default=None, max_length=255)
    field_of_study: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = Field(default=None, max_length=5000)


class SkillEdit(_EditBase):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: SkillCategory | None = None
    proficiency: SkillProficiencyLevel | None = None


class LanguageEdit(_EditBase):
    language: str | None = Field(default=None, min_length=1, max_length=100)
    proficiency: str | None = Field(default=None, max_length=100)
    cefr_level: CefrLevel | None = None


class CertificationEdit(_EditBase):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    issuer: str | None = Field(default=None, max_length=255)
    date: str | None = None
    credential_id: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=500)


class ProjectEdit(_EditBase):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    role: str | None = Field(default=None, max_length=255)
    technologies: list[str] | None = None
    url: str | None = Field(default=None, max_length=500)
    start_date: str | None = None
    end_date: str | None = None


class AdditionalInfoEdit(_EditBase):
    category: AdditionalCategory | None = None
    value: str | None = Field(default=None, min_length=1, max_length=2000)


EDIT_MODELS: dict[SectionName, type[_EditBase]] = {
    "personal_information": ValueEdit,
    "summary": ValueEdit,
    "work_experience": WorkExperienceEdit,
    "education": EducationEdit,
    "skills": SkillEdit,
    "languages": LanguageEdit,
    "certifications": CertificationEdit,
    "projects": ProjectEdit,
    "additional_information": AdditionalInfoEdit,
}


# ---------------------------------------------------------------------------
# 4. API request/response schemas
# ---------------------------------------------------------------------------


class ProfileWarning(BaseModel):
    code: str
    message: str
    section: SectionName | None = None
    item_id: str | None = None


class SectionCounts(BaseModel):
    total: int = 0
    pending: int = 0
    accepted: int = 0
    edited: int = 0
    rejected: int = 0


class ProfileSummary(BaseModel):
    sections: dict[str, SectionCounts]
    total_items: int
    pending_items: int


class ProfileConflict(BaseModel):
    """A field where the user's own profile and the resume disagree.

    Neither side is overwritten; both values are exposed for the user to
    resolve.
    """

    field: str
    label: str
    profile_value: str
    resume_value: str
    potential_conflict: bool = True
    message: str


class DerivedMetrics(BaseModel):
    """Values computed deterministically from the (non-rejected) resume data."""

    total_experience_months: int | None = None
    experience_years: float | None = None
    # True when at least one work experience lacks a usable start date, so
    # the total above is a lower bound.
    experience_is_partial: bool = False
    current_job_title: str | None = None


class ResumeProcessRequest(BaseModel):
    force: bool = Field(
        default=False,
        description="Re-process even if this file was already processed by this parser version.",
    )


class ProcessingAttemptRead(BaseModel):
    profile_id: uuid.UUID
    status: str
    error_code: str | None
    error_message: str | None
    parser_version: str
    started_at: datetime | None
    processed_at: datetime | None


class ResumeProfileRead(BaseModel):
    id: uuid.UUID
    resume_id: uuid.UUID
    status: str
    parser_version: str
    schema_version: int
    ai_provider: str | None
    ai_model: str | None
    source_file_hash: str | None
    reused_from_profile_id: uuid.UUID | None
    extraction_method: str | None
    page_count: int | None
    created_at: datetime
    processed_at: datetime | None
    confirmed_at: datetime | None
    data: ResumeProfileData
    raw_data: ResumeProfileData
    warnings: list[ProfileWarning]
    summary: ProfileSummary
    conflicts: list[ProfileConflict]
    derived: DerivedMetrics


class ReviewItemRequest(BaseModel):
    action: Literal["accept", "reject", "edit", "reset"]
    changes: dict[str, Any] | None = Field(
        default=None, description="Required for 'edit': the fields to change."
    )


class ResumeSourceRead(BaseModel):
    profile_id: uuid.UUID
    extraction_method: str | None
    page_count: int | None
    text: str
