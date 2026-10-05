"""Reading jobs and the user's relationship to them (list, detail, save).

Visibility: a user sees exactly the jobs they have a `UserJob` row for --
jobs their own discovery runs returned, or that they saved. Anything else is
a 404 (never 403), so job ids can't be probed across users.
"""

import math
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, String, UnaryExpression, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.job import Job
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.user import User
from app.models.user_job import UserJob
from app.schemas.jobs import JobDetailRead, JobListResponse, JobSort, JobSummaryRead


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def to_summary(job: Job, user_job: UserJob) -> JobSummaryRead:
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
        source=job.source,
        canonical_url=job.canonical_url,
        posted_at=job.posted_at,
        discovered_at=job.discovered_at,
        saved=user_job.saved_at is not None,
        saved_at=user_job.saved_at,
    )


def to_detail(job: Job, user_job: UserJob) -> JobDetailRead:
    return JobDetailRead(
        **to_summary(job, user_job).model_dump(),
        locations=list(job.locations or []),
        country=job.country,
        department=job.department,
        industry=job.industry,
        description=job.description,
        requirements=list(job.requirements or []),
        responsibilities=list(job.responsibilities or []),
        source_job_id=job.source_job_id,
        source_company_key=job.source_company_key,
        source_url=job.source_url,
        apply_url=job.apply_url,
        source_updated_at=job.source_updated_at,
        last_seen_at=job.last_seen_at,
        first_discovered_by_you_at=user_job.first_discovered_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _visible(user: User) -> Select[tuple[Job, UserJob]]:
    # populate_existing: discovery runs update jobs from another session.
    return (
        select(Job, UserJob)
        .join(UserJob, and_(UserJob.job_id == Job.id, UserJob.user_id == user.id))
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
        query = query.where(Job.source.in_(sources))
    if q and q.strip():
        pattern = _like(q.strip())
        query = query.where(
            or_(
                Job.title.ilike(pattern, escape="\\"),
                Job.company_name.ilike(pattern, escape="\\"),
                Job.description.ilike(pattern, escape="\\"),
                cast(Job.technologies, String).ilike(pattern, escape="\\"),
            )
        )
    if location and location.strip():
        pattern = _like(location.strip())
        query = query.where(
            or_(
                Job.location.ilike(pattern, escape="\\"),
                cast(Job.locations, String).ilike(pattern, escape="\\"),
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
    else:
        order = [Job.posted_at.desc().nulls_last(), Job.discovered_at.desc()]
    rows = db.execute(
        query.order_by(*order, Job.id).offset((page - 1) * page_size).limit(page_size)
    ).all()

    return JobListResponse(
        items=[to_summary(job, user_job) for job, user_job in rows],
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
