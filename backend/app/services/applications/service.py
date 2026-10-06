"""Application workspace orchestration (V0.7): the database side.

    start(user, job)
        UserJob visibility check (404 otherwise -- never another user's job)
        -> existing application for the UserJob?  return it (idempotent)
        -> validate the chosen posting and resume (both must be the user's / the job's)
        -> Application (PREPARING) + immutable context snapshot, in one savepoint
        -> unique(user_job_id) decides concurrent starts: the loser returns the winner's

Ownership: an application belongs to its UserJob and so to one user. Every
query is scoped by `Application.user_id == user.id` (which the composite
foreign key guarantees equals the UserJob's owner); someone else's
application -- or one that doesn't exist -- is a 404.

Only application-specific data is ever written here. The user's profile,
preferences, the canonical Job and the match results are read, never
changed.
"""

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError, NotFoundError, ValidationAppError
from app.models.application import Application, ApplicationStatus
from app.models.application_context_snapshot import ApplicationContextSnapshot
from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from app.models.resume import Resume, ResumeStatus
from app.models.user import User
from app.schemas.application import (
    ApplicationContextRead,
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationPreparationFields,
    ApplicationRead,
    ApplicationResumeRead,
    ApplicationSummaryRead,
    ApplicationUpdate,
    NoticePeriod,
)
from app.services import resume_service
from app.services.applications.context import build_context, content_hash
from app.services.applications.progress import preparation_progress
from app.services.job_discovery.deduplication.merger import order_postings
from app.services.job_discovery.job_service import get_visible_job
from app.services.matching import status as match_status
from app.services.matching.candidate import confirmed_resume_profile

#: Preparation columns copied one-to-one from a request.
_PREPARATION_COLUMNS = (
    "salary_expectation",
    "salary_currency",
    "mention_salary_in_letter",
    "available_from",
    "work_authorization",
    "application_language",
    "notes",
)


@dataclass(frozen=True)
class StartResult:
    application: Application
    #: False when the user already had an application for the job.
    created: bool


# --- Loading -------------------------------------------------------------------------


def _owned(
    db: Session, user: User, application_id: uuid.UUID, *, lock: bool = False
) -> Application:
    """The user's own application (404 otherwise). `lock` takes a row lock for
    a read-modify-write. populate_existing: other sessions update applications."""
    query = (
        select(Application)
        .where(Application.id == application_id, Application.user_id == user.id)
        .options(selectinload(Application.context))
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update(of=Application)
    application = db.scalar(query)
    if application is None:
        raise NotFoundError("Application not found.")
    return application


def get_owned_application(db: Session, user: User, application_id: uuid.UUID) -> Application:
    return _owned(db, user, application_id)


def _by_user_job(db: Session, user_job_id: uuid.UUID) -> Application | None:
    return db.scalar(
        select(Application)
        .where(Application.user_job_id == user_job_id)
        .options(selectinload(Application.context))
        .execution_options(populate_existing=True)
    )


# --- Choosing what an application points at ----------------------------------------------


def _select_posting(job: Job, posting_id: uuid.UUID | None) -> JobSourcePosting | None:
    """The source posting to apply through: the one asked for (it must be one
    of the job's), else the job's preferred posting."""
    postings = order_postings(list(job.source_postings))
    if posting_id is not None:
        for posting in postings:
            if posting.id == posting_id:
                return posting
        raise ValidationAppError(
            "That source posting doesn't belong to this job.", {"field": "source_posting_id"}
        )
    for posting in postings:
        if posting.id == job.preferred_source_posting_id:
            return posting
    return postings[0] if postings else None


def _owned_resume(db: Session, user: User, resume_id: uuid.UUID) -> Resume:
    resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id))
    if resume is None:
        # Unknown and other people's resumes look the same.
        raise ValidationAppError("Resume not found.", {"field": "resume_id"})
    return resume


def _default_resume(db: Session, user: User) -> Resume | None:
    """The resume a new application builds on when none is chosen: the one
    with the user's most recently confirmed Resume Profile -- the same the
    match uses -- else their latest upload (it may still need review), else
    none."""
    profile = confirmed_resume_profile(db, user)
    if profile is not None:
        resume = db.get(Resume, profile.resume_id)
        if resume is not None and resume.user_id == user.id:
            return resume
    return resume_service.get_resume_for_user(db, user)


# --- Preparation data ---------------------------------------------------------------------


def _apply_preparation(
    application: Application, data: ApplicationPreparationFields, fields: set[str]
) -> None:
    """Copy the provided fields. `fields` are the ones the client sent: an
    omitted field is left alone, an explicit null clears it."""
    for name in _PREPARATION_COLUMNS:
        if name in fields:
            setattr(application, name, getattr(data, name))
    if "notice_period" in fields:
        notice = data.notice_period
        application.notice_period_value = notice.value if notice else None
        application.notice_period_unit = notice.unit if notice else None


def _validate_preparation(application: Application) -> None:
    """Rules about the combined state (a partial update can't see them)."""
    if application.salary_expectation is not None and application.salary_currency is None:
        raise ValidationAppError(
            "Enter the currency (for example EUR) together with the salary expectation.",
            {"field": "salary_currency"},
        )


# --- Start ----------------------------------------------------------------------------------


