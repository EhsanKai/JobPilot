"""Request/response schemas for the Application Workspace (V0.7).

An application's *preparation data* is specific to one application and never
touches the user's profile or preferences. Requests are strict (unknown
fields are rejected, so a client cannot try to set `status`, `user_id` or
`job_id`); for updates, an omitted field is left alone and an explicit `null`
clears it.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.application import ApplicationStatus, NoticePeriodUnit, WorkAuthorization
from app.models.resume import ResumeStatus
from app.schemas.application_context import ApplicationContextDocument
from app.schemas.application_tracking import ApplicationTrackingRead, SubmissionSummaryRead
from app.schemas.document_types import CurrencyCode, LanguageKey, quantize_money
from app.schemas.matching import MatchSummaryRead

#: Languages an application can be written in. ISO 639-1 keys, like the rest
#: of JobPilot's language handling; adding one needs no migration.
SUPPORTED_APPLICATION_LANGUAGES: tuple[str, ...] = ("de", "en")

#: Same ceiling as job search criteria and the Postgres column (NUMERIC(12,2)).
MAX_SALARY_EXPECTATION = Decimal("10000000")
MAX_NOTES_LENGTH = 10_000
#: Longest notice period accepted, per unit (two years in each).
MAX_NOTICE_PERIOD: dict[NoticePeriodUnit, int] = {
    NoticePeriodUnit.DAYS: 730,
    NoticePeriodUnit.WEEKS: 104,
    NoticePeriodUnit.MONTHS: 24,
}
_EARLIEST_AVAILABLE_YEAR = 2000
_LATEST_AVAILABLE_DAYS = 5 * 366


class NoticePeriod(BaseModel):
    """How long the candidate must give notice before starting, as they
    state it for this application. No legal assumptions are made: `0` means
    available immediately."""

    model_config = ConfigDict(extra="forbid")

    value: int = Field(ge=0)
    unit: NoticePeriodUnit

    @model_validator(mode="after")
    def _within_limit(self) -> "NoticePeriod":
        limit = MAX_NOTICE_PERIOD[self.unit]
        if self.value > limit:
            raise ValueError(f"Notice period can be at most {limit} {self.unit.value}.")
        return self


class ApplicationPreparationFields(BaseModel):
    """The application-specific preparation data. Every field is optional:
    nothing is invented for the user."""

    model_config = ConfigDict(extra="forbid")

    #: Annual gross, in `salary_currency` (the Job Preferences convention).
    salary_expectation: Decimal | None = None
    salary_currency: CurrencyCode | None = None
    #: The user's explicit choice whether a future letter states the salary.
    #: `None` = not decided.
    mention_salary_in_letter: bool | None = None
    #: The date the candidate can start. A date in the past means "available
    #: now".
    available_from: date | None = None
    notice_period: NoticePeriod | None = None
    work_authorization: WorkAuthorization | None = None
    application_language: LanguageKey | None = None
    #: Private; never sent to an employer.
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)

    @field_validator("salary_expectation")
    @classmethod
    def _salary(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if not value.is_finite() or value <= 0:
            raise ValueError("Salary expectation must be greater than 0.")
        if value > MAX_SALARY_EXPECTATION:
            raise ValueError(f"Salary expectation can be at most {MAX_SALARY_EXPECTATION:,.0f}.")
        if value != value.quantize(Decimal("0.01")):
            raise ValueError("Salary expectation can have at most 2 decimal places.")
        return quantize_money(value)

    @field_validator("available_from")
    @classmethod
    def _available_from(cls, value: date | None) -> date | None:
        if value is None:
            return None
        latest = datetime.now(UTC).date() + timedelta(days=_LATEST_AVAILABLE_DAYS)
        if value.year < _EARLIEST_AVAILABLE_YEAR or value > latest:
            raise ValueError(
                f"Available from must be a realistic date (from {_EARLIEST_AVAILABLE_YEAR} "
                "to at most 5 years ahead)."
            )
        return value

    @field_validator("application_language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_APPLICATION_LANGUAGES:
            raise ValueError(
                "Application language must be one of: "
                f"{', '.join(SUPPORTED_APPLICATION_LANGUAGES)}."
            )
        return value

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ApplicationCreate(ApplicationPreparationFields):
    """Body of `POST /jobs/{job_id}/application` (optional as a whole).

    Only used when the application is created: starting an application that
    already exists returns it unchanged.
    """

    #: The resume version to build on. Without it, the user's most recently
    #: confirmed resume (else their latest upload, else none) is used.
    resume_id: uuid.UUID | None = None
    #: The source posting to apply through. Without it, the job's preferred
    #: posting is used.
    source_posting_id: uuid.UUID | None = None


class ApplicationUpdate(ApplicationPreparationFields):
    """Body of `PATCH /applications/{id}`: only application-specific data.
    Omitted fields are unchanged; `null` clears a field (except `resume_id`,
    which can only be replaced)."""

    resume_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _resume_is_replaced_not_cleared(self) -> "ApplicationUpdate":
        if "resume_id" in self.model_fields_set and self.resume_id is None:
            raise ValueError("A resume can't be removed from an application; choose another one.")
        return self


# --- Read models ---------------------------------------------------------------------


class ApplicationRefRead(BaseModel):
    """The user's application for a job, as shown on the job (list and
    details): enough to offer "Continue Application"."""

    id: uuid.UUID
    status: ApplicationStatus


PreparationKey = Literal[
    "job", "resume", "language", "salary", "availability", "notice_period", "work_authorization"
]


class PreparationItemRead(BaseModel):
    key: PreparationKey
    complete: bool
    #: Optional items (work authorization) are shown but not counted.
    required: bool


class PreparationProgressRead(BaseModel):
    """A deterministic checklist: each item is complete exactly when its data
    is present. No workflow engine, no AI."""

    #: Required items complete / required items in total.
    completed: int
    total: int
    items: list[PreparationItemRead]


class ApplicationResumeRead(BaseModel):
    """The resume version the application builds on (a reference; the file
    and its profile stay in the resume tables)."""

    id: uuid.UUID
    version_number: int
    file_name: str
    status: ResumeStatus
    #: Whether the resume's profile is confirmed -- the trusted structured
    #: data later versions (tailored CV) build on.
    is_confirmed: bool


class ApplicationContextRead(ApplicationContextDocument):
    """The immutable context snapshot, with its checksum."""

    content_hash: str


class ApplicationRead(BaseModel):
    id: uuid.UUID
    status: ApplicationStatus
    #: The canonical job (for navigation); the job *as it was* is `context`.
    job_id: uuid.UUID
    user_job_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    # Preparation data
    salary_expectation: Decimal | None
    salary_currency: str | None
    mention_salary_in_letter: bool | None
    available_from: date | None
    notice_period: NoticePeriod | None
    work_authorization: WorkAuthorization | None
    application_language: str | None
    notes: str | None

    #: The job and source posting as they were when the application was started.
    context: ApplicationContextRead
    #: Whether the selected source posting is still listed by its source (live).
    #: `None` when the posting no longer exists.
    source_is_active: bool | None
    resume: ApplicationResumeRead | None
    #: Your current match for the job (V0.6); null when none was calculated.
    #: Never recalculated here.
    match: MatchSummaryRead | None
    progress: PreparationProgressRead
    #: Where the application is in its tracking lifecycle (V0.12).
    tracking: ApplicationTrackingRead


class ApplicationSummaryRead(BaseModel):
    """An application as shown in lists."""

    id: uuid.UUID
    status: ApplicationStatus
    job_id: uuid.UUID
    title: str
    company_name: str
    location: str | None
    source: str
    original_url: str
    created_at: datetime
    updated_at: datetime
    progress: PreparationProgressRead
    # Tracking (V0.12)
    applied_at: datetime | None
    last_status_change_at: datetime
    follow_up_at: datetime | None
    #: The latest V0.11 submission attempt, if any.
    submission: SubmissionSummaryRead | None


class ApplicationListResponse(BaseModel):
    items: list[ApplicationSummaryRead]
    total: int
    page: int
    page_size: int
    total_pages: int
