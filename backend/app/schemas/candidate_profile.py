"""Candidate Profile snapshot document (V0.5).

`CandidateProfileDocument` is the content of one immutable
`candidate_profile_versions` row: everything future matching may know about
the candidate, frozen so that a match result can always be reproduced from
the exact inputs it used.

It is a read model, a *union* of two inputs:

- the V0.1 profile tables (`professional_profiles`, `job_preferences`,
  `user_skills`, ...) -- the editable source of truth, origin "user";
- the user's confirmed Resume Profile (`resume_profiles.reviewed_data`,
  accepted/edited items only), origin "resume".

Resume data is never written back into the V0.1 tables. Where both inputs
state a value for the same fact, the user-declared value wins; every
origin-bearing entry records which input it came from (`origin`) and, for
resume-derived values, where in the Resume Profile (`evidence_ref`).

Building snapshots is a later V0.5 phase. This module only defines and
validates the document.

Deliberately NOT represented (and rejected as unknown keys): name, email,
phone, LinkedIn or other profile URLs, resume text, age or birth date,
gender, nationality, marital status, photo, health information. Education
dates are left out too (graduation years are an age proxy).
"""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import Field, RootModel, field_validator, model_validator

from app.models.deal_breaker import DealBreakerType
from app.models.job_preferences import (
    EmploymentTypeValue,
    JobTitlePriority,
    RelocationPreference,
    RemotePreference,
    TravelWillingness,
)
from app.models.professional_profile import SeniorityLevel
from app.schemas.document_types import (
    CountryCode,
    CurrencyCode,
    DocumentModel,
    LanguageKey,
    LocationValue,
    PartialDate,
    quantize_money,
    unique_in_order,
)
from app.schemas.resume_profile import SkillCategory, SkillProficiencyLevel

CANDIDATE_PROFILE_SCHEMA_VERSION = "candidate_profile_v1"

ProfileOrigin = Literal["user", "resume"]
PROFILE_ORIGINS: tuple[ProfileOrigin, ...] = ("user", "resume")

ProfileSection = Literal[
    "role",
    "skills",
    "languages",
    "location_and_mobility",
    "preferences",
    "deal_breakers",
    "credentials",
    "work_authorization",
]
PROFILE_SECTIONS: tuple[ProfileSection, ...] = (
    "role",
    "skills",
    "languages",
    "location_and_mobility",
    "preferences",
    "deal_breakers",
    "credentials",
    "work_authorization",
)

# CEFR levels, plus "native". "unknown" when no level was stated -- never
# derived from anything else.
LanguageLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2", "native", "unknown"]
EducationLevel = Literal["secondary", "vocational", "bachelor", "master", "doctorate", "other"]

# `<resume_profile id>` optionally followed by `/<item id or derived field>`
# inside that profile's reviewed_data.
_EVIDENCE_REF = re.compile(
    r"^resume_profile:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"(?:/[A-Za-z0-9_.-]{1,100})?$"
)

Title = Annotated[str, Field(min_length=1, max_length=255)]
Years = Annotated[float, Field(ge=0, le=80)]
Money = Annotated[Decimal, Field(ge=0, le=Decimal("10000000"))]

T = TypeVar("T")


class Origin(DocumentModel):
    """Where a value in the snapshot came from."""

    origin: ProfileOrigin
    #: Required for resume-derived values, absent for user-declared ones.
    evidence_ref: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _evidence_matches_origin(self) -> "Origin":
        if self.origin == "resume":
            if not self.evidence_ref or not _EVIDENCE_REF.match(self.evidence_ref):
                raise ValueError("resume-derived values need a 'resume_profile:...' evidence_ref")
        elif self.evidence_ref is not None:
            raise ValueError("user-declared values carry no evidence_ref")
        return self


class SourcedValue(Origin, Generic[T]):
    """A single fact together with its origin."""

    value: T


class TargetTitle(DocumentModel):
    title: Title
    priority: JobTitlePriority = JobTitlePriority.SECONDARY


class RoleSection(DocumentModel):
    current_title: SourcedValue[Title] | None = None
    seniority_level: SourcedValue[SeniorityLevel] | None = None
    years_of_experience: SourcedValue[Years] | None = None
    # Job Preferences: user-declared only.
    target_titles: list[TargetTitle] = Field(default_factory=list)

    @field_validator("target_titles")
    @classmethod
    def _unique_titles(cls, values: list[TargetTitle]) -> list[TargetTitle]:
        if len({v.title.casefold() for v in values}) != len(values):
            raise ValueError("target_titles must be unique")
        return values

    def is_empty(self) -> bool:
        return (
            self.current_title is None
            and self.seniority_level is None
            and self.years_of_experience is None
            and not self.target_titles
        )


