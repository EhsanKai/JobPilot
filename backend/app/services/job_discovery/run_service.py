"""Discovery runs: start (in the request), execute (background), read (polling).

    start_run()         fast, inside POST /jobs/discover
        lock user -> reject if a run is active -> validate sources
        -> create PENDING DiscoveryRun
    execute_run()       FastAPI background task, own DB session
        RUNNING -> JobDiscoveryService.discover() -> COMPLETED / PARTIAL / FAILED

Same pattern as Resume Intelligence processing (V0.2): no Redis/Celery.
Moving execution to a worker later only changes how `execute_run` is
scheduled.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.models.discovery_run import IN_PROGRESS_RUN_STATUSES, DiscoveryRun, DiscoveryRunStatus
from app.models.user import User
from app.schemas.jobs import DiscoveryRunRead, DiscoverySummary, JobSearchCriteria
from app.services import preferences_service
from app.services.job_discovery.discovery_service import JobDiscoveryService, overall_succeeded
from app.services.job_discovery.registry import JobSourceRegistry

logger = get_logger(__name__)

GENERIC_FAILURE_MESSAGE = "Job discovery failed unexpectedly. Please try again."


def _now() -> datetime:
    return datetime.now(UTC)


def is_active(run: DiscoveryRun) -> bool:
    if run.status not in IN_PROGRESS_RUN_STATUSES:
        return False
    stale_after = timedelta(minutes=settings.JOB_DISCOVERY_STALE_MINUTES)
    return _now() - (run.started_at or run.created_at) < stale_after


def to_read(run: DiscoveryRun) -> DiscoveryRunRead:
    return DiscoveryRunRead(
        id=run.id,
        status=run.status,
        is_active=is_active(run),
        criteria=JobSearchCriteria.model_validate(run.criteria),
        summary=DiscoverySummary.model_validate(run.summary) if run.summary else None,
        error_code=run.error_code,
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
    )


def criteria_from_preferences(db: Session, user: User) -> JobSearchCriteria:
    """Default search criteria derived from the user's Job Preference Profile."""
    preferences = preferences_service.get_or_create_preferences(db, user)
    return JobSearchCriteria(
        job_titles=[t.title for t in preferences.job_titles][:20],
        locations=[loc.location for loc in preferences.locations][:20],
        work_models=[w.work_model for w in preferences.work_models],
        employment_types=[e.employment_type for e in preferences.employment_types],
        industries=[i.industry for i in preferences.industries][:20],
        salary_min=preferences.minimum_salary,
        salary_currency=preferences.salary_currency,
    )


def get_owned_run(db: Session, user: User, run_id: uuid.UUID) -> DiscoveryRun:
    # populate_existing: the run is written by a background task in another
    # session; never serve a stale copy from this session's identity map.
    run = db.execute(
        select(DiscoveryRun)
        .where(DiscoveryRun.id == run_id, DiscoveryRun.user_id == user.id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("Discovery run not found.")
    return run


def get_latest_run(db: Session, user: User) -> DiscoveryRun | None:
    return db.execute(
        select(DiscoveryRun)
        .where(DiscoveryRun.user_id == user.id)
        .order_by(DiscoveryRun.created_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def start_run(
    db: Session, user: User, registry: JobSourceRegistry, criteria: JobSearchCriteria
) -> DiscoveryRun:
    # Serialize run creation per user so two clicks can't start two runs.
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    active = db.execute(
        select(DiscoveryRun).where(
            DiscoveryRun.user_id == user.id,
            DiscoveryRun.status.in_(IN_PROGRESS_RUN_STATUSES),
        )
    ).scalars().all()
    for run in active:
        if is_active(run):
            raise ConflictError(
                "Job discovery is already running. Please wait for it to finish.",
                details={"run_id": str(run.id)},
            )
        # Abandoned (e.g. server restart mid-run).
        run.status = DiscoveryRunStatus.FAILED
        run.error_code = "ABANDONED"
        run.error_message = "This discovery run was interrupted."
        run.finished_at = _now()

    if not registry.select(criteria.sources):
        raise ValidationAppError("No job sources are enabled.")

    run = DiscoveryRun(
        user_id=user.id,
        status=DiscoveryRunStatus.PENDING,
        criteria=criteria.model_dump(mode="json"),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info("Discovery run queued run_id=%s user_id=%s", run.id, user.id)
    return run


def execute_run(
    run_id: uuid.UUID,
    *,
    session_factory: sessionmaker[Session],
    registry: JobSourceRegistry,
) -> None:
    """Background task. Never raises: every outcome is recorded on the run."""
    with session_factory() as db:
        run = db.get(DiscoveryRun, run_id)
        if run is None or run.status != DiscoveryRunStatus.PENDING:
            return
        run.status = DiscoveryRunStatus.RUNNING
        run.started_at = _now()
        db.commit()

        try:
            user = db.get(User, run.user_id)
            if user is None:
                raise LookupError("user no longer exists")
            criteria = JobSearchCriteria.model_validate(run.criteria)
            summary = JobDiscoveryService(registry).discover(db, user, criteria)
        except Exception as exc:  # noqa: BLE001 -- recorded on the run, never re-raised
            db.rollback()
            logger.error("Discovery run failed run_id=%s error=%s", run_id, type(exc).__name__)
            run = db.get(DiscoveryRun, run_id)
            if run is None:
                return
            run.status = DiscoveryRunStatus.FAILED
            run.error_code = "DISCOVERY_FAILED"
            run.error_message = GENERIC_FAILURE_MESSAGE
            run.finished_at = _now()
            db.commit()
            return

        any_ok, any_failed = overall_succeeded(summary)
        if not any_ok:
            run.status = DiscoveryRunStatus.FAILED
            run.error_code = "ALL_SOURCES_FAILED"
            run.error_message = "No job source could be reached. Please try again later."
        elif any_failed:
            run.status = DiscoveryRunStatus.PARTIAL
        else:
            run.status = DiscoveryRunStatus.COMPLETED
        run.summary = summary.model_dump(mode="json")
        run.finished_at = _now()
        db.commit()
        logger.info(
            "Discovery run finished run_id=%s status=%s discovered=%d",
            run_id,
            run.status.value,
            summary.discovered,
        )
