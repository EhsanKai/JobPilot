import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user
from app.db.session import get_db, get_session_factory
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.user import User
from app.schemas.jobs import (
    DiscoverRequest,
    DiscoveryRunRead,
    JobDetailRead,
    JobListResponse,
    JobSearchCriteria,
    JobSort,
    JobSourcePostingRead,
    JobSourceRead,
)
from app.services.job_discovery import job_service, run_service
from app.services.job_discovery.filters import JOBPILOT_EVALUABLE_FILTERS
from app.services.job_discovery.registry import JobSourceRegistry, get_job_source_registry

router = APIRouter(prefix="/jobs", tags=["jobs"])


# --- Sources & criteria ------------------------------------------------------------
# Static paths are declared before /{job_id}.


@router.get("/sources", response_model=list[JobSourceRead])
def list_sources(
    current_user: User = Depends(get_current_user),
    registry: JobSourceRegistry = Depends(get_job_source_registry),
) -> list[JobSourceRead]:
    """The job sources JobPilot knows about. Board names and counts only --
    never configuration secrets (the implemented sources need none)."""
    return [
        JobSourceRead(
            name=source.name,
            display_name=source.display_name,
            acquisition_method=source.acquisition_method.value,
            description=source.description,
            enabled=registry.is_enabled(source.name),
            target_count=source.target_count,
            native_filters=sorted(source.native_filters, key=lambda f: f.value),
            jobpilot_filters=sorted(
                JOBPILOT_EVALUABLE_FILTERS - source.native_filters, key=lambda f: f.value
            ),
        )
        for source in registry.all()
    ]


@router.get("/criteria/defaults", response_model=JobSearchCriteria)
def default_criteria(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> JobSearchCriteria:
    """Search criteria derived from the user's Job Preference Profile."""
    return run_service.criteria_from_preferences(db, current_user)


# --- Discovery ------------------------------------------------------------------------


@router.post("/discover", response_model=DiscoveryRunRead, status_code=status.HTTP_202_ACCEPTED)
def discover_jobs(
    background_tasks: BackgroundTasks,
    request: DiscoverRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    registry: JobSourceRegistry = Depends(get_job_source_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> DiscoveryRunRead:
    """Start a discovery run and return immediately (202). Poll
    `GET /jobs/discover/runs/{id}` for progress and the summary. Without
    criteria, the user's job preferences are used."""
    criteria = request.criteria if request and request.criteria else None
    if criteria is None:
        criteria = run_service.criteria_from_preferences(db, current_user)
    run = run_service.start_run(db, current_user, registry, criteria)
    background_tasks.add_task(
        run_service.execute_run, run.id, session_factory=session_factory, registry=registry
    )
    return run_service.to_read(run)


@router.get("/discover/runs/latest", response_model=DiscoveryRunRead | None)
def latest_discovery_run(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> DiscoveryRunRead | None:
    run = run_service.get_latest_run(db, current_user)
    return run_service.to_read(run) if run else None


@router.get("/discover/runs/{run_id}", response_model=DiscoveryRunRead)
def get_discovery_run(
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiscoveryRunRead:
    return run_service.to_read(run_service.get_owned_run(db, current_user, run_id))


# --- Jobs -------------------------------------------------------------------------------


@router.get("", response_model=JobListResponse)
def list_jobs(
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    source: Annotated[list[str] | None, Query(max_length=20)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    location: Annotated[str | None, Query(max_length=200)] = None,
    work_model: Annotated[list[RemotePreference] | None, Query()] = None,
    employment_type: Annotated[list[EmploymentTypeValue] | None, Query()] = None,
    posted_after: date | None = None,
    saved: bool = False,
    sort: JobSort = JobSort.POSTED,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobListResponse:
    """Jobs visible to the user (found by their discovery runs, or saved).
    One entry per canonical job, however many sources list it; `source`
    filters match a job when any of its postings is from that source."""
    return job_service.list_jobs(
        db,
        current_user,
        page=page,
        page_size=page_size,
        sources=source,
        q=q,
        location=location,
        work_models=work_model,
        employment_types=employment_type,
        posted_after=posted_after,
        saved_only=saved,
        sort=sort,
    )


@router.get("/{job_id}", response_model=JobDetailRead)
def get_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobDetailRead:
    """The canonical job, your saved state, and every source posting it was
    found on (`source_postings`, preferred first)."""
    job, user_job = job_service.get_visible_job(db, current_user, job_id)
    return job_service.detail(db, current_user, job, user_job)


@router.get("/{job_id}/sources", response_model=list[JobSourcePostingRead])
def get_job_sources(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[JobSourcePostingRead]:
    """Where the job was found: one entry per source posting, preferred
    first, with the original URL and freshness. Never raw source data."""
    return job_service.source_postings(db, current_user, job_id)


@router.put("/{job_id}/save", response_model=JobDetailRead)
def save_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobDetailRead:
    job, user_job = job_service.set_saved(db, current_user, job_id, saved=True)
    return job_service.detail(db, current_user, job, user_job)


@router.delete("/{job_id}/save", response_model=JobDetailRead)
def unsave_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobDetailRead:
    job, user_job = job_service.set_saved(db, current_user, job_id, saved=False)
    return job_service.detail(db, current_user, job, user_job)