class SkillEntry(Origin):
    #: Display name as the user or resume states it.
    name: Title
    #: Skill taxonomy key. NULL until skill normalization exists (later V0.5).
    skill_key: str | None = Field(default=None, min_length=1, max_length=100)
    category: SkillCategory | None = None
    proficiency: SkillProficiencyLevel = "unknown"
    years: Years | None = None
    last_used: PartialDate | None = None


class LanguageEntry(Origin):
    language_key: LanguageKey
    level: LanguageLevel = "unknown"


class LocationAndMobility(DocumentModel):
    current_location: SourcedValue[LocationValue] | None = None
    # Job Preferences: user-declared only.
    preferred_locations: list[LocationValue] = Field(default_factory=list)
    relocation: RelocationPreference | None = None
    travel: TravelWillingness | None = None

    @field_validator("preferred_locations")
    @classmethod
    def _unique_locations(cls, values: list[LocationValue]) -> list[LocationValue]:
        if len({v.text.casefold() for v in values}) != len(values):
            raise ValueError("preferred_locations must be unique")
        return values

    def is_empty(self) -> bool:
        return (
            self.current_location is None
            and not self.preferred_locations
            and self.relocation is None
            and self.travel is None
        )


class Preferences(DocumentModel):
    """Job Preferences (user-declared only). Salaries are annual figures in
    `currency`, the V0.1 convention."""

    work_models: list[RemotePreference] = Field(default_factory=list)
    employment_types: list[EmploymentTypeValue] = Field(default_factory=list)
    minimum_salary: Money | None = None
    desired_salary: Money | None = None
    currency: CurrencyCode | None = None

    @field_validator("work_models", "employment_types")
    @classmethod
    def _dedupe(cls, values: list[Any]) -> list[Any]:
        return unique_in_order(values)

    @field_validator("minimum_salary", "desired_salary")
    @classmethod
    def _quantize(cls, value: Decimal | None) -> Decimal | None:
        return quantize_money(value)

    @model_validator(mode="after")
    def _salary_range(self) -> "Preferences":
        if (
            self.minimum_salary is not None
            and self.desired_salary is not None
            and self.desired_salary < self.minimum_salary
        ):
            raise ValueError("desired_salary must be greater than or equal to minimum_salary")
        return self

    def is_empty(self) -> bool:
        return (
            not self.work_models
            and not self.employment_types
            and self.minimum_salary is None
            and self.desired_salary is None
            and self.currency is None
        )


class DealBreakerEntry(DocumentModel):
    """The V0.1 `deal_breakers` representation, unchanged."""

    type: DealBreakerType
    value: str | None = Field(default=None, max_length=255)
    enabled: bool = True


class EducationEntry(Origin):
    degree: str | None = Field(default=None, max_length=255)
    #: Normalized degree level. NULL until education normalization exists.
    degree_level: EducationLevel | None = None
    field_of_study: str | None = Field(default=None, max_length=255)
    institution: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _not_blank(self) -> "EducationEntry":
        if not (self.degree or self.degree_level or self.field_of_study or self.institution):
            raise ValueError("an education entry needs at least one fact")
        return self


class CertificationEntry(Origin):
    name: Title
    issuer: str | None = Field(default=None, max_length=255)
    date: PartialDate | None = None


class Credentials(DocumentModel):
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.education and not self.certifications


class WorkAuthorization(DocumentModel):
    """Countries the user has *declared* they are authorized to work in.

    User-declared only, never inferred (not from location, language, name,
    resume or anything else). An empty list means UNKNOWN -- not "not
    authorized". Nationality is never collected.
    """

    authorized_countries: list[CountryCode] = Field(default_factory=list)

    @field_validator("authorized_countries")
    @classmethod
    def _canonical(cls, values: list[str]) -> list[str]:
        return sorted(set(values))

    def is_empty(self) -> bool:
        return not self.authorized_countries


class SnapshotMetadata(DocumentModel):
    """Derived from the document's content (and verified against it), so two
    snapshots with equal content always serialize -- and hash -- equally.
    Left as None, `sections_provided` and `sources` are filled in."""

    #: Sections that hold any data, in `PROFILE_SECTIONS` order. A section
    #: missing here was not provided: matching must treat it as unknown.
    sections_provided: list[ProfileSection] | None = None
    schema_version: str = Field(
        default=CANDIDATE_PROFILE_SCHEMA_VERSION, min_length=1, max_length=50
    )
    #: Skill taxonomy version the `skill_key`s refer to; NULL while no
    #: taxonomy exists.
    taxonomy_version: str | None = Field(default=None, min_length=1, max_length=50)
    #: Origins that contributed any value, in `PROFILE_ORIGINS` order.
    sources: list[ProfileOrigin] | None = None


