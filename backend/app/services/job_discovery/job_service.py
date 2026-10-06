"""Reading jobs and the user's relationship to them (list, detail, save).

Visibility: a user sees exactly the jobs they have a `UserJob` row for --
jobs their own discovery runs returned, or that they saved. Anything else is
a 404 (never 403), so job ids can't be probed across users.

Canonical job data and source postings are global; the only user-specific
data returned is the user's own `UserJob` state and whether *their* latest
discovery run saw each posting. Raw source payloads and matching internals
are never returned.
"""

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, String, UnaryExpression, and_, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.errors import NotFoundError
from app.models.discovery_run import DiscoveryRun, DiscoveryRunStatus
from app.models.job import Job
from app.models.job_match import JobMatch
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.job_source_posting import JobSourcePosting
from app.models.user import User
from app.models.user_job import UserJob
from app.schemas.jobs import (
    JobDetailRead,
    JobListResponse,
    JobSort,
    JobSourcePostingRead,
    JobSummaryRead,
    PostingStatus,
)
from app.services.applications.refs import application_refs
from app.services.job_discovery.deduplication.merger import order_postings
from app.services.matching import status as match_status


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _terms(value: str | None) -> list[str]:
    return [term.strip() for term in (value or "").split(",") if term.strip()][:20]


def posting_status(posting: JobSourcePosting, *, now: datetime | None = None) -> PostingStatus:
    if not posting.is_active:
        return PostingStatus.INACTIVE
    now = now or datetime.now(UTC)
    if now - posting.last_seen_at > timedelta(days=settings.JOB_POSTING_STALE_DAYS):
        return PostingStatus.STALE
    return PostingStatus.ACTIVE


def _ordered_postings(job: Job) -> list[JobSourcePosting]:
    """Preferred posting first, then the others in preference order."""
    postings = order_postings(list(job.source_postings))
    preferred = [p for p in postings if p.id == job.preferred_source_posting_id]
    return preferred + [p for p in postings if p.id != job.preferred_source_posting_id]


def to_summary(job: Job, user_job: UserJob) -> JobSummaryRead:
    postings = _ordered_postings(job)
    sources = list(dict.fromkeys(p.source for p in postings))
    return JobSummaryRead(
        id=job.id,
        title=job.title,
        company_name=job.company_name,
        location=job.location,
        work_model=job.work_model,
        employment_type=job.employment_type,
        seniority=job.seniority,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        salary_period=job.salary_period,
        technologies=list(job.technologies or []),
        source=sources[0] if sources else "unknown",
        sources=sources,
        source_count=len(postings),
        is_active=any(p.is_active for p in postings),
        canonical_url=job.canonical_url,
        posted_at=job.posted_at,
        discovered_at=job.discovered_at,
        saved=user_job.saved_at is not None,
        saved_at=user_job.saved_at,
    )


@dataclass(frozen=True)
class RunCoverage:
    """Which sources the user's latest finished discovery run received
    data from, and when it started."""

    started_at: datetime
    sources: frozenset[str]


