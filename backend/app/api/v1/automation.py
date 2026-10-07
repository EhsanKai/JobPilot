"""Browser assistant endpoints (V0.10).

    JobPilot fills forms; the user remains in control.

Sessions belong to an application and so to the user who owns its `UserJob`: another
user's application, session or field -- or one that doesn't exist -- is a 404. Nothing a
client sends can set an owner, a status, a URL, a locator, a mapping decision or a file
path (request bodies reject unknown keys), and no response carries cookies, tokens,
browser process or connection details, or filesystem paths.

There is no endpoint that submits an application, and none that clicks anything on the
page: the assistant fills approved answers and hands the browser back.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.ai.provider import AIProvider
from app.api.deps import get_current_user, get_user_ai_provider
from app.db.session import get_db
from app.models.user import User
from app.schemas.automation import (
    ConfirmDomainRequest,
    DocumentChoicesResponse,
    DocumentSelectionRequest,
    EventListResponse,
    FieldListResponse,
    FieldRead,
    FillRequest,
    MappingRequest,
    ResumeRequest,
    SessionListResponse,
    SessionRead,
    StartRequest,
    StopRequest,
    SuggestRequest,
    SuggestResponse,
)
from app.services.automation import controller, service

router = APIRouter(prefix="/applications/{application_id}/automation", tags=["automation"])


@router.post("/start", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def start_session(
    application_id: uuid.UUID,
    request: StartRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_user_ai_provider),
) -> SessionRead:
    """Open the application's page in a controlled browser and discover its form. You
    start it explicitly; the page is the application's own URL, never one you send."""
    return service.start(db, current_user, application_id, provider)


@router.get("", response_model=SessionListResponse)
def list_sessions(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionListResponse:
    """This application's browser sessions, newest first, and whether one can be started."""
    return service.list_sessions(db, current_user, application_id)


@router.get("/{session_id}", response_model=SessionRead)
def get_session(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionRead:
    return service.get_session(db, current_user, application_id, session_id)


@router.get("/{session_id}/fields", response_model=FieldListResponse)
def list_fields(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FieldListResponse:
    """The fields found on the page, how each maps to a question, and what is approved."""
    return service.list_fields(db, current_user, application_id, session_id)


@router.get("/{session_id}/events", response_model=EventListResponse)
def list_events(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EventListResponse:
    return service.list_events(db, current_user, application_id, session_id, limit)


@router.get("/{session_id}/screenshot")
def get_screenshot(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """What the browser shows now (PNG), for your live view. Taken on request, never stored."""
    png = service.screenshot(db, current_user, application_id, session_id)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


# --- Your controls -------------------------------------------------------------------------------


@router.post("/{session_id}/pause", response_model=SessionRead)
def pause(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionRead:
    """Stop the assistant from changing anything. The browser stays as it is."""
    controller.pause(db, current_user, application_id, session_id)
    return service.get_session(db, current_user, application_id, session_id)


@router.post("/{session_id}/take-control", response_model=SessionRead)
def take_control(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionRead:
    """The browser is yours. The assistant changes nothing until you resume it."""
    controller.take_control(db, current_user, application_id, session_id)
    return service.get_session(db, current_user, application_id, session_id)


@router.post("/{session_id}/resume", response_model=SessionRead)
def resume(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: ResumeRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_user_ai_provider),
) -> SessionRead:
    """Give the browser back to the assistant. It looks at the page first and keeps
    whatever you changed; with `fill` it then fills the fields that are still empty."""
    controller.resume(db, current_user, application_id, session_id, request, provider)
    return service.get_session(db, current_user, application_id, session_id)


@router.post("/{session_id}/stop", response_model=SessionRead)
def stop(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: StopRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionRead:
    """End the assistant. The browser may stay open for you; the assistant no longer
    touches it."""
    controller.stop(
        db,
        current_user,
        application_id,
        session_id,
        close_browser=bool(request and request.close_browser),
    )
    return service.get_session(db, current_user, application_id, session_id)


@router.post("/{session_id}/confirm-domain", response_model=SessionRead)
def confirm_domain(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: ConfirmDomainRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_user_ai_provider),
) -> SessionRead:
    """Say that the website the browser moved to is part of this application."""
    controller.confirm_domain(db, current_user, application_id, session_id, request.host, provider)
    return service.get_session(db, current_user, application_id, session_id)


# --- What the assistant does ---------------------------------------------------------------------


@router.post("/{session_id}/discover", response_model=SessionRead)
def discover(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_user_ai_provider),
) -> SessionRead:
    """Read the page again (a new step, a section that opened, a field that appeared).
    Reads only: nothing on the page changes."""
    controller.discover(db, current_user, application_id, session_id, provider)
    return service.get_session(db, current_user, application_id, session_id)


@router.post("/{session_id}/fill", response_model=SessionRead)
def fill(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: FillRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_user_ai_provider),
) -> SessionRead:
    """Fill the approved answers into empty fields and check each one. Never overwrites
    what you changed, never ticks a consent, never submits."""
    controller.fill(db, current_user, application_id, session_id, request, provider)
    return service.get_session(db, current_user, application_id, session_id)


@router.post("/{session_id}/suggest", response_model=SuggestResponse)
async def suggest(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: SuggestRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_user_ai_provider),
) -> SuggestResponse:
    """Ask for AI suggestions for the mapped questions without an answer. They are only
    suggestions: you accept or edit them in the Questions section before anything is filled."""
    return await service.suggest(db, current_user, application_id, session_id, provider)


# --- Your decisions about one field --------------------------------------------------------------


@router.post("/{session_id}/fields/{field_id}/mapping", response_model=FieldRead)
def set_mapping(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
    request: MappingRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FieldRead:
    """Confirm the proposed question, choose another, leave the field unmapped or add a
    question for it. Your decision is remembered for later sessions."""
    controller.set_mapping(db, current_user, application_id, session_id, field_id, request)
    return service.get_field(db, current_user, application_id, session_id, field_id)


@router.get("/{session_id}/fields/{field_id}/documents", response_model=DocumentChoicesResponse)
def document_choices(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentChoicesResponse:
    """The documents of this application you can attach to a file field."""
    return service.document_choices(db, current_user, application_id, session_id, field_id)


@router.put("/{session_id}/fields/{field_id}/document", response_model=FieldRead)
def select_document(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
    request: DocumentSelectionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FieldRead:
    """ "Use this file": pin one document of this application to the field. Nothing is
    attached before you do; a newer version never replaces your choice by itself."""
    controller.set_document(
        db,
        current_user,
        application_id,
        session_id,
        field_id,
        request.document_type,
        request.document_id,
    )
    return service.get_field(db, current_user, application_id, session_id, field_id)


@router.delete("/{session_id}/fields/{field_id}/document", response_model=FieldRead)
def clear_document(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FieldRead:
    controller.clear_document(db, current_user, application_id, session_id, field_id)
    return service.get_field(db, current_user, application_id, session_id, field_id)
