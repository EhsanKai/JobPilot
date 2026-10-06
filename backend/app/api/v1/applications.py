"""Application Workspace endpoints (V0.7).

An application is the user's private preparation workspace for one job. It
is created only by an explicit `POST /jobs/{job_id}/application`, belongs to
the user who owns the job's `UserJob`, and nothing here sends anything to an
employer. Another user's application -- or one that doesn't exist -- is a
404.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.application import (
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationRead,
    ApplicationUpdate,
)
from app.services.applications import service

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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationListResponse:
    """Your applications, most recently updated first."""
    return service.list_applications(db, current_user, page=page, page_size=page_size)


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