def start_application(
    db: Session, user: User, job_id: uuid.UUID, request: ApplicationCreate | None = None
) -> StartResult:
    """Create the user's application for one of *their* jobs, or return the
    one that already exists (the request body is then ignored: starting never
    changes an existing application). Commits."""
    request = request or ApplicationCreate()
    job, user_job = get_visible_job(db, user, job_id)

    existing = _by_user_job(db, user_job.id)
    if existing is not None:
        return StartResult(existing, created=False)

    posting = _select_posting(job, request.source_posting_id)
    resume = (
        _owned_resume(db, user, request.resume_id)
        if request.resume_id is not None
        else _default_resume(db, user)
    )

    application = Application(
        user_id=user.id,
        user_job_id=user_job.id,
        job_id=job.id,
        status=ApplicationStatus.PREPARING,
        source_posting_id=posting.id if posting is not None else None,
        resume_id=resume.id if resume is not None else None,
    )
    _apply_preparation(application, request, request.model_fields_set)
    _validate_preparation(application)

    document = build_context(job, posting, captured_at=datetime.now(UTC))
    application.context = ApplicationContextSnapshot(
        schema_version=document.schema_version,
        content_hash=content_hash(document),
        document=document.model_dump(mode="json"),
    )
    try:
        with db.begin_nested():
            db.add(application)
            db.flush()
    except IntegrityError:
        # Another request started the same application first: unique(user_job_id)
        # decided, and the loser opens the winner's.
        existing = _by_user_job(db, user_job.id)
        if existing is None:
            raise
        return StartResult(existing, created=False)
    db.commit()
    return StartResult(application, created=True)


# --- Update ---------------------------------------------------------------------------------


def update_application(
    db: Session, user: User, application_id: uuid.UUID, patch: ApplicationUpdate
) -> Application:
    """Update the application's own preparation data (and which of the user's
    resumes it builds on). Nothing else is writable: not the status, the job,
    the context snapshot, the user's profile or the match. Commits."""
    application = _owned(db, user, application_id, lock=True)
    fields = patch.model_fields_set
    try:
        _apply_preparation(application, patch, fields)
        if "resume_id" in fields and patch.resume_id != application.resume_id:
            # Re-selecting the current resume changes nothing; only an explicit
            # choice of another one does.
            assert patch.resume_id is not None  # the schema rejects null
            application.resume_id = _owned_resume(db, user, patch.resume_id).id
        _validate_preparation(application)
    except AppError:
        db.rollback()
        raise
    db.commit()
    return application


# --- Read -----------------------------------------------------------------------------------


def _notice_period(application: Application) -> NoticePeriod | None:
    if application.notice_period_value is None or application.notice_period_unit is None:
        return None
    return NoticePeriod(value=application.notice_period_value, unit=application.notice_period_unit)


def _snapshot(application: Application) -> ApplicationContextSnapshot:
    if application.context is None:
        # Applications are created together with their snapshot, and the V0.7
        # migration gave every V0 application one.
        raise RuntimeError(f"Application {application.id} has no context snapshot")
    return application.context


def _resume_read(resume: Resume | None) -> ApplicationResumeRead | None:
    if resume is None:
        return None
    return ApplicationResumeRead(
        id=resume.id,
        version_number=resume.version_number,
        file_name=resume.file_name,
        status=resume.status,
        is_confirmed=resume.status is ResumeStatus.CONFIRMED,
    )


def to_read(db: Session, user: User, application: Application) -> ApplicationRead:
    """The full application: preparation data, the context it was started
    with, the selected resume, and the current match (read, never
    recalculated)."""
    snapshot = _snapshot(application)
    job = application.user_job.job
    posting = application.source_posting
    return ApplicationRead(
        id=application.id,
        status=application.status,
        job_id=job.id,
        user_job_id=application.user_job_id,
        created_at=application.created_at,
        updated_at=application.updated_at,
        salary_expectation=application.salary_expectation,
        salary_currency=application.salary_currency,
        mention_salary_in_letter=application.mention_salary_in_letter,
        available_from=application.available_from,
        notice_period=_notice_period(application),
        work_authorization=application.work_authorization,
        application_language=application.application_language,
        notes=application.notes,
        context=ApplicationContextRead.model_validate(
            {**snapshot.read_document().model_dump(), "content_hash": snapshot.content_hash}
        ),
        source_is_active=posting.is_active if posting is not None else None,
        resume=_resume_read(application.resume),
        match=match_status.summaries(db, user, [job]).get(job.id),
        progress=preparation_progress(application),
    )


def _to_summary(application: Application) -> ApplicationSummaryRead:
    document = _snapshot(application).read_document()
    return ApplicationSummaryRead(
        id=application.id,
        status=application.status,
        job_id=document.job.job_id,
        title=document.job.title,
        company_name=document.job.company_name,
        location=document.job.location,
        source=document.source.source,
        original_url=document.source.original_url,
        created_at=application.created_at,
        updated_at=application.updated_at,
        progress=preparation_progress(application),
    )


def list_applications(
    db: Session, user: User, *, page: int, page_size: int
) -> ApplicationListResponse:
    """The user's own applications, most recently updated first."""
    base = select(Application).where(Application.user_id == user.id)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.scalars(
        base.options(selectinload(Application.context))
        .order_by(Application.updated_at.desc(), Application.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .execution_options(populate_existing=True)
    ).all()
    return ApplicationListResponse(
        items=[_to_summary(application) for application in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
    )