def latest_run_coverage(db: Session, user: User) -> RunCoverage | None:
    run = db.execute(
        select(DiscoveryRun)
        .where(
            DiscoveryRun.user_id == user.id,
            DiscoveryRun.status.in_([DiscoveryRunStatus.COMPLETED, DiscoveryRunStatus.PARTIAL]),
        )
        .order_by(DiscoveryRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if run is None or run.started_at is None or not run.summary:
        return None
    sources = frozenset(
        entry["source"]
        for entry in run.summary.get("sources", [])
        if isinstance(entry, dict) and entry.get("status") != "failed" and "source" in entry
    )
    return RunCoverage(started_at=run.started_at, sources=sources)


def to_posting_read(
    posting: JobSourcePosting, job: Job, coverage: RunCoverage | None
) -> JobSourcePostingRead:
    seen: bool | None = None
    if coverage is not None and posting.source in coverage.sources:
        seen = posting.last_seen_at >= coverage.started_at
    return JobSourcePostingRead(
        id=posting.id,
        source=posting.source,
        source_job_id=posting.source_job_id,
        source_company_key=posting.source_company_key,
        original_url=posting.source_url or posting.canonical_source_url,
        canonical_url=posting.canonical_source_url,
        apply_url=posting.apply_url,
        title=posting.title,
        company_name=posting.company_name,
        location=posting.location,
        posted_at=posting.posted_at,
        first_seen_at=posting.first_seen_at,
        last_seen_at=posting.last_seen_at,
        is_active=posting.is_active,
        status=posting_status(posting),
        is_preferred=posting.id == job.preferred_source_posting_id,
        seen_in_latest_run=seen,
    )


def to_detail(
    job: Job, user_job: UserJob, coverage: RunCoverage | None = None
) -> JobDetailRead:
    postings = _ordered_postings(job)
    preferred = postings[0] if postings else None
    return JobDetailRead(
        **to_summary(job, user_job).model_dump(),
        locations=list(job.locations or []),
        country=job.country,
        city=job.city,
        department=job.department,
        industry=job.industry,
        description=job.description,
        requirements=list(job.requirements or []),
        responsibilities=list(job.responsibilities or []),
        salary_text=job.salary_text,
        source_job_id=preferred.source_job_id if preferred else None,
        source_company_key=preferred.source_company_key if preferred else None,
        source_url=preferred.source_url if preferred else None,
        apply_url=job.apply_url,
        source_updated_at=preferred.source_updated_at if preferred else None,
        last_seen_at=job.last_seen_at,
        first_discovered_by_you_at=user_job.first_discovered_at,
        preferred_source_posting_id=job.preferred_source_posting_id,
        source_postings=[to_posting_read(p, job, coverage) for p in postings],
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def detail(db: Session, user: User, job: Job, user_job: UserJob) -> JobDetailRead:
    result = to_detail(job, user_job, latest_run_coverage(db, user))
    result.match = match_status.summaries(db, user, [job]).get(job.id)
    result.application = application_refs(db, user, [job.id]).get(job.id)
    return result


def source_postings(db: Session, user: User, job_id: uuid.UUID) -> list[JobSourcePostingRead]:
    job, _ = get_visible_job(db, user, job_id)
    coverage = latest_run_coverage(db, user)
    return [to_posting_read(p, job, coverage) for p in _ordered_postings(job)]


def _visible(user: User) -> Select[tuple[Job, UserJob]]:
    # populate_existing: discovery runs update jobs from another session.
    return (
        select(Job, UserJob)
        .join(UserJob, and_(UserJob.job_id == Job.id, UserJob.user_id == user.id))
        .options(selectinload(Job.source_postings))
        .execution_options(populate_existing=True)
    )


def list_jobs(
    db: Session,
    user: User,
    *,
    page: int,
    page_size: int,
    sources: list[str] | None = None,
    q: str | None = None,
    location: str | None = None,
    work_models: list[RemotePreference] | None = None,
    employment_types: list[EmploymentTypeValue] | None = None,
    posted_after: date | None = None,
    saved_only: bool = False,
    sort: JobSort = JobSort.POSTED,
) -> JobListResponse:
    query = _visible(user)
    if sources:
        # A job matches when *any* of its postings is from one of the
        # sources, so multi-source jobs stay findable under each source.
        query = query.where(Job.source_postings.any(JobSourcePosting.source.in_(sources)))
    # Comma-separated terms are OR'ed, as in discovery criteria.
    if terms := _terms(q):
        query = query.where(
            or_(
                *(
                    column.ilike(_like(term), escape="\\")
                    for term in terms
                    for column in (
                        Job.title,
                        Job.company_name,
                        Job.description,
                        cast(Job.technologies, String),
                    )
                )
            )
        )
    if places := _terms(location):
        query = query.where(
            or_(
                *(
                    column.ilike(_like(place), escape="\\")
                    for place in places
                    for column in (Job.location, cast(Job.locations, String))
                )
            )
        )
    if work_models:
        query = query.where(Job.work_model.in_(work_models))
    if employment_types:
        query = query.where(Job.employment_type.in_(employment_types))
    if posted_after:
        since = datetime(posted_after.year, posted_after.month, posted_after.day, tzinfo=UTC)
        query = query.where(Job.posted_at >= since)
    if saved_only:
        query = query.where(UserJob.saved_at.is_not(None))

    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()

    order: list[UnaryExpression[Any]]
    if sort is JobSort.SAVED:
        order = [UserJob.saved_at.desc().nulls_last()]
    elif sort is JobSort.DISCOVERED:
        order = [UserJob.first_discovered_at.desc().nulls_last()]
    elif sort is JobSort.MATCH:
        # The user's own current results only.
        query = query.outerjoin(
            JobMatch,
            and_(JobMatch.job_id == Job.id, JobMatch.user_id == user.id, JobMatch.is_current),
        )
        order = [JobMatch.score.desc().nulls_last(), Job.posted_at.desc().nulls_last()]
    else:
        order = [Job.posted_at.desc().nulls_last(), Job.discovered_at.desc()]
    rows = db.execute(
        query.order_by(*order, Job.id).offset((page - 1) * page_size).limit(page_size)
    ).all()

    items = [to_summary(job, user_job) for job, user_job in rows]
    matches = match_status.summaries(db, user, [job for job, _ in rows])
    applications = application_refs(db, user, [job.id for job, _ in rows])
    for item in items:
        item.match = matches.get(item.id)
        item.application = applications.get(item.id)
    return JobListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


def get_visible_job(db: Session, user: User, job_id: uuid.UUID) -> tuple[Job, UserJob]:
    row = db.execute(_visible(user).where(Job.id == job_id)).one_or_none()
    if row is None:
        raise NotFoundError("Job not found.")
    return row[0], row[1]


def set_saved(db: Session, user: User, job_id: uuid.UUID, *, saved: bool) -> tuple[Job, UserJob]:
    job, user_job = get_visible_job(db, user, job_id)
    if saved and user_job.saved_at is None:
        user_job.saved_at = datetime.now(UTC)
    elif not saved:
        user_job.saved_at = None
    db.commit()
    db.refresh(user_job)
    return job, user_job