class CandidateProfileDocument(DocumentModel):
    """Root of `candidate_profile_versions.document`."""

    role: RoleSection = Field(default_factory=RoleSection)
    skills: list[SkillEntry] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)
    location_and_mobility: LocationAndMobility = Field(default_factory=LocationAndMobility)
    preferences: Preferences = Field(default_factory=Preferences)
    deal_breakers: list[DealBreakerEntry] = Field(default_factory=list)
    credentials: Credentials = Field(default_factory=Credentials)
    work_authorization: WorkAuthorization = Field(default_factory=WorkAuthorization)
    metadata: SnapshotMetadata = Field(default_factory=SnapshotMetadata)

    @field_validator("skills")
    @classmethod
    def _unique_skills(cls, values: list[SkillEntry]) -> list[SkillEntry]:
        names = [v.name.casefold() for v in values]
        keys = [v.skill_key for v in values if v.skill_key is not None]
        if len(set(names)) != len(names) or len(set(keys)) != len(keys):
            raise ValueError("skills must be unique (one entry per skill)")
        return values

    @field_validator("languages")
    @classmethod
    def _unique_languages(cls, values: list[LanguageEntry]) -> list[LanguageEntry]:
        if len({v.language_key for v in values}) != len(values):
            raise ValueError("languages must be unique (one entry per language)")
        return values

    @field_validator("deal_breakers")
    @classmethod
    def _unique_deal_breakers(cls, values: list[DealBreakerEntry]) -> list[DealBreakerEntry]:
        if len({v.type for v in values}) != len(values):
            raise ValueError("deal_breakers must be unique per type")
        return values

    def provided_sections(self) -> list[ProfileSection]:
        empty = {
            "role": self.role.is_empty(),
            "skills": not self.skills,
            "languages": not self.languages,
            "location_and_mobility": self.location_and_mobility.is_empty(),
            "preferences": self.preferences.is_empty(),
            "deal_breakers": not self.deal_breakers,
            "credentials": self.credentials.is_empty(),
            "work_authorization": self.work_authorization.is_empty(),
        }
        return [section for section in PROFILE_SECTIONS if not empty[section]]

    def contributing_origins(self) -> list[ProfileOrigin]:
        sourced: list[Origin | None] = [
            self.role.current_title,
            self.role.seniority_level,
            self.role.years_of_experience,
            self.location_and_mobility.current_location,
            *self.skills,
            *self.languages,
            *self.credentials.education,
            *self.credentials.certifications,
        ]
        origins = {item.origin for item in sourced if item is not None}
        # These are user-declared by definition.
        if (
            self.role.target_titles
            or self.location_and_mobility.preferred_locations
            or self.location_and_mobility.relocation is not None
            or self.location_and_mobility.travel is not None
            or not self.preferences.is_empty()
            or self.deal_breakers
            or not self.work_authorization.is_empty()
        ):
            origins.add("user")
        return [origin for origin in PROFILE_ORIGINS if origin in origins]

    @model_validator(mode="after")
    def _derive_metadata(self) -> "CandidateProfileDocument":
        for field, actual in (
            ("sections_provided", self.provided_sections()),
            ("sources", self.contributing_origins()),
        ):
            declared = getattr(self.metadata, field)
            if declared is None:
                setattr(self.metadata, field, actual)
            elif declared != actual:
                raise ValueError(
                    f"metadata.{field} is {declared}, but the document content gives {actual}"
                )
        return self


# ---------------------------------------------------------------------------
# candidate_profile_versions.sources: the exact input records
# ---------------------------------------------------------------------------

SnapshotRecordType = Literal[
    "user_profile",
    "professional_profile",
    "job_preferences",
    "user_skills",
    "user_languages",
    "deal_breakers",
    "resume_profile",
]


class SnapshotSource(DocumentModel):
    """One input record a snapshot was built from (audit/provenance).

    Unlike `metadata.sources`, these carry record ids and timestamps, so they
    are *not* part of the content hash: re-saving an unchanged profile must
    not produce a new snapshot version.
    """

    origin: ProfileOrigin
    record_type: SnapshotRecordType
    #: NULL for multi-row inputs (e.g. all of a user's skills).
    record_id: uuid.UUID | None = None
    record_updated_at: datetime | None = None

    @model_validator(mode="after")
    def _origin_matches_record(self) -> "SnapshotSource":
        expected: ProfileOrigin = "resume" if self.record_type == "resume_profile" else "user"
        if self.origin != expected:
            raise ValueError(f"{self.record_type} records have origin {expected!r}")
        if self.record_type == "resume_profile" and self.record_id is None:
            raise ValueError("a resume_profile source needs its record_id")
        return self


class SnapshotSources(RootModel[list[SnapshotSource]]):
    @field_validator("root")
    @classmethod
    def _unique(cls, values: list[SnapshotSource]) -> list[SnapshotSource]:
        if len({(v.record_type, v.record_id) for v in values}) != len(values):
            raise ValueError("snapshot sources must be unique")
        return values
