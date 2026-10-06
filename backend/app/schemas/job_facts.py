"""Job Intelligence fact sheet document (V0.5).

`JobFacts` is the content of `job_intelligence.facts`: what a deterministic
extractor established about a canonical job, each fact with its provenance.
Requirements (skills, experience, languages, ...) are separate rows in
`job_requirements`.

Every fact is optional: None means "not stated / not extracted", which
matching must treat as unknown -- never as a negative. Extraction itself is
a later V0.5 phase; this module only defines and validates the document.
"""

from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import Field, field_validator, model_validator

from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference, TravelWillingness
from app.models.job_requirement import ExtractionConfidence
from app.models.professional_profile import SeniorityLevel
from app.schemas.document_types import (
    EVIDENCE_MAX_CHARS,
    CountryCode,
    CurrencyCode,
    DocumentModel,
    LanguageKey,
    LocationValue,
    quantize_money,
)

JOB_FACTS_SCHEMA_VERSION = "job_facts_v1"

Money = Annotated[Decimal, Field(ge=0, le=Decimal("10000000"))]

T = TypeVar("T")


class Fact(DocumentModel, Generic[T]):
    """One extracted fact and where it came from (same provenance fields as
    a `job_requirements` row)."""

    value: T
    #: Canonical job field the fact was read from, e.g. "description".
    source_field: str = Field(min_length=1, max_length=50)
    extraction_method: str = Field(min_length=1, max_length=50)
    confidence: ExtractionConfidence
    #: Verbatim quote, when the fact came from text.
    evidence_text: str | None = Field(default=None, max_length=EVIDENCE_MAX_CHARS)
    #: Character offset of `evidence_text` in `source_field`.
    evidence_offset: int | None = Field(default=None, ge=0)


class SalaryValue(DocumentModel):
    minimum: Money | None = None
    maximum: Money | None = None
    currency: CurrencyCode | None = None
    period: SalaryPeriod | None = None

    @field_validator("minimum", "maximum")
    @classmethod
    def _quantize(cls, value: Decimal | None) -> Decimal | None:
        return quantize_money(value)

    @model_validator(mode="after")
    def _range(self) -> "SalaryValue":
        if self.minimum is None and self.maximum is None:
            raise ValueError("a salary needs a minimum or a maximum")
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("salary maximum must be greater than or equal to its minimum")
        return self


class AuthorizationValue(DocumentModel):
    """What the posting itself states about work authorization."""

    #: Countries the posting requires authorization for.
    countries: list[CountryCode] = Field(default_factory=list)
    #: Whether the posting offers visa sponsorship; None when not stated.
    sponsorship_available: bool | None = None

    @field_validator("countries")
    @classmethod
    def _canonical(cls, values: list[str]) -> list[str]:
        return sorted(set(values))

    @model_validator(mode="after")
    def _not_blank(self) -> "AuthorizationValue":
        if not self.countries and self.sponsorship_available is None:
            raise ValueError("an authorization fact needs countries or sponsorship information")
        return self


class JobFacts(DocumentModel):
    """Root of `job_intelligence.facts`."""

    schema_version: str = Field(default=JOB_FACTS_SCHEMA_VERSION, min_length=1, max_length=50)

    # V0.6 (additive): the canonical title and industry the extractor read,
    # so a match result can be reproduced from its fact sheet alone.
    title: Fact[Annotated[str, Field(min_length=1, max_length=500)]] | None = None
    industry: Fact[Annotated[str, Field(min_length=1, max_length=255)]] | None = None

    seniority: Fact[SeniorityLevel] | None = None
    employment_type: Fact[EmploymentTypeValue] | None = None
    work_model: Fact[RemotePreference] | None = None
    location: Fact[Annotated[list[LocationValue], Field(min_length=1)]] | None = None
    salary: Fact[SalaryValue] | None = None
    #: Working language(s) the posting explicitly names.
    language: Fact[Annotated[list[LanguageKey], Field(min_length=1)]] | None = None
    travel: Fact[TravelWillingness] | None = None
    authorization: Fact[AuthorizationValue] | None = None
    #: The language the posting is written in. A *signal* only: it never
    #: becomes a requirement by itself.
    document_language: Fact[LanguageKey] | None = None
