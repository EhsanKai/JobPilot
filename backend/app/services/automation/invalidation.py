"""Invalidating a final review and its authorization (V0.11).

Anything that may change what would be submitted -- the user takes control, pauses,
stops or resumes the assistant, a fill pass, a mapping or document decision, a consent
decision, a navigation or reload, another website -- calls `invalidate`. The final review
is then INVALIDATED and an open authorization INVALIDATED: the user reviews and
authorizes again. Nothing is regenerated silently.

These are guarded UPDATEs (`WHERE status = 'ACTIVE' / 'AUTHORIZED'`), not read-modify-
write: if a submission consumed the authorization a moment earlier, the UPDATE waits for
its row lock, re-checks the condition and leaves the consumed authorization alone.

Changes made elsewhere (an answer edited in the Questions section, a field the user typed
in the browser) are not announced here: they are caught by the state hash, which is
recomputed whenever the review is shown, when the submission is authorized, and right
before the final button is pressed.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.automation import AutomationEvent, AutomationSession
from app.models.submission import (
    FinalReview,
    FinalReviewStatus,
    SubmissionAuthorization,
    SubmissionAuthorizationStatus,
)
from app.services.automation.events import EventType

# Reasons (stable codes).
USER_TAKEOVER = "USER_TAKEOVER"
PAUSED = "PAUSED"
STOPPED = "STOPPED"
RESUMED = "RESUMED"
FILL = "FILL"
MAPPING_CHANGED = "MAPPING_CHANGED"
DOCUMENT_CHANGED = "DOCUMENT_CHANGED"
CONSENT_CHANGED = "CONSENT_CHANGED"
NAVIGATION = "NAVIGATION"
DOMAIN_CHANGED = "DOMAIN_CHANGED"
STATE_CHANGED = "STATE_CHANGED"
REVIEW_SUPERSEDED = "REVIEW_SUPERSEDED"
USER_CANCELLED = "USER_CANCELLED"
BROWSER_GONE = "BROWSER_GONE"


def _event(db: Session, session: AutomationSession, event_type: EventType, **data: str) -> None:
    db.add(
        AutomationEvent(
            session_id=session.id,
            user_id=session.user_id,
            event_type=event_type.value,
            data=dict(data),
        )
    )


def invalidate(
    db: Session,
    session: AutomationSession,
    reason: str,
    *,
    reviews: bool = True,
    review_status: FinalReviewStatus = FinalReviewStatus.INVALIDATED,
) -> bool:
    """Invalidate the session's open authorization (and, with `reviews`, its active final
    review). The caller commits. Returns whether anything was invalidated."""
    now = datetime.now(UTC)
    changed = False
    authorizations = db.execute(
        update(SubmissionAuthorization)
        .where(
            SubmissionAuthorization.session_id == session.id,
            SubmissionAuthorization.status == SubmissionAuthorizationStatus.AUTHORIZED,
        )
        .values(
            status=SubmissionAuthorizationStatus.INVALIDATED,
            invalidated_at=now,
            invalidation_reason=reason[:40],
        )
        .returning(SubmissionAuthorization.id)
        .execution_options(synchronize_session=False)
    ).all()
    for (authorization_id,) in authorizations:
        _event(
            db,
            session,
            EventType.SUBMISSION_AUTHORIZATION_INVALIDATED,
            reason=reason,
            authorization_id=str(authorization_id),
        )
        changed = True
    if reviews:
        values: dict[str, object] = {"status": review_status}
        if review_status is FinalReviewStatus.INVALIDATED:
            values.update(invalidated_at=now, invalidation_reason=reason[:40])
        found = db.execute(
            update(FinalReview)
            .where(
                FinalReview.session_id == session.id,
                FinalReview.status == FinalReviewStatus.ACTIVE,
            )
            .values(**values)
            .returning(FinalReview.id)
            .execution_options(synchronize_session=False)
        ).all()
        for (review_id,) in found:
            _event(
                db,
                session,
                EventType.FINAL_REVIEW_INVALIDATED,
                reason=reason,
                review_id=str(review_id),
            )
            changed = True
    return changed


def active_review(db: Session, session_id: uuid.UUID) -> FinalReview | None:
    return db.scalar(
        select(FinalReview)
        .where(
            FinalReview.session_id == session_id,
            FinalReview.status == FinalReviewStatus.ACTIVE,
        )
        .execution_options(populate_existing=True)
    )


def invalidate_if_navigated(db: Session, session: AutomationSession, marker: str) -> bool:
    """A navigation, reload or new window since the review: the reviewed page instance is
    gone, so the review and its authorization are too. The caller commits."""
    review = active_review(db, session.id)
    if review is None or review.page_marker == marker:
        return False
    return invalidate(db, session, NAVIGATION)
