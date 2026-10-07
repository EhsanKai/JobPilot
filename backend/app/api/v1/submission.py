"""Review & submit endpoints (V0.11).

    JobPilot prepares the application; you review it, authorize it, and only then does
    JobPilot press the one final button -- once.

Ownership as everywhere: another user's application, session, review, authorization or
attempt -- or one that doesn't exist -- is a 404. Request bodies reject unknown keys: the
submit request takes an authorization id and nothing else (no URL, no selector, no form
data). There is still no endpoint that clicks anything generic.

This router is included before the V0.10 automation router, so `/automation/submissions`
is not read as a session id.
"""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.submission import (
    AttemptListResponse,
    AttemptRead,
    AuthorizationRead,
    AuthorizationRequest,
    ConsentRequest,
    CreateReviewRequest,
    FinalReviewRead,
    FinalReviewResponse,
    SubmitRequest,
    VerifyRequest,
)
from app.services.applications.service import get_owned_application
from app.services.automation import submission
from app.services.automation import submission_views as views

router = APIRouter(prefix="/applications/{application_id}/automation", tags=["submission"])


@router.get("/submissions", response_model=AttemptListResponse)
def list_submissions(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AttemptListResponse:
    """Every submission attempt of this application, newest first (the audit trail)."""
    return views.attempts(db, current_user, application_id)


@router.post("/submissions/{attempt_id}/verification", response_model=AttemptRead)
def verify_submission(
    application_id: uuid.UUID,
    attempt_id: uuid.UUID,
    request: VerifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AttemptRead:
    """After an unknown result: record what you found when you checked yourself."""
    attempt = submission.verify_attempt(
        db, current_user, application_id, attempt_id, request.outcome
    )
    return views.attempt_read(get_owned_application(db, current_user, application_id), attempt)


@router.get("/{session_id}/final-review", response_model=FinalReviewResponse)
def get_final_review(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FinalReviewResponse:
    """Exactly what would be submitted now -- read from the live page -- what blocks it,
    and your latest final review and authorization."""
    return views.final_review(db, current_user, application_id, session_id)


@router.post(
    "/{session_id}/final-review",
    response_model=FinalReviewRead,
    status_code=status.HTTP_201_CREATED,
)
def create_final_review(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: CreateReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FinalReviewRead:
    """Confirm the final review you looked at (by its hash). It becomes an immutable
    record; any later change makes it stale."""
    review = submission.create_review(
        db, current_user, application_id, session_id, request.expected_state_hash
    )
    return views.review_read(review)


@router.post("/{session_id}/consents/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def authorize_consent(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
    request: ConsentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Give one consent, for exactly the text you were shown."""
    submission.authorize_consent(
        db,
        current_user,
        application_id,
        session_id,
        field_id,
        request.consent_text_hash,
        request.give,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{session_id}/consents/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_consent(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Withdraw a consent authorization."""
    submission.revoke_consent(db, current_user, application_id, session_id, field_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{session_id}/submission-authorization",
    response_model=AuthorizationRead,
    status_code=status.HTTP_201_CREATED,
)
def authorize_submission(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: AuthorizationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuthorizationRead:
    """Your explicit authorization to submit the reviewed application."""
    row = submission.authorize(
        db,
        current_user,
        application_id,
        session_id,
        request.final_review_id,
        request.reviewed_state_hash,
    )
    return views.authorization_read(row)


@router.get("/{session_id}/submission-authorization", response_model=AuthorizationRead | None)
def get_submission_authorization(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuthorizationRead | None:
    from app.services.automation import controller

    session = controller.owned_session(db, current_user, application_id, session_id)
    submission.expire_authorizations(db, session.application_id)
    db.commit()
    row = submission.open_authorization(db, session.id)
    return views.authorization_read(row) if row is not None else None


@router.delete("/{session_id}/submission-authorization", status_code=status.HTTP_204_NO_CONTENT)
def cancel_submission_authorization(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    submission.cancel_authorization(db, current_user, application_id, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{session_id}/submit", response_model=AttemptRead)
def submit(
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: SubmitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AttemptRead:
    """Submit the reviewed application under your authorization -- once. The result is
    SUCCESS only when the page confirmed it; anything uncertain is SUBMISSION_UNKNOWN and
    JobPilot never submits again by itself."""
    attempt = submission.submit(
        db, current_user, application_id, session_id, request.authorization_id
    )
    return views.attempt_read(get_owned_application(db, current_user, application_id), attempt)
