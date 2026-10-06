"""Request/response schemas for Job Discovery (V0.3) and Job Intelligence (V0.4)."""

import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.discovery_run import DiscoveryRunStatus
from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel

_MAX_TERM_LENGTH = 100
_MAX_TERMS = 20


def _clean_terms(values: list[str]) -> list[str]:
    """Strip, drop empties and case-insensitive duplicates, keep order."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        term = " ".join(value.split())
        if not term:
            continue
        if len(term) > _MAX_TERM_LENGTH:
            raise ValueError(f"Search terms must be at most {_MAX_TERM_LENGTH} characters long.")
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            cleaned.append(term)
    return cleaned


class SearchFilter(str, enum.Enum):
    """A criterion a discovery run can be narrowed by.

    Each source declares which of these it applies itself (natively). The
    discovery service applies the rest deterministically after fetching, when
    the canonical job data allows it, and reports anything it could not apply.
    """

    KEYWORDS = "keywords"
    JOB_TITLES = "job_titles"
    LOCATIONS = "locations"
    WORK_MODELS = "work_models"
    EMPLOYMENT_TYPES = "employment_types"
    INDUSTRIES = "industries"
    SALARY = "salary"
    POSTED_AFTER = "posted_after"


class JobSearchCriteria(BaseModel):
    """What to look for in a discovery run.

    Lists are OR'ed within themselves (any keyword, any location, ...);
    different criteria are AND'ed. Not every source supports every criterion;
    the discovery summary reports, per source, who applied each one.
    """

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list, max_length=_MAX_TERMS)
    job_titles: list[str] = Field(default_factory=list, max_length=_MAX_TERMS)
    locations: list[str] = Field(default_factory=list, max_length=_MAX_TERMS)
    work_models: list[RemotePreference] = Field(default_factory=list)
    employment_types: list[EmploymentTypeValue] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list, max_length=_MAX_TERMS)
    # Annual figures in `salary_currency`, the same convention as Job
    # Preferences.
    salary_min: Decimal | None = Field(default=None, ge=0, le=Decimal("10000000"))
    salary_max: Decimal | None = Field(default=None, ge=0, le=Decimal("10000000"))
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    posted_after: date | None = None
    # Source names to query; None means every enabled source.
    sources: list[str] | None = Field(default=None, max_length=20)
    # Pagination over each source's matching results (newest first).
    page: int = Field(default=1, ge=1, le=100)
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("keywords", "job_titles", "locations", "industries")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        return _clean_terms(values)

    @field_validator("work_models", "employment_types")
    @classmethod
    def _dedupe_enums(cls, values: list[Any]) -> list[Any]:
        return list(dict.fromkeys(values))

    @field_validator("sources")
    @classmethod
    def _clean_sources(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return list(dict.fromkeys(v.strip().lower() for v in values if v.strip()))

    @field_validator("salary_currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        if not value.isalpha():
            raise ValueError("salary_currency must be a 3-letter ISO currency code.")
        return value

    @field_validator("posted_after")
    @classmethod
    def _not_in_future(cls, value: date | None) -> date | None:
        if value is not None and value > datetime.now(UTC).date():
            raise ValueError("posted_after cannot be in the future.")
        return value

    @model_validator(mode="after")
    def _salary_range(self) -> "JobSearchCriteria":
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_max < self.salary_min
        ):
            raise ValueError("salary_max must be greater than or equal to salary_min")
        return self

    def requested_filters(self) -> set[SearchFilter]:
        requested: set[SearchFilter] = set()
        if self.keywords:
            requested.add(SearchFilter.KEYWORDS)
        if self.job_titles:
            requested.add(SearchFilter.JOB_TITLES)
        if self.locations:
            requested.add(SearchFilter.LOCATIONS)
        if self.work_models:
            requested.add(SearchFilter.WORK_MODELS)
        if self.employment_types:
            requested.add(SearchFilter.EMPLOYMENT_TYPES)
        if self.industries:
            requested.add(SearchFilter.INDUSTRIES)
        if self.salary_min is not None or self.salary_max is not None:
            requested.add(SearchFilter.SALARY)
        if self.posted_after is not None:
            requested.add(SearchFilter.POSTED_AFTER)
        return requested


class DiscoverRequest(BaseModel):
    """Body of `POST /jobs/discover`.

    Without `criteria`, the run uses criteria derived from the user's Job
    Preference Profile (see `GET /jobs/criteria/defaults`).
    """

    model_config = ConfigDict(extra="forbid")

    criteria: JobSearchCriteria | None = None


# --- Discovery summary ---------------------------------------------------------


class FilterAppliedBy(str, enum.Enum):
    SOURCE = "source"  # the source applied it natively
    JOBPILOT = "jobpilot"  # JobPilot applied it to the fetched postings
    NOT_APPLIED = "not_applied"  # nobody could apply it; results are unfiltered


class FilterReport(BaseModel):
    filter: SearchFilter
    applied_by: FilterAppliedBy


class SourceRunStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    # Some of the source's boards failed, others worked.
    PARTIAL = "partial"
    FAILED = "failed"


class SourceIssueRead(BaseModel):
    code: str
    message: str
    # Which board/site the issue concerns, when relevant.
    target: str | None = None


class DiscoveryCounts(BaseModel):
    """Counters of a discovery run, per source and in total.

    Per source: fetched = invalid + duplicates + filtered_out + beyond_limit
    + (created + merged + updated + unchanged). The last four count source
    *postings* stored for the user. `discovered` counts distinct canonical
    *jobs*; the run total is not the sum of the sources' values when sources
    share jobs.
    """

    #: Postings received from the source.
    fetched: int = 0
    #: Postings rejected by validation (e.g. missing title or URL).
    invalid: int = 0
    #: Valid postings that didn't match the search criteria.
    filtered_out: int = 0
    #: Matching postings beyond the requested page/limit.
    beyond_limit: int = 0
    #: The same posting (same source, same id) returned more than once in one run.
    duplicates: int = 0
    #: New postings that became a new canonical job (nothing matched).
    created: int = 0
    #: New postings attached to an existing canonical job found by another
    #: source (EXACT or HIGH_CONFIDENCE match).
    merged: int = 0
    #: Known postings whose content changed (the job was refreshed).
    updated: int = 0
    #: Known postings seen again without changes.
    unchanged: int = 0
    #: Stored postings whose canonical job is listed by two or more sources
    #: (whether merged in this run or earlier).
    cross_source_matches: int = 0
    #: New postings that resembled an existing job (POSSIBLE_MATCH) but were
    #: deliberately kept as a separate job. Never merged automatically.
    possible_matches: int = 0
    #: Postings fetched but not stored: invalid + duplicates + filtered_out +
    #: beyond_limit.
    skipped: int = 0
    #: Problems reported by the source (failed boards, unreachable source)
    #: and postings that could not be stored.
    error_count: int = 0
    #: HTTP requests retried after a transient failure (timeouts, 429, 5xx).
    #: Retries are bounded; a request that still fails is reported as an error.
    retried_requests: int = 0
    #: Distinct canonical jobs stored for the user.
    discovered: int = 0


class SourceRunSummary(DiscoveryCounts):
    source: str
    display_name: str
    status: SourceRunStatus
    errors: list[SourceIssueRead] = Field(default_factory=list)
    filters: list[FilterReport] = Field(default_factory=list)


class DiscoverySummary(DiscoveryCounts):
    sources: list[SourceRunSummary] = Field(default_factory=list)


class DiscoveryRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: DiscoveryRunStatus
    is_active: bool
    criteria: JobSearchCriteria
    summary: DiscoverySummary | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


# --- Sources ---------------------------------------------------------------------


class JobSourceRead(BaseModel):
    name: str
    display_name: str
    acquisition_method: str
    description: str
    enabled: bool
    #: How many boards/sites are configured for the source.
    target_count: int
    native_filters: list[SearchFilter]
    #: Filters JobPilot can apply to this source's postings after fetching.
    jobpilot_filters: list[SearchFilter]


# --- Jobs --------------------------------------------------------------------------


class PostingStatus(str, enum.Enum):
    #: Listed by its source within the freshness window.
    ACTIVE = "active"
    #: Not seen by any discovery run for a while; may or may not still be open.
    STALE = "stale"
    #: Its source's complete board listing no longer contains it.
    INACTIVE = "inactive"


class JobSourcePostingRead(BaseModel):
    """Where a job was found: one source posting. Source-specific, as the
    source stated it. Raw payloads and matching internals are never exposed."""

    id: uuid.UUID
    source: str
    source_job_id: str | None
    #: Board/site within the source (e.g. a Greenhouse board token).
    source_company_key: str | None
    #: The posting URL exactly as the source returned it (falls back to the
    #: canonical URL when the source gave none).
    original_url: str
    #: The same URL with tracking parameters and fragment removed.
    canonical_url: str
    apply_url: str | None
    #: Title, company and location as written by this source.
    title: str
    company_name: str
    location: str | None
    posted_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool
    status: PostingStatus
    #: The posting JobPilot's canonical data comes from and that application
    #: automation will use.
    is_preferred: bool
    #: Whether your latest finished discovery run received this posting from
    #: its source. None if that run didn't query this source (or you have no
    #: finished run).
    seen_in_latest_run: bool | None


class JobSummaryRead(BaseModel):
    """A job as shown in the list. The canonical job plus this user's state."""

    id: uuid.UUID
    title: str
    company_name: str
    location: str | None
    work_model: RemotePreference | None
    employment_type: EmploymentTypeValue | None
    seniority: SeniorityLevel | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_period: SalaryPeriod | None
    technologies: list[str]
    #: Source of the preferred posting.
    source: str
    #: Every source the job was found on, preferred first (V0.4).
    sources: list[str]
    #: Number of source postings (one per source today).
    source_count: int
    #: False when no source lists the job any more.
    is_active: bool
    canonical_url: str
    posted_at: datetime | None
    discovered_at: datetime
    saved: bool
    saved_at: datetime | None


class JobDetailRead(JobSummaryRead):
    locations: list[str]
    country: str | None
    #: Canonical city from JobPilot's location vocabulary (e.g. "Cologne"
    #: for "Köln"), when recognized.
    city: str | None
    department: str | None
    industry: str | None
    description: str | None
    requirements: list[str]
    responsibilities: list[str]
    #: Salary as the posting wrote it (also when too ambiguous to structure).
    salary_text: str | None
    # Identity of the preferred posting (kept for V0.3 compatibility; see
    # `source_postings` for all of them).
    source_job_id: str | None
    source_company_key: str | None
    source_url: str | None
    apply_url: str | None
    source_updated_at: datetime | None
    last_seen_at: datetime
    first_discovered_by_you_at: datetime | None
    preferred_source_posting_id: uuid.UUID | None
    source_postings: list[JobSourcePostingRead]
    created_at: datetime
    updated_at: datetime


class JobListResponse(BaseModel):
    items: list[JobSummaryRead]
    total: int
    page: int
    page_size: int
    total_pages: int


class JobSort(str, enum.Enum):
    POSTED = "posted"  # newest posting first
    DISCOVERED = "discovered"  # most recently discovered first
    SAVED = "saved"  # most recently saved first
