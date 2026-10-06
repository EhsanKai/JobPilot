"""The Application Context document (V0.7): what a job was when the user
started applying to it.

An application's context is captured once, when the workspace is created,
and never changes afterwards -- not when the canonical `Job` is refreshed by
later discovery runs, and not when source postings change or disappear.
Later versions (tailored CV, Anschreiben, application questions, browser
automation) read it to reproduce exactly which job the user was applying to.

It reuses the canonical-content mechanism of V0.4: the `job` part has the
same fields as `CANONICAL_CONTENT_FIELDS` (the columns a posting's content
becomes on `Job`), plus the URLs and the canonical job id. The `source` part
is the posting chosen as the application target: its identity, the URLs as
the source stated them, and where applying actually happens.

Like the V0.5 documents, every part forbids unknown keys, so the document can
only contain what is declared here -- in particular nothing the user wrote
(notes, salary expectation...) and no raw source payloads. Stored as JSONB in
`application_context_snapshots.document` and validated on every write and
read.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from pydantic import Field, field_validator

from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel
from app.schemas.document_types import DocumentModel, quantize_money

APPLICATION_CONTEXT_SCHEMA_VERSION: Final = "application_context_v1"

#: Where a context came from. "created": captured when the user started the
#: application. "migrated": the application predates snapshots (V0
#: placeholder rows), so the context was rebuilt from the job as it was when
#: the V0.7 migration ran -- the earliest state still available.
ContextOrigin = Literal["created", "migrated"]


class ContextJob(DocumentModel):
    """The canonical job at capture time."""

    #: The canonical job this application was started from. A reference for
    #: navigation; every value below is the snapshot, not a live read.
    job_id: uuid.UUID
    canonical_url: str = Field(min_length=1, max_length=2000)
    apply_url: str | None = Field(default=None, max_length=2000)

    # The same fields, in the same order, as CANONICAL_CONTENT_FIELDS.
    title: str = Field(min_length=1, max_length=500)
    company_name: str = Field(min_length=1, max_length=255)
    location: str | None = None
    locations: list[str] = Field(default_factory=list)
    country: str | None = None
    department: str | None = None
    description: str | None = None
    requirements: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = None
    salary_period: SalaryPeriod | None = None
    #: The salary as the posting wrote it, also when too ambiguous to structure.
    salary_text: str | None = None
    employment_type: EmploymentTypeValue | None = None
    work_model: RemotePreference | None = None
    seniority: SeniorityLevel | None = None
    industry: str | None = None
    posted_at: datetime | None = None

    @field_validator("salary_min", "salary_max")
    @classmethod
    def _quantize(cls, value: Decimal | None) -> Decimal | None:
        # Money has exactly two decimals, so equal amounts hash identically.
        return quantize_money(value)


class ContextSource(DocumentModel):
    """The source posting chosen as the application target."""

    #: The posting row, when it existed (None for a job with no postings).
    #: The row may disappear later; everything below survives it.
    posting_id: uuid.UUID | None = None
    #: Source name, e.g. "greenhouse" ("unknown" for a job without postings).
    source: str = Field(min_length=1, max_length=100)
    source_job_id: str | None = Field(default=None, max_length=255)
    #: The board / site within the source (e.g. a Greenhouse board token).
    source_company_key: str | None = Field(default=None, max_length=255)

    # What this source said about the job, as written.
    title: str = Field(min_length=1, max_length=500)
    company_name: str = Field(min_length=1, max_length=255)
    location: str | None = None
    posted_at: datetime | None = None
    source_updated_at: datetime | None = None

    #: The posting URL exactly as the source returned it (the normalized URL
    #: when the source gave none): what "View original posting" opens.
    original_url: str = Field(min_length=1, max_length=2000)
    #: The same URL with tracking parameters and fragment removed.
    canonical_url: str = Field(min_length=1, max_length=2000)
    #: The application URL the source states explicitly, if any.
    apply_url: str | None = Field(default=None, max_length=2000)
    #: Where applying happens: `apply_url`, else the posting page for sources
    #: that host the application form themselves. None when unknown -- the
    #: job's canonical URL is deliberately *not* assumed to be it.
    application_url: str | None = Field(default=None, max_length=2000)
    #: Whether this was the job's preferred posting when captured.
    was_preferred: bool = False


class ApplicationContextDocument(DocumentModel):
    schema_version: Literal["application_context_v1"] = APPLICATION_CONTEXT_SCHEMA_VERSION
    origin: ContextOrigin = "created"
    captured_at: datetime
    job: ContextJob
    source: ContextSource
