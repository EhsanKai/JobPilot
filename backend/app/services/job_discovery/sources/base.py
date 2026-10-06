"""The `JobSource` contract every job-source adapter implements.

The rest of JobPilot (discovery service, persistence, API) depends only on
this module, never on a specific provider. Adding a source means writing one
`JobSource` subclass and registering it (see `app.services.job_discovery.registry`).

Sources differ in *how* they acquire postings, which `AcquisitionMethod`
records, but they all produce the same output: `RawJob`s.

    JobSource
       +-- ATS job boards  (Greenhouse, Lever)        AcquisitionMethod.ATS
       +-- job-search APIs (future, e.g. a public     AcquisitionMethod.API
       |                    employment-agency API)
       +-- web pages       (future, structured job    AcquisitionMethod.WEB
                            pages / schema.org JobPosting)
"""

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.schemas.jobs import JobSearchCriteria, SearchFilter
from app.services.job_discovery.raw_job import RawJob


class AcquisitionMethod(str, enum.Enum):
    #: A public applicant-tracking-system job board (one board per company).
    ATS = "ats"
    #: A job-search API spanning many employers.
    API = "api"
    #: Structured data from job pages on the web.
    WEB = "web"


class SourceError(Exception):
    """A failure fetching from a source. `code` and `message` are user-safe:
    they never contain credentials, response bodies or stack traces."""

    code = "SOURCE_ERROR"
    default_message = "The job source returned an error."

    def __init__(self, message: str | None = None, *, target: str | None = None) -> None:
        self.message = message or self.default_message
        self.target = target
        super().__init__(self.message)


class SourceUnavailableError(SourceError):
    code = "SOURCE_UNAVAILABLE"
    default_message = "Unable to reach the job source."


class SourceTimeoutError(SourceError):
    code = "SOURCE_TIMEOUT"
    default_message = "The job source took too long to respond."


class SourceRateLimitedError(SourceError):
    code = "SOURCE_RATE_LIMITED"
    default_message = "The job source is rate limiting requests. Please try again later."


class SourceHTTPError(SourceError):
    code = "SOURCE_HTTP_ERROR"
    default_message = "The job source returned an error response."


class SourceNotFoundError(SourceError):
    code = "SOURCE_NOT_FOUND"
    default_message = "The job board could not be found at the source."


class SourceResponseError(SourceError):
    code = "SOURCE_INVALID_RESPONSE"
    default_message = "The job source returned data JobPilot could not read."


class SourceNotConfiguredError(SourceError):
    code = "SOURCE_NOT_CONFIGURED"
    default_message = "This job source has no boards configured."


@dataclass(frozen=True)
class SourceIssue:
    """A non-fatal problem within one source run (e.g. one board failed)."""

    code: str
    message: str
    target: str | None = None

    @classmethod
    def from_error(cls, error: SourceError) -> "SourceIssue":
        return cls(code=error.code, message=error.message, target=error.target)


@dataclass
class SourceSearchResult:
    """What `JobSource.search` returns.

    `jobs` holds every posting the source could read. `issues` lists the
    parts that failed (e.g. one of several company boards); one failing
    board never discards the others. When *every* target failed
    (`targets_failed == targets_attempted`) the discovery service reports the
    source as failed, so "no matching jobs" is never confused with "couldn't
    reach the source". A source may also raise `SourceError` when it can't
    run at all.
    """

    jobs: list[RawJob] = field(default_factory=list)
    issues: list[SourceIssue] = field(default_factory=list)
    #: Number of boards/sites/endpoints the source attempted, and how many
    #: of them failed outright.
    targets_attempted: int = 0
    targets_failed: int = 0
    #: Items in a response the adapter couldn't interpret at all (e.g. not a
    #: JSON object). Counted as invalid; never silently dropped.
    unreadable_items: int = 0
    #: Boards/sites (`RawJob.source_company_key`) whose *complete* listing is
    #: in `jobs`: read without error, nothing unreadable, not cut off by a
    #: cap. Only for these may JobPilot conclude that a stored posting
    #: missing from `jobs` is no longer listed (V0.4 freshness). Sources
    #: that can't guarantee completeness leave it empty.
    complete_targets: list[str] = field(default_factory=list)
    #: HTTP requests that were retried after a transient failure (V0.4.1).
    retried_requests: int = 0


class JobSource(ABC):
    """Contract for a job source adapter.

    Subclasses set the class attributes and implement `search`. They are
    responsible for converting their source's format into `RawJob` and must
    not apply normalization rules themselves.
    """

    #: Stable registry name, persisted as `jobs.source`. Never change it for
    #: an existing source, or deduplication against stored jobs breaks.
    name: str
    display_name: str
    description: str = ""
    acquisition_method: AcquisitionMethod
    #: Filters this source applies itself, in `search`. Everything else is
    #: either applied by JobPilot after fetching or reported as not applied
    #: -- a source must never claim a filter it doesn't actually apply.
    native_filters: frozenset[SearchFilter] = frozenset()

    @property
    def target_count(self) -> int:
        """How many boards/sites/endpoints this source is configured to read."""
        return 1

    @property
    def is_configured(self) -> bool:
        return self.target_count > 0

    @abstractmethod
    async def search(self, criteria: JobSearchCriteria) -> SourceSearchResult:
        """Fetch postings for `criteria`.

        Raises `SourceError` when the source as a whole can't be read.
        """
        raise NotImplementedError
