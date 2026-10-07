"""Read models of review & submit (V0.11): what the API returns."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.submission import (
    FinalReview,
    FinalReviewStatus,
    SubmissionAttempt,
    SubmissionAuthorization,
    SubmissionAuthorizationStatus,
    SubmissionResult,
)
from app.models.user import User
from app.schemas.submission import (
    AttemptListResponse,
    AttemptRead,
    AuthorizationRead,
    DestinationRead,
    FinalReviewRead,
    FinalReviewResponse,
    IssueRead,
    ReviewConsentRead,
    ReviewDocumentRead,
    ReviewFieldRead,
    ReviewStateRead,
    SubmitControlRead,
)
from app.services.applications.service import get_owned_application
from app.services.automation import controller, messages, submission
from app.services.automation.review import Issue, SubmissionState


def _job(application: Application) -> tuple[str | None, str | None, str | None]:
    snapshot = application.context
    document = snapshot.read_document() if snapshot is not None else None
    if document is None:
        return None, None, None
    return document.job.company_name, document.job.title, document.source.source


def _issue(issue: Issue) -> IssueRead:
    return IssueRead(
        code=issue.code, message=issue.message, field_id=issue.field_id, label=issue.label
    )


def _uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _when(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def state_read(application: Application, state: SubmissionState) -> ReviewStateRead:
    data = state.state
    company, position, source = _job(application)
    control = data.get("submit_control")
    return ReviewStateRead(
        schema_version=data["schema_version"],
        state_hash=state.state_hash,
        observed=state.observed,
        destination=DestinationRead(
            host=data["destination"]["host"],
            domain=data["destination"]["domain"],
            url=data["destination"]["url"],
            company=company,
            position=position,
            source=source,
        ),
        fields=[
            ReviewFieldRead(
                field_id=_uuid(f.get("field_id")),
                field_key=f["field_key"],
                label=f["label"],
                input_type=f["input_type"],
                required=f["required"],
                group=f["group"],
                mapping=f["mapping"],
                question_id=_uuid(f.get("question_id")),
                question=f.get("question"),
                value=f.get("value"),
                value_label=f.get("value_label"),
                source=f.get("source"),
                approval=f["approval"],
                verification=f["verification"],
            )
            for f in data["fields"]
        ],
        documents=[
            ReviewDocumentRead(
                field_id=_uuid(d.get("field_id")),
                field_key=d["field_key"],
                label=d["label"],
                required=d["required"],
                file=d.get("file"),
                document_type=d.get("document_type"),
                document_id=_uuid(d.get("document_id")),
                document_label=d.get("document_label"),
                version_number=d.get("version_number"),
                origin=d.get("origin"),
                content_hash=d.get("content_hash"),
                source=d.get("source"),
                verification=d["verification"],
            )
            for d in data["documents"]
        ],
        consents=[
            ReviewConsentRead(
                field_id=_uuid(c.get("field_id")),
                field_key=c["field_key"],
                input_type=c["input_type"],
                text=c["text"],
                text_hash=c["text_hash"],
                required=c["required"],
                given=c["given"],
                authorized=c["authorized"],
                authorized_at=_when(c.get("authorized_at")),
            )
            for c in data["consents"]
        ],
        submit_control=SubmitControlRead(label=control["label"]) if control else None,
        blockers=[_issue(i) for i in state.blockers],
        warnings=[_issue(i) for i in state.warnings],
        ready=state.ready,
    )


def review_read(review: FinalReview) -> FinalReviewRead:
    return FinalReviewRead(
        id=review.id,
        session_id=review.session_id,
        status=review.status,
        state_hash=review.state_hash,
        destination_host=review.destination_host,
        destination_domain=review.destination_domain,
        destination_url=review.destination_url,
        reviewed_at=review.created_at,
        invalidated_at=review.invalidated_at,
        invalidation_reason=review.invalidation_reason,
    )


def authorization_read(row: SubmissionAuthorization) -> AuthorizationRead:
    return AuthorizationRead(
        id=row.id,
        session_id=row.session_id,
        final_review_id=row.final_review_id,
        reviewed_state_hash=row.reviewed_state_hash,
        destination_host=row.destination_host,
        status=row.status,
        authorized_at=row.created_at,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
        invalidated_at=row.invalidated_at,
        invalidation_reason=row.invalidation_reason,
    )


def final_review(
    db: Session, user: User, application_id: uuid.UUID, session_id: uuid.UUID
) -> FinalReviewResponse:
    application = get_owned_application(db, user, application_id)
    session = controller.owned_session(db, user, application_id, session_id)
    controller.refresh(db, user, session)
    submission.expire_authorizations(db, application.id)
    db.commit()
    submission.reconcile_attempts(db, application.id)
    state = submission.current_state(db, user, application, session)
    review = submission.latest_review(db, session.id)
    authorization = submission.open_authorization(db, session.id)
    current = (
        review is not None
        and review.status is FinalReviewStatus.ACTIVE
        and state.observed
        and review.state_hash == state.state_hash
        and review.page_marker == state.page_marker
    )
    open_auth = (
        authorization is not None
        and authorization.status is SubmissionAuthorizationStatus.AUTHORIZED
        and authorization.expires_at > datetime.now(UTC)
    )
    return FinalReviewResponse(
        current=state_read(application, state),
        review=review_read(review) if review is not None else None,
        review_is_current=current,
        authorization=authorization_read(authorization) if authorization is not None else None,
        can_confirm_review=state.observed and state.ready,
        can_authorize=current and state.ready and not open_auth,
    )


def attempt_read(application: Application, row: SubmissionAttempt) -> AttemptRead:
    company, position, _ = _job(application)
    key = row.result.value if row.result is not None else "IN_PROGRESS"
    return AttemptRead(
        id=row.id,
        session_id=row.session_id,
        authorization_id=row.authorization_id,
        final_review_id=row.final_review_id,
        reviewed_state_hash=row.reviewed_state_hash,
        destination_domain=row.destination_domain,
        destination_url=row.destination_url,
        started_at=row.started_at,
        submit_triggered_at=row.submit_triggered_at,
        finished_at=row.finished_at,
        result=row.result,
        message=messages.SUBMISSION_RESULT_MESSAGES[key],
        confirmation_reference=row.confirmation_reference,
        confirmation_summary=row.confirmation_summary,
        failure_code=row.failure_code,
        failure_reason=row.failure_reason,
        user_verified_outcome=row.user_verified_outcome,
        user_verified_at=row.user_verified_at,
        company=company,
        position=position,
    )


def attempts(db: Session, user: User, application_id: uuid.UUID) -> AttemptListResponse:
    application = get_owned_application(db, user, application_id)
    rows = submission.list_attempts(db, user, application_id)
    submitted = any(
        r.result is SubmissionResult.SUCCESS or r.user_verified_outcome == "SUBMITTED" for r in rows
    )
    pending = any(
        r.result is SubmissionResult.SUBMISSION_UNKNOWN and r.user_verified_outcome is None
        for r in rows
    )
    return AttemptListResponse(
        items=[attempt_read(application, r) for r in rows],
        submitted=submitted,
        needs_verification=pending,
    )
