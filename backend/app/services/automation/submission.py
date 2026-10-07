"""Review & submit (V0.11): the only way JobPilot submits an application.

    Final review     the user looks at exactly what would be submitted and confirms it
                     -> FinalReview (immutable snapshot + SHA-256 of the state)
    Consents         each legal consent is authorized one by one, for its exact text
                     -> ConsentAuthorization
    Authorization    "I have reviewed this and authorize JobPilot to submit it"
                     -> SubmissionAuthorization (scoped to the review, its hash, the session,
                        the host; expires; one-shot)
    Submit           consume the authorization, re-verify the live page under the write
                     gate, press the one final button ONCE through a single-use permit
                     -> SubmissionAttempt (append-only audit)
    Verify           SUCCESS only on evidence the page shows; anything uncertain after the
                     press is SUBMISSION_UNKNOWN -- and JobPilot never presses again by itself

Guarantees (each pinned by tests):

- **Separate capability.** The fill assistant still cannot press a button
  (`safety.SUBMIT_ACTIONS_ALLOWED` is False). Only `submit` here issues the permit the
  final-submission channel needs, after the checks below.
- **Server-side authorization.** Nothing the client sends chooses what is submitted or
  where: the request names an authorization id, nothing else. The authorization must be the
  user's, for this application and session, unused, unexpired, and its review current.
- **Exactly once.** The session row is locked, the authorization row is locked and moved
  AUTHORIZED -> CONSUMED in the same transaction that creates the attempt; the database
  allows one attempt per authorization and one unfinished attempt per application. A second
  request -- concurrent or later -- finds the authorization consumed.
- **What is pressed is what was reviewed.** Immediately before the press, under the write
  gate (the session row FOR SHARE: a take-control waits), the state is recomputed from the
  live page: its hash must equal the reviewed hash, the page instance must be the reviewed
  one, the host the reviewed host, no blocker present.
- **No automatic retry.** `submit_triggered_at` is committed *before* the press. After the
  press, a timeout, a crash, a disconnect or a page without a confirmation is
  SUBMISSION_UNKNOWN; the application then accepts no new authorization until the user has
  checked and recorded what they found.
"""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.models.application import Application
from app.models.automation import (
    AutomationField,
    AutomationSession,
    AutomationStatus,
)
from app.models.submission import (
    ConsentAuthorization,
    FinalReview,
    FinalReviewStatus,
    SubmissionAttempt,
    SubmissionAuthorization,
    SubmissionAuthorizationStatus,
    SubmissionResult,
)
from app.models.user import User
from app.services.applications import tracking
from app.services.applications.service import get_owned_application
from app.services.automation import controller, filler, invalidation, messages, safety
from app.services.automation import review as review_state
from app.services.automation.browser.base import BrowserError
from app.services.automation.browser.final_submit import (
    FinalSubmissionChannel,
    OutcomeSignals,
    PermitError,
    SubmissionNotTriggered,
    issue_permit,
)
from app.services.automation.events import EventType
from app.services.automation.registry import LiveSession, registry

logger = get_logger(__name__)

S = AutomationStatus
_WORKING = (S.RUNNING, S.COMPLETED)
_POLL_SECONDS = 0.3
#: An unfinished attempt older than this, that no request of this process is running, was
#: interrupted (the server restarted mid-attempt).
_STALE_AFTER = timedelta(minutes=5)

# Attempts this process is running right now (so they are not mistaken for interrupted).
_IN_FLIGHT: set[uuid.UUID] = set()
_IN_FLIGHT_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _blocked(code: str, message: str, issues: list[review_state.Issue]) -> AppError:
    return AppError(
        code,
        message,
        409,
        {
            "blockers": [
                {
                    "code": i.code,
                    "message": i.message,
                    "field_id": str(i.field_id) if i.field_id else None,
                    "label": i.label,
                }
                for i in issues
            ]
        },
    )


def _live(session: AutomationSession) -> LiveSession | None:
    live = registry.get(session.id)
    if live is None or not live.browser.is_alive() or live.detached:
        return None
    return live


# --- Housekeeping --------------------------------------------------------------------------------


