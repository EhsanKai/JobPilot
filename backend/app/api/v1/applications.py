"""Application Workspace endpoints (V0.7).

An application is the user's private preparation workspace for one job. It
is created only by an explicit `POST /jobs/{job_id}/application`, belongs to
the user who owns the job's `UserJob`, and nothing here sends anything to an
employer. Another user's application -- or one that doesn't exist -- is a
404.
"""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.application import ApplicationStatus
from app.models.user import User
from app.schemas.application import (
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationRead,
    ApplicationUpdate,
)
from app.schemas.application_tracking import (
    ApplicationListFilters,
    StatusChangeRequest,
    StatusChangeResponse,
    StatusCountsRead,
    TimelineResponse,
    TrackingUpdateRequest,
)
from app.services.applications import service, tracking

router = APIRouter(tags=["applications"])


@router.post(
    "/jobs/{job_id}/application",
    response_model=ApplicationRead,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": ApplicationRead, "description": "The application already existed."}},
)
def start_application(
    job_id: uuid.UUID,
    response: Response,
    request: ApplicationCreate | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationRead:
    """Start (create) your application for one of your jobs: 201 with the new
    application, or 200 with the existing one -- starting twice never creates
    a second. The body is optional and only used when creating."""
    result = service.start_application(db, current_user, job_id, request)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return service.to_read(db, current_user, result.application)


@router.get("/applications", response_model=ApplicationListResponse)
def list_applications(
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_: Annotated[
        list[ApplicationStatus] | None, Query(alias="status", description="One or more statuses.")
    ] = None,
    company: Annotated[str | None, Query(max_length=255)] = None,
    source: Annotated[str | None, Query(max_length=100)] = None,
    applied_from: date | None = None,
    applied_to: date | None = None,
    sort: Literal["updated", "applied"] = "updated",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationListResponse:
    """Your applications ("My Applications"), most recently updated first.

    Filter by `status` (repeatable), `company` (contains, any case), `source` and the
    application date (`applied_from` / `applied_to`, inclusive, UTC). `sort=applied` sorts
    by application date instead; applications not applied yet come last."""
    filters = ApplicationListFilters(
        statuses=status_ or [],
        company=company,
        source=source,
        applied_from=applied_from,
        applied_to=applied_to,
        sort=sort,
    )
    return service.list_applications(
        db, current_user, page=page, page_size=page_size, filters=filters
    )


@router.get("/applications/summary", response_model=StatusCountsRead)
def application_status_counts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StatusCountsRead:
    """How many of your applications are in each status."""
    return tracking.status_counts(db, current_user)


@router.get("/applications/{application_id}", response_model=ApplicationRead)
def get_application(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationRead:
    application = service.get_owned_application(db, current_user, application_id)
    return service.to_read(db, current_user, application)


@router.patch("/applications/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: uuid.UUID,
    request: ApplicationUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationRead:
    """Update the application's own preparation data. Omitted fields stay as
    they are; `null` clears a field. Your profile, the job and your match are
    never changed."""
    application = service.update_application(db, current_user, application_id, request)
    return service.to_read(db, current_user, application)


# --- Tracking (V0.12) ------------------------------------------------------------------------


@router.patch("/applications/{application_id}/status", response_model=StatusChangeResponse)
def change_application_status(
    application_id: uuid.UUID,
    request: StatusChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StatusChangeResponse:
    """Change the application's tracking status yourself.

    Only `status`, an optional `note`, an optional `follow_up_at` and `correction` are
    accepted; the previous status, the time, the user and the actor are the server's.
    A change the lifecycle doesn't allow is a 409 (with what is allowed); `correction: true`
    is the explicit way to fix a wrongly recorded status, and needs a note. The same
    status again changes nothing. Nothing is sent to anyone."""
    result = tracking.change_status(db, current_user, application_id, request)
    return _tracking_response(db, current_user, result)


@router.patch("/applications/{application_id}/tracking", response_model=StatusChangeResponse)
def update_application_tracking(
    application_id: uuid.UUID,
    request: TrackingUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StatusChangeResponse:
    """Set (or clear, with `null`) the follow-up date, and/or add a note. A follow-up
    date is a reminder for you: nothing is sent and nothing happens when it passes."""
    result = tracking.update_tracking(db, current_user, application_id, request)
    return _tracking_response(db, current_user, result)


@router.get("/applications/{application_id}/timeline", response_model=TimelineResponse)
def get_application_timeline(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TimelineResponse:
    """The application's history, newest first: status changes, notes, corrections and
    submission attempts."""
    return tracking.timeline(db, current_user, application_id)


def _tracking_response(
    db: Session, user: User, result: tracking.ChangeResult
) -> StatusChangeResponse:
    application = result.application
    attempt = tracking.latest_attempts(db, user.id, [application.id]).get(application.id)
    return StatusChangeResponse(
        changed=result.changed, tracking=tracking.tracking_read(application, attempt)
    )