def expire_authorizations(db: Session, application_id: uuid.UUID) -> None:
    """Authorizations not used in time are EXPIRED (a guarded UPDATE). The caller commits."""
    db.execute(
        update(SubmissionAuthorization)
        .where(
            SubmissionAuthorization.application_id == application_id,
            SubmissionAuthorization.status == SubmissionAuthorizationStatus.AUTHORIZED,
            SubmissionAuthorization.expires_at <= _now(),
        )
        .values(status=SubmissionAuthorizationStatus.EXPIRED)
        .execution_options(synchronize_session=False)
    )


def reconcile_attempts(db: Session, application_id: uuid.UUID) -> None:
    """An attempt left unfinished by a server that stopped mid-way is finished now -- as
    UNKNOWN if the press may have happened, as FAILED_BEFORE_SUBMIT if it certainly did not.
    Never retried."""
    cutoff = _now() - _STALE_AFTER
    with _IN_FLIGHT_LOCK:
        running = set(_IN_FLIGHT)
    rows = list(
        db.scalars(
            select(SubmissionAttempt)
            .where(
                SubmissionAttempt.application_id == application_id,
                SubmissionAttempt.finished_at.is_(None),
                SubmissionAttempt.started_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        )
    )
    for attempt in rows:
        if attempt.id in running:
            continue
        attempt.finished_at = _now()
        if attempt.submit_triggered_at is not None:
            attempt.result = SubmissionResult.SUBMISSION_UNKNOWN
            attempt.failure_code = "INTERRUPTED_AFTER_PRESS"
        else:
            attempt.result = SubmissionResult.FAILED_BEFORE_SUBMIT
            attempt.failure_code = "INTERRUPTED"
        attempt.failure_reason = messages.SUBMISSION_FAILURES[attempt.failure_code]
    db.commit()


def _housekeeping(db: Session, application_id: uuid.UUID) -> None:
    expire_authorizations(db, application_id)
    db.commit()
    reconcile_attempts(db, application_id)


def open_authorization(db: Session, session_id: uuid.UUID) -> SubmissionAuthorization | None:
    """The newest authorization of the session (any status), for display."""
    return db.scalar(
        select(SubmissionAuthorization)
        .where(SubmissionAuthorization.session_id == session_id)
        .order_by(SubmissionAuthorization.created_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


def latest_review(db: Session, session_id: uuid.UUID) -> FinalReview | None:
    return db.scalar(
        select(FinalReview)
        .where(FinalReview.session_id == session_id)
        .order_by(FinalReview.created_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


# --- The final review ----------------------------------------------------------------------------


def current_state(
    db: Session, user: User, application: Application, session: AutomationSession
) -> review_state.SubmissionState:
    """The live submission state (brings the fields up to date first). If it no longer
    matches the active final review, that review is invalidated: the user reviews again."""
    live = _live(session)
    if live is None or session.status not in _WORKING:
        return review_state.compute(db, user, application, session, live, sync=False)
    with live.lock:
        db.refresh(session)
        state = review_state.compute(db, user, application, session, live, sync=True)
        active = invalidation.active_review(db, session.id)
        if active is not None and state.observed:
            if state.page_marker != active.page_marker:
                invalidation.invalidate(db, session, invalidation.NAVIGATION)
            elif state.state_hash != active.state_hash:
                invalidation.invalidate(db, session, invalidation.STATE_CHANGED)
            db.commit()
    return state


def create_review(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    expected_state_hash: str,
) -> FinalReview:
    """The user confirms the final review they are looking at. It is persisted only if
    the live state is still exactly that one (`expected_state_hash`) and nothing blocks it."""
    application = get_owned_application(db, user, application_id)
    session = controller.owned_session(db, user, application_id, session_id)
    _housekeeping(db, application.id)
    live = _live(session)
    if live is None:
        raise AppError("BROWSER_GONE", review_state.BLOCKER_MESSAGES["BROWSER_GONE"], 409)
    with live.lock:
        db.refresh(session)
        state = review_state.compute(db, user, application, session, live, sync=True)
        if state.blockers:
            raise _blocked(
                "REVIEW_BLOCKED",
                "Something must be resolved before this application can be reviewed for "
                "submission.",
                state.blockers,
            )
        if state.state_hash != expected_state_hash:
            raise AppError(
                "REVIEW_CHANGED",
                "The application changed while you were looking at it. Check the final review "
                "again.",
                409,
                {"state_hash": state.state_hash},
            )
        invalidation.invalidate(
            db,
            session,
            invalidation.REVIEW_SUPERSEDED,
            review_status=FinalReviewStatus.SUPERSEDED,
        )
        review = FinalReview(
            user_id=user.id,
            application_id=application.id,
            session_id=session.id,
            schema_version=review_state.SCHEMA_VERSION,
            state_hash=state.state_hash,
            destination_host=state.destination_host,
            destination_domain=state.destination_domain,
            destination_url=state.destination_url,
            page_marker=state.page_marker,
            snapshot=state.state,
            status=FinalReviewStatus.ACTIVE,
        )
        db.add(review)
        db.flush()
        controller.record(db, session, EventType.FINAL_REVIEW_CREATED, review_id=str(review.id))
        db.commit()
        return review


# --- Consents ------------------------------------------------------------------------------------


def _consent_field(
    db: Session, user: User, session: AutomationSession, field_id: uuid.UUID
) -> AutomationField:
    row = controller.owned_field(db, user, session, field_id)
    if row.user_action_reason != "LEGAL_CONSENT":
        raise AppError("NOT_A_CONSENT", "This field is not a legal consent.", 409)
    if not row.present:
        raise AppError("FIELD_GONE", "This consent is no longer on the page.", 409)
    return row


def _working_live(db: Session, session: AutomationSession) -> LiveSession:
    if session.status not in _WORKING:
        raise AppError(
            "INVALID_SESSION_STATE",
            "Resume the browser assistant first: it needs to read the page.",
            409,
            {"status": session.status.value},
        )
    return controller._live_of(db, session)  # noqa: SLF001


def _set_consent_box(
    db: Session,
    user: User,
    application: Application,
    session: AutomationSession,
    live: LiveSession,
    row: AutomationField,
    give: bool,
) -> None:
    """Tick (or untick) one consent checkbox -- only ever right after the user's explicit,
    per-consent decision -- and read it back. Under the same write gate as every fill."""
    if row.input_type != "checkbox":
        return
    page = live.browser.page
    if session.status is S.COMPLETED:
        controller._set_status(db, session, S.RUNNING)  # noqa: SLF001 - as `fill` does
        db.commit()
    try:
        with controller._writing_allowed(db, session.id):  # noqa: SLF001
            locator = filler.locator_of(row)
            current = page.read_value(locator)
            if not current.found:
                raise AppError("FIELD_GONE", "This consent is no longer on the page.", 409)
            if bool(current.checked) != give:
                page.write_checked(locator, give)
                if bool(page.read_value(locator).checked) != give:
                    raise AppError(
                        "CONSENT_NOT_SET",
                        "The page did not accept the change. Do it yourself.",
                        409,
                    )
            controller.record(
                db, session, EventType.FIELD_FILLED, row.id, source="CONSENT", given=give
            )
            db.commit()
        controller._settle(db, user, application, session)  # noqa: SLF001
    except controller.Interrupted:
        raise AppError(
            "INVALID_SESSION_STATE", "The assistant is no longer running.", 409
        ) from None
    except BrowserError as exc:
        db.rollback()
        raise AppError(exc.code, exc.message, 502) from None


def authorize_consent(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
    consent_text_hash: str,
    give: bool,
) -> AutomationField:
    """The user explicitly gives one consent -- exactly the text they were shown
    (`consent_text_hash`). With `give`, JobPilot ticks that one checkbox for them."""
    application = get_owned_application(db, user, application_id)
    session = controller.owned_session(db, user, application_id, session_id)
    live = _working_live(db, session)
    with live.lock:
        db.refresh(session)
        if session.status not in _WORKING:
            raise AppError("INVALID_SESSION_STATE", "The assistant is no longer running.", 409)
        inspection = controller._inspect(db, user, application, session, live, None)  # noqa: SLF001
        if inspection.outcome != "running":
            raise AppError(
                "INVALID_SESSION_STATE", "The page needs you first (see the assistant).", 409
            )
        row = _consent_field(db, user, session, field_id)
        text = row.consent_text or row.label
        digest = review_state.text_hash(text)
        if digest != consent_text_hash:
            raise AppError(
                "CONSENT_TEXT_CHANGED",
                "The text of this consent is not the one you were shown. Read it again.",
                409,
                {"text": text, "text_hash": digest},
            )
        existing = db.scalar(
            select(ConsentAuthorization).where(
                ConsentAuthorization.field_id == row.id,
                ConsentAuthorization.revoked_at.is_(None),
            )
        )
        if existing is None or existing.consent_text_hash != digest:
            if existing is not None:
                existing.revoked_at = _now()
                existing.revoke_reason = "TEXT_CHANGED"
                db.flush()
            db.add(
                ConsentAuthorization(
                    user_id=user.id,
                    session_id=session.id,
                    field_id=row.id,
                    field_key=row.field_key,
                    consent_text=text,
                    consent_text_hash=digest,
                )
            )
            controller.record(db, session, EventType.CONSENT_AUTHORIZED, row.id)
        invalidation.invalidate(db, session, invalidation.CONSENT_CHANGED)
        db.commit()
        if give:
            _set_consent_box(db, user, application, session, live, row, True)
    return row


def revoke_consent(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
) -> AutomationField:
    """The user withdraws a consent authorization. JobPilot unticks the box it may have
    ticked (only ever in that direction on its own) when the assistant is running."""
    application = get_owned_application(db, user, application_id)
    session = controller.owned_session(db, user, application_id, session_id)
    row = controller.owned_field(db, user, session, field_id)
    if row.user_action_reason != "LEGAL_CONSENT":
        raise AppError("NOT_A_CONSENT", "This field is not a legal consent.", 409)
    existing = db.scalar(
        select(ConsentAuthorization).where(
            ConsentAuthorization.field_id == row.id,
            ConsentAuthorization.revoked_at.is_(None),
        )
    )
    if existing is not None:
        existing.revoked_at = _now()
        existing.revoke_reason = "USER_REVOKED"
        controller.record(db, session, EventType.CONSENT_REVOKED, row.id)
    invalidation.invalidate(db, session, invalidation.CONSENT_CHANGED)
    db.commit()
    live = _live(session)
    if live is not None and session.status in _WORKING:
        with live.lock:
            _set_consent_box(db, user, application, session, live, row, False)
    return row


# --- Authorization -------------------------------------------------------------------------------


def authorize(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    final_review_id: uuid.UUID,
    reviewed_state_hash: str,
) -> SubmissionAuthorization:
    """The user's explicit "submit this". Granted only when every condition holds now:
    the review is the session's current one, the hash the client saw is the review's, the
    live page is still exactly that state (same hash, same page instance), and nothing
    blocks it."""
    application = get_owned_application(db, user, application_id)
    session = controller.owned_session(db, user, application_id, session_id)
    _housekeeping(db, application.id)
    review = db.scalar(
        select(FinalReview).where(
            FinalReview.id == final_review_id,
            FinalReview.session_id == session.id,
            FinalReview.user_id == user.id,
        )
    )
    if review is None:
        raise NotFoundError("Final review not found.")
    if review.status is not FinalReviewStatus.ACTIVE:
        raise AppError(
            "REVIEW_NOT_CURRENT",
            "This final review is no longer current"
            + (
                f" ({review.invalidation_reason.lower().replace('_', ' ')})"
                if review.invalidation_reason
                else ""
            )
            + ". Review the application again.",
            409,
            {"status": review.status.value, "reason": review.invalidation_reason},
        )
    if reviewed_state_hash != review.state_hash:
        raise AppError(
            "REVIEW_HASH_MISMATCH",
            "What you confirmed is not the reviewed state. Review the application again.",
            409,
        )
    live = _live(session)
    if live is None:
        raise AppError("BROWSER_GONE", review_state.BLOCKER_MESSAGES["BROWSER_GONE"], 409)
    with live.lock:
        db.refresh(session)
        state = review_state.compute(db, user, application, session, live, sync=True)
        if state.blockers:
            raise _blocked(
                "AUTHORIZATION_BLOCKED",
                "JobPilot cannot be authorized to submit this application yet.",
                state.blockers,
            )
        reason: str | None = None
        if state.page_marker != review.page_marker:
            reason = invalidation.NAVIGATION
        elif state.state_hash != review.state_hash:
            reason = invalidation.STATE_CHANGED
        if reason is not None:
            invalidation.invalidate(db, session, reason)
            db.commit()
            raise AppError(
                "STATE_CHANGED",
                "The application changed after your final review"
                + (
                    " (the page was reloaded or changed)"
                    if reason == invalidation.NAVIGATION
                    else ""
                )
                + ". Review it again before authorizing.",
                409,
                {"reason": reason},
            )
        now = _now()
        authorization = SubmissionAuthorization(
            user_id=user.id,
            application_id=application.id,
            session_id=session.id,
            final_review_id=review.id,
            reviewed_state_hash=review.state_hash,
            destination_host=review.destination_host,
            status=SubmissionAuthorizationStatus.AUTHORIZED,
            created_at=now,
            expires_at=now + timedelta(seconds=settings.SUBMISSION_AUTHORIZATION_TTL_SECONDS),
        )
        db.add(authorization)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise AppError(
                "ALREADY_AUTHORIZED",
                "This review was already authorized. Review the application again to authorize "
                "a new submission.",
                409,
            ) from None
        controller.record(
            db,
            session,
            EventType.SUBMISSION_AUTHORIZED,
            authorization_id=str(authorization.id),
            review_id=str(review.id),
        )
        db.commit()
        return authorization


def cancel_authorization(
    db: Session, user: User, application_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    """The user withdraws their authorization before it is used."""
    get_owned_application(db, user, application_id)
    session = controller.owned_session(db, user, application_id, session_id, lock=True)
    invalidation.invalidate(db, session, invalidation.USER_CANCELLED, reviews=False)
    db.commit()


# --- Submitting ----------------------------------------------------------------------------------


class _Recorder:
    """Writes the attempt record in its own short transactions, so that what it records
    (above all: "the final button is about to be pressed") is committed at once -- while
    the request's own transaction still holds the write gate."""

    def __init__(self, db: Session, attempt_id: uuid.UUID) -> None:
        self._bind = db.get_bind()
        self.attempt_id = attempt_id

    def update(self, **values: Any) -> None:
        with Session(bind=self._bind) as own:
            row = own.get(SubmissionAttempt, self.attempt_id, populate_existing=True)
            assert row is not None
            for key, value in values.items():
                setattr(row, key, value)
            own.commit()

    def finish(
        self,
        result: SubmissionResult,
        code: str | None = None,
        *,
        reference: str | None = None,
        summary: str | None = None,
        clear_trigger: bool = False,
    ) -> None:
        values: dict[str, Any] = {
            "result": result,
            "finished_at": _now(),
            "failure_code": code,
            "failure_reason": messages.SUBMISSION_FAILURES.get(code or "") if code else None,
            "confirmation_reference": reference,
            "confirmation_summary": summary[:300] if summary else None,
        }
        if clear_trigger:
            values["submit_triggered_at"] = None
        self.update(**values)


def submit(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    authorization_id: uuid.UUID,
) -> SubmissionAttempt:
    """Submit the application, once, under a valid authorization. The request names only
    the authorization; the destination, the button and the values are the reviewed ones,
    derived and verified here."""
    application = get_owned_application(db, user, application_id)
    _housekeeping(db, application.id)

    # 1. Claim: exactly one request turns the authorization into an attempt.
    session = controller.owned_session(db, user, application_id, session_id, lock=True)
    authorization = db.scalar(
        select(SubmissionAuthorization)
        .where(
            SubmissionAuthorization.id == authorization_id,
            SubmissionAuthorization.user_id == user.id,
            SubmissionAuthorization.application_id == application.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if authorization is None:
        db.rollback()
        raise NotFoundError("Submission authorization not found.")
    if authorization.session_id != session.id:
        db.rollback()
        raise AppError(
            "WRONG_SESSION", "This authorization belongs to another browser session.", 409
        )
    status = authorization.status
    if status is SubmissionAuthorizationStatus.CONSUMED:
        db.rollback()
        raise AppError(
            "AUTHORIZATION_CONSUMED",
            "This authorization was already used. JobPilot never submits twice with one "
            "authorization.",
            409,
        )
    if status is SubmissionAuthorizationStatus.INVALIDATED:
        db.rollback()
        raise AppError(
            "AUTHORIZATION_INVALIDATED",
            "This authorization is no longer valid"
            + (
                f" ({authorization.invalidation_reason.lower().replace('_', ' ')})"
                if authorization.invalidation_reason
                else ""
            )
            + ". Review and authorize again.",
            409,
            {"reason": authorization.invalidation_reason},
        )
    if status is SubmissionAuthorizationStatus.EXPIRED or authorization.expires_at <= _now():
        if status is SubmissionAuthorizationStatus.AUTHORIZED:
            authorization.status = SubmissionAuthorizationStatus.EXPIRED
            db.commit()
        else:
            db.rollback()
        raise AppError(
            "AUTHORIZATION_EXPIRED", "This authorization has expired. Authorize again.", 409
        )
    review = db.get(FinalReview, authorization.final_review_id, populate_existing=True)
    if review is None or review.status is not FinalReviewStatus.ACTIVE:
        db.rollback()
        raise AppError(
            "REVIEW_NOT_CURRENT", "The final review is no longer current. Review again.", 409
        )
    history = review_state.history_blockers(db, application.id)
    if history:
        db.rollback()
        raise _blocked(history[0].code, history[0].message, history)
    if session.status not in _WORKING:
        db.rollback()
        raise AppError(
            "INVALID_SESSION_STATE",
            "The browser assistant is not running, so JobPilot does not submit.",
            409,
            {"status": session.status.value},
        )

    now = _now()
    authorization.status = SubmissionAuthorizationStatus.CONSUMED
    authorization.consumed_at = now
    review.status = FinalReviewStatus.USED
    attempt = SubmissionAttempt(
        user_id=user.id,
        application_id=application.id,
        session_id=session.id,
        authorization_id=authorization.id,
        final_review_id=review.id,
        reviewed_state_hash=authorization.reviewed_state_hash,
        destination_domain=review.destination_domain,
        destination_url=review.destination_url,
        started_at=now,
    )
    db.add(attempt)
    try:
        db.flush()
        controller.record(db, session, EventType.SUBMISSION_STARTED, attempt_id=str(attempt.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(
            "SUBMISSION_IN_PROGRESS", "A submission of this application is in progress.", 409
        ) from None
    attempt_id = attempt.id
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT.add(attempt_id)
    try:
        _run(db, user, application, session.id, authorization, review, attempt_id)
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(attempt_id)
    db.expire_all()
    done = db.get(SubmissionAttempt, attempt_id, populate_existing=True)
    assert done is not None
    return done


def _run(
    db: Session,
    user: User,
    application: Application,
    session_id: uuid.UUID,
    authorization: SubmissionAuthorization,
    review: FinalReview,
    attempt_id: uuid.UUID,
) -> None:
    recorder = _Recorder(db, attempt_id)
    pressed = False
    try:
        session = db.get(AutomationSession, session_id, populate_existing=True)
        assert session is not None
        live = _live(session)
        channel = review_state.channel_of(live) if live is not None else None
        if live is None or channel is None:
            _fail(db, session, recorder, "BROWSER_GONE")
            return
        with live.lock:
            outcome = _press(db, user, application, authorization, review, live, channel, recorder)
            if outcome is None:
                return
            pressed = True
            before, error = outcome
            session = db.get(AutomationSession, session_id, populate_existing=True)
            assert session is not None
            # The assistant's work on this page is over: the browser is the user's again.
            controller._set_status(db, session, S.STOPPED, attention=None)  # noqa: SLF001
            controller.record(db, session, EventType.SUBMISSION_TRIGGERED)
            db.commit()
            live.detached = True
            if error is not None:
                _unknown(db, session, recorder, error)
                return
            _observe(db, application, session, live, channel, before, recorder)
    except Exception:  # noqa: BLE001 - recorded, never retried
        logger.exception("Submission attempt failed unexpectedly")
        db.rollback()
        session = db.get(AutomationSession, session_id, populate_existing=True)
        assert session is not None
        if pressed or _triggered(db, attempt_id):
            _unknown(db, session, recorder, "SUBMIT_ERROR")
        else:
            _fail(db, session, recorder, "SUBMIT_ERROR_BEFORE_PRESS")


def _triggered(db: Session, attempt_id: uuid.UUID) -> bool:
    with Session(bind=db.get_bind()) as own:
        row = own.get(SubmissionAttempt, attempt_id)
        return row is not None and row.submit_triggered_at is not None and row.finished_at is None


def _press(
    db: Session,
    user: User,
    application: Application,
    authorization: SubmissionAuthorization,
    review: FinalReview,
    live: LiveSession,
    channel: FinalSubmissionChannel,
    recorder: _Recorder,
) -> tuple[OutcomeSignals | None, str | None] | None:
    """Verify, then press -- under the write gate. Returns None when nothing was pressed
    (the attempt is then finished as FAILED_BEFORE_SUBMIT), else what the page showed
    before the press and an error code if the press itself reported one."""
    session_id = authorization.session_id
    gate = db.scalar(
        select(AutomationSession.status)
        .where(AutomationSession.id == session_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    session = db.get(AutomationSession, session_id, populate_existing=True)
    assert session is not None
    if gate not in _WORKING:
        db.rollback()
        code = "USER_TOOK_CONTROL" if gate is S.USER_CONTROL else "SESSION_NOT_READY"
        _fail(db, session, recorder, code)
        return None
    state = review_state.compute(
        db, user, application, session, live, sync=False, ignore_attempt=recorder.attempt_id
    )
    failure: str | None = None
    if state.blockers:
        failure = "SUBMISSION_BLOCKED"
    elif state.page_marker != review.page_marker:
        failure = "PAGE_RELOADED"
    elif state.destination_host != authorization.destination_host:
        failure = "WRONG_DESTINATION"
    elif state.state_hash != authorization.reviewed_state_hash:
        failure = "STATE_CHANGED"
    elif state.control is None:
        failure = "SUBMIT_CONTROL_NOT_FOUND"
    if failure is not None:
        db.rollback()
        _fail(db, session, recorder, failure)
        return None
    try:
        before: OutcomeSignals | None = channel.read_outcome()
    except BrowserError:
        before = None
    assert state.control is not None
    permit = issue_permit(
        attempt_id=recorder.attempt_id,
        authorization_id=authorization.id,
        expected_host=state.destination_host,
        control=state.control,
    )
    # Committed before the press: from here on, a crash reads as "maybe submitted".
    recorder.update(submit_triggered_at=_now())
    error: str | None = None
    try:
        channel.submit_final_application(permit)
    except (SubmissionNotTriggered, PermitError) as exc:
        db.rollback()
        code = exc.code if isinstance(exc, SubmissionNotTriggered) else "PERMIT_REFUSED"
        recorder.finish(SubmissionResult.FAILED_BEFORE_SUBMIT, code, clear_trigger=True)
        controller.record(db, session, EventType.SUBMISSION_FAILED, code=code)
        db.commit()
        return None
    except BrowserError as exc:
        error = (
            "BROWSER_DISCONNECTED"
            if exc.code in ("BROWSER_CLOSED", "PAGE_CRASHED")
            else "SUBMIT_ERROR"
        )
    db.commit()  # releases the write gate
    return before, error


def _fail(db: Session, session: AutomationSession, recorder: _Recorder, code: str) -> None:
    recorder.finish(SubmissionResult.FAILED_BEFORE_SUBMIT, code)
    controller.record(db, session, EventType.SUBMISSION_FAILED, code=code)
    db.commit()


def _unknown(db: Session, session: AutomationSession, recorder: _Recorder, code: str) -> None:
    recorder.finish(SubmissionResult.SUBMISSION_UNKNOWN, code)
    controller.record(db, session, EventType.SUBMISSION_UNKNOWN, code=code)
    db.commit()


def _observe(
    db: Session,
    application: Application,
    session: AutomationSession,
    live: LiveSession,
    channel: FinalSubmissionChannel,
    before: OutcomeSignals | None,
    recorder: _Recorder,
) -> None:
    """Watch the page -- reads only -- for evidence of success. No evidence in time, or a
    browser that went away: SUBMISSION_UNKNOWN. The button is never pressed again."""
    deadline = time.monotonic() + settings.SUBMISSION_RESULT_TIMEOUT_SECONDS
    while True:
        try:
            after: OutcomeSignals | None = channel.read_outcome()
        except BrowserError as exc:
            if exc.code in ("BROWSER_CLOSED", "PAGE_CRASHED") or not live.browser.is_alive():
                _unknown(db, session, recorder, "BROWSER_DISCONNECTED")
                return
            after = None  # mid-navigation: look again
        if after is not None:
            found = safety.confirmation_of(after, before)
            if found.confirmed:
                recorder.finish(
                    SubmissionResult.SUCCESS,
                    reference=found.reference,
                    summary=found.summary,
                )
                # Tracking (V0.12): a confirmed submission means APPLIED.
                tracking.record_submission_confirmed(db, recorder.attempt_id)
                controller.record(
                    db, session, EventType.SUBMISSION_CONFIRMED, evidence=found.evidence
                )
                db.commit()
                return
        if time.monotonic() >= deadline:
            _unknown(db, session, recorder, "NO_CONFIRMATION")
            return
        time.sleep(_POLL_SECONDS)


# --- After an unknown result: the user checks -----------------------------------------------------


def list_attempts(db: Session, user: User, application_id: uuid.UUID) -> list[SubmissionAttempt]:
    application = get_owned_application(db, user, application_id)
    _housekeeping(db, application.id)
    return list(
        db.scalars(
            select(SubmissionAttempt)
            .where(
                SubmissionAttempt.application_id == application.id,
                SubmissionAttempt.user_id == user.id,
            )
            .order_by(SubmissionAttempt.started_at.desc())
            .execution_options(populate_existing=True)
        )
    )


def verify_attempt(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    attempt_id: uuid.UUID,
    outcome: str,
) -> SubmissionAttempt:
    """After SUBMISSION_UNKNOWN the user checks with the employer and records what they
    found -- once. NOT_SUBMITTED allows a new review and authorization; SUBMITTED marks
    the application submitted. JobPilot itself never decides this."""
    application = get_owned_application(db, user, application_id)
    attempt = db.scalar(
        select(SubmissionAttempt)
        .where(
            SubmissionAttempt.id == attempt_id,
            SubmissionAttempt.application_id == application.id,
            SubmissionAttempt.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise NotFoundError("Submission attempt not found.")
    if attempt.result is not SubmissionResult.SUBMISSION_UNKNOWN:
        raise AppError(
            "NOT_UNKNOWN", "Only an attempt with an unknown result needs your check.", 409
        )
    if attempt.user_verified_outcome is not None:
        raise AppError("ALREADY_VERIFIED", "You already recorded what you found.", 409)
    attempt.user_verified_outcome = outcome
    attempt.user_verified_at = _now()
    if outcome == "SUBMITTED":
        db.flush()
        tracking.record_user_verified_submission(db, attempt)
    session = db.get(AutomationSession, attempt.session_id)
    if session is not None:
        controller.record(db, session, EventType.SUBMISSION_VERIFIED_BY_USER, outcome=outcome)
    db.commit()
    return attempt


__all__ = [
    "authorize",
    "authorize_consent",
    "cancel_authorization",
    "create_review",
    "current_state",
    "expire_authorizations",
    "latest_review",
    "list_attempts",
    "open_authorization",
    "reconcile_attempts",
    "revoke_consent",
    "submit",
    "verify_attempt",
]
