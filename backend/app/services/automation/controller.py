"""The automation controller (V0.10): the lifecycle of a browser session and every
action the assistant takes -- with the user in control at every step.

    start    open the application page, inspect it, discover its fields
    discover read the page again (navigation, a section that opened, a new field)
    fill     write the *approved* answers into empty fields, verify each, never overwrite
    pause / take_control / resume / stop    the user's controls

The invariants this module enforces (and its tests pin):

- **No submission.** Nothing here can press a button -- the browser interface has no
  click. A control that looks like a final action is only *recognized*
  (`safety.is_submission_label`) so the user can be told to submit manually.
- **Every write is guarded.** Immediately before a field is written, the session's row
  is locked `FOR SHARE` and its status must be RUNNING. `pause` / `take_control` /
  `stop` need the row exclusively, so once one of them has returned, no further write
  can happen (a write already in flight finishes first).
- **The user's edits survive.** A field is written only when it is empty, still holds
  exactly what the assistant wrote earlier, or the user asked for that field to be
  overwritten. Anything else is `USER_MODIFIED`.
- **Only approved values.** The value comes from `AnswerResolver`; an unreviewed
  suggestion is never written. Legal confirmations and consents are never ticked.
- **Stop at a challenge.** A CAPTCHA / security check or a login moves the session to
  BLOCKED and hands the browser to the user; nothing tries to get around it.
- **Any change ends a final review (V0.11).** Taking control, pausing, stopping, resuming,
  filling, a mapping or document decision, a navigation, another website: each invalidates
  the session's final review and submission authorization (`invalidation.invalidate`).
  Submitting itself is not here -- see `services.automation.submission`.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.ai.provider import AIProvider
from app.core.config import settings
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.models.application import Application
from app.models.application_question import ApplicationQuestion
from app.models.automation import (
    ACTIVE_STATUSES,
    AutomationEvent,
    AutomationField,
    AutomationSession,
    AutomationStatus,
    FillStatus,
    MappingStatus,
)
from app.models.user import User
from app.schemas.automation import FillRequest, MappingRequest, ResumeRequest
from app.services.applications.service import get_owned_application
from app.services.automation import (
    documents,
    filler,
    invalidation,
    mapping,
    messages,
    review,
    safety,
)
from app.services.automation.browser.base import BrowserError, PageSnapshot
from app.services.automation.discovery import DiscoveredField, discover_fields
from app.services.automation.events import EventType
from app.services.automation.registry import LiveSession, registry
from app.services.automation.resolver import AnswerResolver
from app.services.documents.service import GenerationInputs

logger = get_logger(__name__)

#: Dynamic forms: fields that appear after an answer are discovered and filled in
#: further rounds, up to this many.
MAX_FILL_ROUNDS = 3

S = AutomationStatus

#: Which statuses each action may start from.
_FROM: dict[str, frozenset[AutomationStatus]] = {
    "pause": frozenset({S.RUNNING, S.COMPLETED}),
    "take_control": frozenset({S.RUNNING, S.PAUSED, S.BLOCKED, S.COMPLETED}),
    "resume": frozenset({S.PAUSED, S.USER_CONTROL, S.COMPLETED}),
    "stop": ACTIVE_STATUSES,
    "work": frozenset({S.RUNNING, S.COMPLETED}),
}

#: The statuses in which the assistant may act on the page.
_WORKING = frozenset({S.RUNNING, S.COMPLETED})


class Interrupted(Exception):
    """The user paused, took control or stopped while the assistant was working."""

    def __init__(self, status: AutomationStatus) -> None:
        super().__init__(status.value)
        self.status = status


# --- Loading and recording -----------------------------------------------------------------------


def owned_session(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    lock: bool = False,
) -> AutomationSession:
    """The user's session of the user's application. Anything else -- another
    user's, another application's, one that doesn't exist -- is the same 404."""
    application = get_owned_application(db, user, application_id)
    statement = (
        select(AutomationSession)
        .where(
            AutomationSession.id == session_id,
            AutomationSession.application_id == application.id,
            AutomationSession.user_id == user.id,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    session = db.scalar(statement)
    if session is None:
        raise NotFoundError("Browser session not found.")
    return session


def record(
    db: Session,
    session: AutomationSession,
    event_type: EventType,
    field_id: uuid.UUID | None = None,
    **data: str | int | bool | None,
) -> None:
    """Append an event. Codes and counts only -- never a value."""
    db.add(
        AutomationEvent(
            session_id=session.id,
            user_id=session.user_id,
            field_id=field_id,
            event_type=event_type.value,
            data=dict(data),
        )
    )


def _set_status(
    db: Session,
    session: AutomationSession,
    status: AutomationStatus,
    *,
    attention: str | None | bool = False,
    error: tuple[str, str] | None = None,
) -> None:
    """Move the session. `attention=False` leaves the attention as it is."""
    session.status = status
    if attention is not False:
        session.attention_code = attention  # type: ignore[assignment]
    if error is not None:
        session.last_error_code, session.last_error = error
    if status in (S.STOPPED, S.FAILED):
        session.ended_at = datetime.now(UTC)


def _live_application(db: Session, user: User, session: AutomationSession) -> Application:
    return get_owned_application(db, user, session.application_id)


@contextmanager
def _browser_errors(db: Session, session: AutomationSession) -> Iterator[None]:
    """A browser that is gone ends the session; any other browser error is reported."""
    try:
        yield
    except BrowserError as exc:
        if exc.code in ("BROWSER_CLOSED", "PAGE_CRASHED"):
            _end_session(db, session, exc.code, exc.message, crashed=exc.code == "PAGE_CRASHED")
            raise AppError(
                "BROWSER_GONE",
                "The browser window was closed, so the assistant stopped. "
                "Start it again to continue.",
                409,
            ) from None
        raise AppError(exc.code, exc.message, 502) from None


def _end_session(
    db: Session, session: AutomationSession, code: str, message: str, *, crashed: bool = False
) -> None:
    if session.status in (S.STOPPED, S.FAILED):
        return
    _set_status(
        db, session, S.FAILED if crashed else S.STOPPED, attention=None, error=(code, message)
    )
    invalidation.invalidate(db, session, invalidation.BROWSER_GONE)
    record(
        db,
        session,
        EventType.SESSION_FAILED if crashed else EventType.AUTOMATION_STOPPED,
        code=code,
    )
    db.commit()
    registry.remove(session.id)


def _live_of(db: Session, session: AutomationSession) -> LiveSession:
    """The running browser of an active session. A session whose browser is gone (the
    server restarted, the window was closed) is ended and reported as such."""
    live = registry.get(session.id)
    if live is None or not live.browser.is_alive():
        _end_session(
            db,
            session,
            "BROWSER_GONE",
            "The browser session ended (the server restarted or the browser was closed).",
        )
        raise AppError(
            "BROWSER_GONE",
            "The browser session ended (the server restarted or the browser was closed). "
            "Start the assistant again.",
            409,
        )
    return live


def _require(session: AutomationSession, action: str) -> None:
    if session.status not in _FROM[action]:
        raise AppError(
            "INVALID_SESSION_STATE",
            _state_hint(session, action),
            409,
            {"status": session.status.value, "action": action},
        )


def _state_hint(session: AutomationSession, action: str) -> str:
    status = session.status
    if status in (S.STOPPED, S.FAILED):
        return "This browser session has ended. Start the assistant again to continue."
    if action == "work":
        if status is S.USER_CONTROL:
            return "You are in control of the browser. Press Resume assistant to let it continue."
        if status is S.PAUSED:
            return "The assistant is paused. Press Resume assistant to let it continue."
        if status is S.BLOCKED:
            return "The page needs you first. Take control of the browser and deal with it."
        return "The assistant is not running."
    if action == "resume" and status is S.BLOCKED:
        return "The page needs you first. Take control of the browser, deal with it, then resume."
    return f"This is not possible while the assistant is {status.value.lower().replace('_', ' ')}."


# --- Starting ------------------------------------------------------------------------------------


def _application_url(db: Session, user: User, application: Application) -> str:
    inputs = GenerationInputs(db, user, application)
    document = inputs.context_document
    url = document.source.application_url if document is not None else None
    try:
        return safety.validate_application_url(
            url, allow_private=settings.AUTOMATION_ALLOW_PRIVATE_HOSTS
        )
    except safety.UnsafeUrl as exc:
        raise AppError(exc.code, exc.message, 409) from None


def active_session(db: Session, application_id: uuid.UUID) -> AutomationSession | None:
    return db.scalar(
        select(AutomationSession)
        .where(
            AutomationSession.application_id == application_id,
            AutomationSession.status.in_(ACTIVE_STATUSES),
        )
        .execution_options(populate_existing=True)
    )


def start(
    db: Session, user: User, application_id: uuid.UUID, provider: AIProvider | None
) -> AutomationSession:
    """Open the application's page in a controlled browser and discover its form.

    The user starts this explicitly; the URL is the application's own (from its
    context snapshot), never one a client supplies."""
    application = get_owned_application(db, user, application_id)
    target = _application_url(db, user, application)

    existing = active_session(db, application.id)
    if existing is not None:
        live = registry.peek(existing.id)
        if live is not None and live.browser.is_alive():
            raise AppError(
                "SESSION_ALREADY_ACTIVE",
                "The assistant is already running for this application.",
                409,
                {"session_id": str(existing.id)},
            )
        # A leftover of a restart: its browser is gone.
        _end_session(db, existing, "BROWSER_GONE", "The browser session ended.")
    if not registry.has_capacity():
        raise AppError(
            "TOO_MANY_BROWSERS",
            "The server is running as many browsers as it allows. Try again in a moment.",
            503,
        )

    session = AutomationSession(
        application_id=application.id,
        user_id=user.id,
        status=S.STARTING,
        target_url=safety.redact_url(target) or target,
        allowed_hosts=[h for h in [safety.host_of(target)] if h],
        browser_mode="headless" if settings.AUTOMATION_BROWSER_HEADLESS else "visible",
        acknowledged_markers=[],
    )
    db.add(session)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppError(
            "SESSION_ALREADY_ACTIVE", "The assistant is already running for this application.", 409
        ) from None
    record(db, session, EventType.SESSION_STARTED)
    db.commit()

    try:
        browser = registry.new_driver().launch(
            allow_private_hosts=settings.AUTOMATION_ALLOW_PRIVATE_HOSTS
        )
    except BrowserError as exc:
        _set_status(db, session, S.FAILED, attention=None, error=(exc.code, exc.message))
        record(db, session, EventType.SESSION_FAILED, code=exc.code)
        db.commit()
        return session
    session.browser_mode = browser.mode
    live = LiveSession(session_id=session.id, browser=browser)
    registry.add(live)

    try:
        browser.open(target, timeout_seconds=settings.AUTOMATION_NAVIGATION_TIMEOUT_SECONDS)
    except BrowserError as exc:
        if exc.code == "ACCESS_RESTRICTED":
            session.started_at = datetime.now(UTC)
            _set_status(db, session, S.BLOCKED, attention=messages.ACCESS_RESTRICTED)
            record(db, session, EventType.SECURITY_CHALLENGE, code=exc.code)
            db.commit()
            return session
        _set_status(db, session, S.FAILED, attention=None, error=(exc.code, exc.message))
        record(db, session, EventType.SESSION_FAILED, code=exc.code)
        db.commit()
        registry.remove(session.id)
        return session

    session.started_at = datetime.now(UTC)
    _set_status(db, session, S.RUNNING, attention=None)
    db.commit()
    with live.lock:
        try:
            _inspect(db, user, application, session, live, provider)
        except AppError:
            raise
        except BrowserError as exc:
            _fail_inspect(db, session, exc)
    return session


def _fail_inspect(db: Session, session: AutomationSession, exc: BrowserError) -> None:
    if exc.code in ("BROWSER_CLOSED", "PAGE_CRASHED"):
        _end_session(db, session, exc.code, exc.message, crashed=exc.code == "PAGE_CRASHED")
        return
    _set_status(db, session, S.FAILED, attention=None, error=(exc.code, exc.message))
    record(db, session, EventType.SESSION_FAILED, code=exc.code)
    db.commit()
    registry.remove(session.id)


# --- Inspecting the page -------------------------------------------------------------------------


@dataclass
class Inspection:
    #: running | blocked | paused
    outcome: str
    fields: list[AutomationField] = field(default_factory=list)


def _inspect(
    db: Session,
    user: User,
    application: Application,
    session: AutomationSession,
    live: LiveSession,
    provider: AIProvider | None,
) -> Inspection:
    """Read the page, make sure it is somewhere the assistant may work, and bring the
    session's fields up to date. May move the session to BLOCKED (a security check or a
    login) or PAUSED (an unexpected website) -- then it does not touch the fields."""
    snapshot = live.browser.page.snapshot(max_controls=settings.AUTOMATION_MAX_FIELDS)
    session.current_url = safety.redact_url(snapshot.url)
    session.page_title = snapshot.title[:300] or None

    host = safety.host_of(snapshot.url)
    if not safety.host_in_scope(host, list(session.allowed_hosts)):
        _set_status(db, session, S.PAUSED, attention=messages.UNEXPECTED_DOMAIN)
        record(db, session, EventType.DOMAIN_CHANGED)
        invalidation.invalidate(db, session, invalidation.DOMAIN_CHANGED)
        db.commit()
        return Inspection("paused")

    pending = safety.pending_markers(snapshot, list(session.acknowledged_markers))
    if pending:
        _set_status(db, session, S.BLOCKED, attention=messages.SECURITY_CHALLENGE)
        record(db, session, EventType.SECURITY_CHALLENGE, markers=",".join(sorted(pending)))
        invalidation.invalidate(db, session, messages.SECURITY_CHALLENGE)
        db.commit()
        return Inspection("blocked")
    if snapshot.signals.has_password:
        _set_status(db, session, S.BLOCKED, attention=messages.LOGIN_REQUIRED)
        record(db, session, EventType.LOGIN_REQUIRED)
        invalidation.invalidate(db, session, messages.LOGIN_REQUIRED)
        db.commit()
        return Inspection("blocked")

    detected = safety.submission_detected(snapshot)
    if detected and not session.submit_detected:
        record(db, session, EventType.SUBMIT_DETECTED)
    session.submit_detected = detected

    record(db, session, EventType.DISCOVERY_STARTED)
    fields = _sync_fields(db, user, application, session, snapshot, provider)
    if session.attention_code in (messages.NO_FORM_FOUND, None) and session.status in _WORKING:
        session.attention_code = None if fields else messages.NO_FORM_FOUND
    db.commit()
    return Inspection("running", fields)


def _user_mappings(db: Session, application_id: uuid.UUID) -> dict[str, AutomationField]:
    """The latest decision the user made about each field of this application in an
    earlier session, by field key."""
    rows = db.scalars(
        select(AutomationField)
        .join(AutomationSession, AutomationField.session_id == AutomationSession.id)
        .where(
            AutomationSession.application_id == application_id,
            AutomationField.mapping_method == "USER",
        )
        .order_by(AutomationField.updated_at.asc())
    )
    return {row.field_key: row for row in rows}


def _sync_fields(
    db: Session,
    user: User,
    application: Application,
    session: AutomationSession,
    snapshot: PageSnapshot,
    provider: AIProvider | None,
) -> list[AutomationField]:
    """Reconcile the page's controls with the session's field records, and map the
    new ones to questions."""
    discovered = discover_fields(snapshot)
    now = datetime.now(UTC)
    rows = {
        row.field_key: row
        for row in db.scalars(
            select(AutomationField).where(AutomationField.session_id == session.id)
        )
    }
    questions = list(
        db.scalars(
            select(ApplicationQuestion)
            .where(ApplicationQuestion.application_id == application.id)
            .options(selectinload(ApplicationQuestion.answer))
            .order_by(ApplicationQuestion.sort_order, ApplicationQuestion.created_at)
            .execution_options(populate_existing=True)
        )
    )
    by_id = {q.id: q for q in questions}
    claimed = {row.question_id for row in rows.values() if row.question_id is not None}
    mapper = mapping.FieldMapper(
        questions, provider if _ai_usable(provider) else None, claimed=claimed
    )
    prior = _user_mappings(db, application.id)

    seen: set[str] = set()
    ordered: list[AutomationField] = []
    for item in discovered:
        seen.add(item.field_key)
        row = rows.get(item.field_key)
        if row is None:
            row = AutomationField(
                session_id=session.id,
                user_id=user.id,
                field_key=item.field_key,
                first_seen_at=now,
                last_seen_at=now,
                input_type=item.input_type,
                label=item.label,
                position=item.position,
                meta={},
                locator={},
            )
            db.add(row)
            rows[item.field_key] = row
            _copy_page_data(row, item, now)
            db.flush()
            record(db, session, EventType.FIELD_DISCOVERED, row.id, input_type=item.input_type)
        else:
            _copy_page_data(row, item, now)
        _map_field(db, user, application, session, row, item, mapper, prior, by_id)
        ordered.append(row)

    for key, row in rows.items():
        if key not in seen:
            row.present = False
    db.flush()
    return ordered


def _ai_usable(provider: AIProvider | None) -> bool:
    return provider is not None and provider.name not in ("disabled", "unknown")


def _copy_page_data(row: AutomationField, item: DiscoveredField, now: datetime) -> None:
    row.position = item.position
    row.input_type = item.input_type
    row.label = item.label[:500]
    row.name = item.name
    row.dom_id = item.dom_id
    row.form_context = item.form_context
    row.required = item.required
    row.max_length = item.max_length
    row.options = item.options if item.options else None
    row.meta = item.meta
    row.locator = item.locator.as_dict()
    row.present = True
    row.last_seen_at = now
    row.user_action_reason = item.user_action_reason
    row.consent_text = (
        review.consent_text_of(item) if item.user_action_reason == "LEGAL_CONSENT" else None
    )
    row.detected_document_type = item.detected_document_type
    if item.user_action_reason is not None and row.fill_status is FillStatus.NOT_FILLED:
        row.fill_status = FillStatus.USER_ACTION_REQUIRED


def _map_field(
    db: Session,
    user: User,
    application: Application,
    session: AutomationSession,
    row: AutomationField,
    item: DiscoveredField,
    mapper: mapping.FieldMapper,
    prior: dict[str, AutomationField],
    questions: dict[uuid.UUID, ApplicationQuestion],
) -> None:
    """Give a field its mapping the first time it is seen; keep it up to date after."""
    if item.is_file:
        if row.mapping_method is None:
            known = item.detected_document_type is not None
            row.mapping_status = MappingStatus.AUTO_MAPPED if known else MappingStatus.NEEDS_REVIEW
            row.mapping_method = "LABEL"
            row.mapping_confidence = "HIGH" if known else "LOW"
            row.mapping_reason = "DOCUMENT_TYPE" if known else "FILE_UNCLEAR"
        return
    if not item.has_question:
        return
    if row.mapping_method is not None:
        question = questions.get(row.question_id) if row.question_id else None
        if question is not None and question.source == mapping.BROWSER_SOURCE:
            outcome = mapping.refresh_browser_question(question, item)
            if outcome.conflict and row.mapping_status is MappingStatus.AUTO_MAPPED:
                row.mapping_status = MappingStatus.NEEDS_REVIEW
                row.mapping_reason = "FIELD_CHANGED"
            elif outcome.updated:
                record(db, session, EventType.FIELD_MAPPED, row.id, change="question_updated")
        return
    if not item.label.strip():
        row.mapping_status = MappingStatus.UNMAPPED
        row.mapping_method = "NONE"
        row.mapping_reason = "NO_LABEL"
        return

    earlier = prior.get(row.field_key)
    if earlier is not None:
        _apply_earlier_decision(row, earlier, questions, mapper)
        if row.mapping_method is not None:
            record(db, session, EventType.FIELD_MAPPED, row.id, method="USER")
            return

    decision = mapper.decide(item)
    row.mapping_status = decision.status
    row.mapping_method = decision.method
    row.mapping_confidence = decision.confidence
    row.mapping_reason = decision.reason
    row.mapping_provenance = decision.provenance
    row.proposed_question_id = decision.proposed.id if decision.proposed is not None else None
    if decision.create:
        created = mapping.create_browser_question(db, user.id, application.id, item)
        if created is None:
            row.mapping_status = MappingStatus.UNMAPPED
            row.mapping_confidence = None
            row.mapping_reason = "TOO_MANY_QUESTIONS"
            row.question_id = None
        else:
            questions[created.id] = created
            mapper.claimed.add(created.id)
            row.question_id = created.id
    else:
        row.question_id = decision.question.id if decision.question is not None else None
    record(
        db,
        session,
        EventType.FIELD_MAPPED,
        row.id,
        method=row.mapping_method,
        status=row.mapping_status.value,
        confidence=row.mapping_confidence,
    )


def _apply_earlier_decision(
    row: AutomationField,
    earlier: AutomationField,
    questions: dict[uuid.UUID, ApplicationQuestion],
    mapper: mapping.FieldMapper,
) -> None:
    """The user decided about this field in an earlier session: that stands."""
    if earlier.mapping_status is MappingStatus.UNMAPPED:
        row.mapping_status = MappingStatus.UNMAPPED
        row.mapping_method = "USER"
        row.mapping_reason = "LEFT_UNMAPPED"
        return
    question = questions.get(earlier.question_id) if earlier.question_id else None
    if question is None or question.id in mapper.claimed:
        return
    row.mapping_status = MappingStatus.USER_CONFIRMED
    row.mapping_method = "USER"
    row.mapping_confidence = "HIGH"
    row.mapping_reason = "CONFIRMED_EARLIER"
    row.question_id = question.id
    mapper.claimed.add(question.id)


# --- The user's controls -------------------------------------------------------------------------


def pause(
    db: Session, user: User, application_id: uuid.UUID, session_id: uuid.UUID
) -> AutomationSession:
    session = owned_session(db, user, application_id, session_id, lock=True)
    _require(session, "pause")
    _set_status(db, session, S.PAUSED)
    record(db, session, EventType.AUTOMATION_PAUSED)
    invalidation.invalidate(db, session, invalidation.PAUSED)
    db.commit()
    return session


def take_control(
    db: Session, user: User, application_id: uuid.UUID, session_id: uuid.UUID
) -> AutomationSession:
    """The browser is the user's. From the moment this returns, the assistant writes
    nothing until they resume it."""
    session = owned_session(db, user, application_id, session_id, lock=True)
    _require(session, "take_control")
    _set_status(db, session, S.USER_CONTROL)
    record(db, session, EventType.USER_TAKEOVER)
    # Human control wins: whatever was reviewed or authorized is no longer what will be sent.
    invalidation.invalidate(db, session, invalidation.USER_TAKEOVER)
    db.commit()
    return session


def stop(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    close_browser: bool,
) -> AutomationSession:
    session = owned_session(db, user, application_id, session_id, lock=True)
    _require(session, "stop")
    _set_status(db, session, S.STOPPED, attention=None)
    record(db, session, EventType.AUTOMATION_STOPPED, closed=close_browser)
    invalidation.invalidate(db, session, invalidation.STOPPED)
    db.commit()
    live = registry.peek(session.id)
    if live is not None:
        if close_browser or not live.browser.is_alive():
            registry.remove(session.id)
        else:
            live.detached = True  # open for manual use; the assistant no longer touches it
    return session


def resume(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: ResumeRequest | None,
    provider: AIProvider | None,
) -> AutomationSession:
    """Give the browser back to the assistant. It first looks at the page; it does not
    refill anything on its own, and what the user changed is kept."""
    session = owned_session(db, user, application_id, session_id, lock=True)
    _require(session, "resume")
    if session.attention_code == messages.UNEXPECTED_DOMAIN and session.status is S.PAUSED:
        raise AppError(
            "CONFIRM_DOMAIN_FIRST",
            messages.ATTENTION_MESSAGES[messages.UNEXPECTED_DOMAIN],
            409,
            {"attention": messages.UNEXPECTED_DOMAIN},
        )
    db.commit()  # release the row lock: the page work below takes its own
    session = owned_session(db, user, application_id, session_id)
    live = _live_of(db, session)
    application = _live_application(db, user, session)
    with live.lock, _browser_errors(db, session):
        previous = session.status
        if previous is S.USER_CONTROL and session.attention_code == messages.SECURITY_CHALLENGE:
            # The user dealt with the check themselves: do not stop for the same one again.
            snapshot = live.browser.page.snapshot(max_controls=settings.AUTOMATION_MAX_FIELDS)
            known = set(session.acknowledged_markers)
            known.update(
                m for m in snapshot.signals.challenge_markers if m not in safety.GATE_MARKERS
            )
            session.acknowledged_markers = sorted(known)
        _set_status(db, session, S.RUNNING, attention=None)
        record(db, session, EventType.AUTOMATION_RESUMED)
        invalidation.invalidate(db, session, invalidation.RESUMED)
        db.commit()
        inspection = _inspect(db, user, application, session, live, provider)
        if inspection.outcome == "running":
            _reconcile(db, session, live, inspection.fields)
            db.commit()
            filled = request is not None and request.fill
            if filled:
                _fill(db, user, application, session, live, provider, FillRequest())
            _settle(db, user, application, session, may_complete=filled)
    return session


def confirm_domain(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    host: str,
    provider: AIProvider | None,
) -> AutomationSession:
    """The user says the website the browser is on belongs to this application."""
    session = owned_session(db, user, application_id, session_id)
    if session.status not in (S.PAUSED, S.USER_CONTROL):
        raise AppError("INVALID_SESSION_STATE", _state_hint(session, "resume"), 409)
    live = _live_of(db, session)
    current = safety.host_of(live.browser.page.url)
    if current is None or current != host.strip().lower():
        raise AppError(
            "DOMAIN_CHANGED_AGAIN",
            "The browser is no longer on that website. Check the page and try again.",
            409,
        )
    if current not in session.allowed_hosts:
        session.allowed_hosts = [*session.allowed_hosts, current]
    session.attention_code = None
    record(db, session, EventType.DOMAIN_CONFIRMED)
    invalidation.invalidate(db, session, invalidation.DOMAIN_CHANGED)
    db.commit()
    return resume(db, user, application_id, session_id, ResumeRequest(), provider)


# --- Discovering ---------------------------------------------------------------------------------


def discover(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    provider: AIProvider | None,
) -> AutomationSession:
    """Read the page again: after the user opened the next step, a section expanded or
    a field appeared. Reads only -- it writes nothing to the page."""
    session = owned_session(db, user, application_id, session_id)
    _require(session, "work")
    live = _live_of(db, session)
    application = _live_application(db, user, session)
    with live.lock, _browser_errors(db, session):
        session = owned_session(db, user, application_id, session_id)
        _require(session, "work")
        inspection = _inspect(db, user, application, session, live, provider)
        if inspection.outcome == "running":
            _reconcile(db, session, live, inspection.fields)
            db.commit()
            _settle(db, user, application, session, may_complete=False)
    return session


def _reconcile(
    db: Session, session: AutomationSession, live: LiveSession, fields: list[AutomationField]
) -> None:
    """Notice what the user changed since the assistant last wrote: read-only."""
    for row in fields:
        if row.last_agent_value_hash is None or row.fill_status is not FillStatus.FILLED:
            continue
        _, current = filler.read_current(live.browser.page, row)
        if filler.hash_value(row.id, current) != row.last_agent_value_hash:
            row.fill_status = FillStatus.USER_MODIFIED
            record(db, session, EventType.USER_MODIFIED, row.id)


# --- Filling -------------------------------------------------------------------------------------


@dataclass
class _Pass:
    db: Session
    user: User
    application: Application
    session: AutomationSession
    live: LiveSession
    resolver: AnswerResolver
    questions: dict[uuid.UUID, ApplicationQuestion]
    overwrite: set[uuid.UUID]
    only: set[uuid.UUID] | None
    done: set[uuid.UUID] = field(default_factory=set)


@contextmanager
def _writing_allowed(db: Session, session_id: uuid.UUID) -> Iterator[None]:
    """The gate in front of every write: the session must be RUNNING *now*. The row is
    locked FOR SHARE until the caller commits, so a pause / take-control / stop (which
    need it exclusively) cannot slip in between the check and the write."""
    status = db.scalar(
        select(AutomationSession.status)
        .where(AutomationSession.id == session_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if status is None or status is not S.RUNNING:
        db.rollback()
        raise Interrupted(status or S.STOPPED)
    yield


def fill(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    request: FillRequest | None,
    provider: AIProvider | None,
) -> AutomationSession:
    """Fill the approved answers into the page. Nothing else: no click, no submission."""
    session = owned_session(db, user, application_id, session_id)
    _require(session, "work")
    live = _live_of(db, session)
    application = _live_application(db, user, session)
    with live.lock, _browser_errors(db, session):
        session = owned_session(db, user, application_id, session_id)
        _require(session, "work")
        if session.status is S.COMPLETED:
            _set_status(db, session, S.RUNNING)
        # A fill pass may change what would be submitted.
        invalidation.invalidate(db, session, invalidation.FILL)
        db.commit()
        _fill(db, user, application, session, live, provider, request or FillRequest())
        _settle(db, user, application, session)
    return session


def _fill(
    db: Session,
    user: User,
    application: Application,
    session: AutomationSession,
    live: LiveSession,
    provider: AIProvider | None,
    request: FillRequest,
) -> None:
    only = set(request.field_ids) if request.field_ids is not None else None
    overwrite = set(request.overwrite_field_ids)
    context: _Pass | None = None
    for _round in range(MAX_FILL_ROUNDS):
        inspection = _inspect(db, user, application, session, live, provider)
        if inspection.outcome != "running":
            return
        questions = {
            q.id: q
            for q in db.scalars(
                select(ApplicationQuestion)
                .where(ApplicationQuestion.application_id == application.id)
                .options(selectinload(ApplicationQuestion.answer))
                .execution_options(populate_existing=True)
            )
        }
        resolver = AnswerResolver(db, user, application)
        if context is None:
            context = _Pass(
                db, user, application, session, live, resolver, questions, overwrite, only
            )
        else:
            context.resolver, context.questions = resolver, questions
        wrote = False
        try:
            for row in inspection.fields:
                if row.id in context.done:
                    continue
                if only is not None and row.id not in only:
                    continue
                outcome = _fill_field(context, row)
                if outcome != "skipped":
                    context.done.add(row.id)
                wrote = wrote or outcome == "filled"
        except Interrupted as stop:
            logger.info("Fill interrupted: %s", stop.status.value)
            return
        if not wrote:
            return  # no new fields appeared that could be filled


def _mark(
    db: Session,
    pass_: _Pass,
    row: AutomationField,
    status: FillStatus,
    *,
    error: str | None = None,
    event: EventType | None = None,
) -> None:
    row.fill_status = status
    row.fill_error_code = error
    if event is not None:
        record(db, pass_.session, event, row.id, **({"code": error} if error else {}))
    db.commit()


def _fill_field(pass_: _Pass, row: AutomationField) -> str:
    """One field: skipped, left to the user, already right, filled or failed."""
    db = pass_.db
    if row.user_action_reason is not None:
        if row.fill_status is not FillStatus.USER_ACTION_REQUIRED:
            _mark(db, pass_, row, FillStatus.USER_ACTION_REQUIRED, event=EventType.FIELD_SKIPPED)
        return "user_action"
    if row.input_type == "file":
        return _fill_file(pass_, row)

    trusted = row.mapping_status is MappingStatus.USER_CONFIRMED or (
        row.mapping_status is MappingStatus.AUTO_MAPPED and row.mapping_confidence == "HIGH"
    )
    if not trusted or row.question_id is None:
        return "skipped"
    question = pass_.questions.get(row.question_id)
    if question is None:
        return "skipped"
    answer = pass_.resolver.resolve(question)
    if not answer.approved:
        # No approved answer: nothing is written, whatever the suggestion says.
        return "skipped"
    record(db, pass_.session, EventType.ANSWER_APPROVED, row.id, source=answer.source)

    plan = filler.plan_value(row, answer)
    if isinstance(plan, filler.Unfillable):
        row.fill_error_code = plan.code
        if row.fill_status is FillStatus.FILLED:
            row.fill_status = FillStatus.NOT_FILLED
        record(db, pass_.session, EventType.FIELD_SKIPPED, row.id, code=plan.code)
        db.commit()
        return "skipped"
    return _write_verified(pass_, row, plan, source=answer.source or "USER")


def _write_verified(
    pass_: _Pass, row: AutomationField, plan: filler.Intended, *, source: str
) -> str:
    db = pass_.db
    page = pass_.live.browser.page
    with _writing_allowed(db, pass_.session.id):
        try:
            value, current = filler.read_current(page, row)
            if not value.found:
                _mark(
                    db,
                    pass_,
                    row,
                    FillStatus.FILL_FAILED,
                    error="FIELD_NOT_FOUND",
                    event=EventType.FILL_FAILED,
                )
                return "failed"
            decision = filler.decide(row, plan.canon, current, overwrite=row.id in pass_.overwrite)
            if decision.action == "already_correct":
                row.fill_status = FillStatus.FILLED
                row.fill_error_code = None
                row.last_agent_value_hash = filler.hash_value(row.id, current)
                row.last_value_source = source[:20]
                db.commit()
                return "already_correct"
            if decision.action == "user_modified":
                if row.fill_status is not FillStatus.USER_MODIFIED:
                    record(db, pass_.session, EventType.USER_MODIFIED, row.id)
                row.fill_status = FillStatus.USER_MODIFIED
                row.fill_error_code = None
                db.commit()
                return "user_modified"

            try:
                filler.write(page, row, plan)
            except BrowserError as exc:
                if exc.code in ("BROWSER_CLOSED", "PAGE_CRASHED"):
                    raise
                code = exc.code if exc.code in messages.FILL_ERRORS else "WRITE_FAILED"
                _mark(
                    db, pass_, row, FillStatus.FILL_FAILED, error=code, event=EventType.FILL_FAILED
                )
                return "failed"
            verified = filler.verify(page, row, plan)
            row.last_agent_value_hash = filler.hash_value(row.id, verified.shown)
            row.last_value_source = source[:20]
            if verified.ok:
                row.fill_status = FillStatus.FILLED
                row.fill_error_code = None
                row.last_filled_at = datetime.now(UTC)
                record(db, pass_.session, EventType.FIELD_FILLED, row.id, source=source)
                record(db, pass_.session, EventType.FIELD_VERIFIED, row.id)
                db.commit()
                return "filled"
            row.fill_status = FillStatus.FILL_FAILED
            row.fill_error_code = "VALUE_MISMATCH"
            record(db, pass_.session, EventType.FILL_FAILED, row.id, code="VALUE_MISMATCH")
            db.commit()
            return "failed"
        except BrowserError as exc:
            db.rollback()
            if exc.code in ("BROWSER_CLOSED", "PAGE_CRASHED"):
                raise
            code = exc.code if exc.code in messages.FILL_ERRORS else "WRITE_FAILED"
            row = db.get(AutomationField, row.id) or row
            _mark(db, pass_, row, FillStatus.FILL_FAILED, error=code, event=EventType.FILL_FAILED)
            return "failed"


def _fill_file(pass_: _Pass, row: AutomationField) -> str:
    """Attach the document the user chose -- and only that, only after they confirmed it."""
    db = pass_.db
    if not (row.document_confirmed and row.document_type and row.document_id):
        # Waiting for "Use this file". A field that does not say which document it wants is
        # left to the user outright: JobPilot never guesses.
        if row.detected_document_type is None and row.fill_status is FillStatus.NOT_FILLED:
            _mark(
                db,
                pass_,
                row,
                FillStatus.USER_ACTION_REQUIRED,
                error="FILE_UNCLEAR",
                event=EventType.FIELD_SKIPPED,
            )
        return "skipped"
    choice = documents.find_choice(
        db, pass_.user, pass_.application, row.document_type, row.document_id
    )
    if choice is None:
        _mark(
            db,
            pass_,
            row,
            FillStatus.FILL_FAILED,
            error="DOCUMENT_UNAVAILABLE",
            event=EventType.FILL_FAILED,
        )
        return "failed"
    try:
        file_name, mime, data = documents.render(db, pass_.user, pass_.application, choice)
    except documents.DocumentUnavailable:
        _mark(
            db,
            pass_,
            row,
            FillStatus.FILL_FAILED,
            error="DOCUMENT_UNAVAILABLE",
            event=EventType.FILL_FAILED,
        )
        return "failed"
    accept = (row.meta or {}).get("accept")
    if not documents.file_accepted(accept, file_name, mime):
        _mark(
            db,
            pass_,
            row,
            FillStatus.USER_ACTION_REQUIRED,
            error="FILE_TYPE_NOT_ACCEPTED",
            event=EventType.FIELD_SKIPPED,
        )
        return "user_action"
    plan = filler.intended_file(file_name, len(data))
    page = pass_.live.browser.page
    with _writing_allowed(db, pass_.session.id):
        try:
            value, current = filler.read_current(page, row)
            if not value.found:
                _mark(
                    db,
                    pass_,
                    row,
                    FillStatus.FILL_FAILED,
                    error="FIELD_NOT_FOUND",
                    event=EventType.FILL_FAILED,
                )
                return "failed"
            decision = filler.decide(row, plan.canon, current, overwrite=row.id in pass_.overwrite)
            if decision.action == "already_correct":
                row.fill_status = FillStatus.FILLED
                row.fill_error_code = None
                row.last_agent_value_hash = filler.hash_value(row.id, current)
                row.last_value_source = "DOCUMENT"
                db.commit()
                return "already_correct"
            if decision.action == "user_modified":
                if row.fill_status is not FillStatus.USER_MODIFIED:
                    record(db, pass_.session, EventType.USER_MODIFIED, row.id)
                row.fill_status = FillStatus.USER_MODIFIED
                db.commit()
                return "user_modified"
            try:
                page.write_file(filler.locator_of(row), file_name, mime, data)
            except BrowserError as exc:
                if exc.code in ("BROWSER_CLOSED", "PAGE_CRASHED"):
                    raise
                _mark(
                    db,
                    pass_,
                    row,
                    FillStatus.FILL_FAILED,
                    error="FILE_REJECTED",
                    event=EventType.FILL_FAILED,
                )
                return "failed"
            verified = filler.verify(page, row, plan)
            row.last_agent_value_hash = filler.hash_value(row.id, verified.shown)
            row.last_value_source = "DOCUMENT"
            if verified.ok:
                row.fill_status = FillStatus.FILLED
                row.fill_error_code = None
                row.last_filled_at = datetime.now(UTC)
                record(db, pass_.session, EventType.FIELD_FILLED, row.id, source="DOCUMENT")
                record(db, pass_.session, EventType.FIELD_VERIFIED, row.id)
                db.commit()
                return "filled"
            row.fill_status = FillStatus.FILL_FAILED
            row.fill_error_code = "FILE_REJECTED"
            record(db, pass_.session, EventType.FILL_FAILED, row.id, code="FILE_REJECTED")
            db.commit()
            return "failed"
        except BrowserError:
            db.rollback()
            raise


def _settle(
    db: Session,
    user: User,
    application: Application,
    session: AutomationSession,
    *,
    may_complete: bool = True,
) -> None:
    """After a pass: COMPLETED when the page offers a final submission control and
    there is nothing left the assistant could still do; RUNNING otherwise. Without
    `may_complete` (reading the page only) a session is never *moved to* COMPLETED."""
    db.expire(session)
    session = owned_session(db, user, application.id, session.id)
    if session.status not in _WORKING:
        return
    rows = list(
        db.scalars(
            select(AutomationField).where(
                AutomationField.session_id == session.id, AutomationField.present.is_(True)
            )
        )
    )
    resolver = AnswerResolver(db, user, application)
    questions = {
        q.id: q
        for q in db.scalars(
            select(ApplicationQuestion)
            .where(ApplicationQuestion.application_id == application.id)
            .options(selectinload(ApplicationQuestion.answer))
        )
    }
    pending = False
    for row in rows:
        if row.user_action_reason is not None:
            continue
        if row.fill_status in (FillStatus.FILLED, FillStatus.USER_MODIFIED, FillStatus.FILL_FAILED):
            continue
        if row.input_type == "file":
            pending = pending or bool(row.document_confirmed and row.document_id)
            continue
        trusted = row.mapping_status is MappingStatus.USER_CONFIRMED or (
            row.mapping_status is MappingStatus.AUTO_MAPPED and row.mapping_confidence == "HIGH"
        )
        question = questions.get(row.question_id) if row.question_id else None
        if trusted and question is not None and resolver.resolve(question).approved:
            if not isinstance(
                filler.plan_value(row, resolver.resolve(question)), filler.Unfillable
            ):
                pending = True
    if may_complete and session.submit_detected and not pending:
        if session.status is not S.COMPLETED:
            _set_status(db, session, S.COMPLETED)
    elif session.status is S.COMPLETED:
        _set_status(db, session, S.RUNNING)
    db.commit()


# --- Keeping the session's view of the browser current -------------------------------------------


def refresh(db: Session, user: User, session: AutomationSession) -> None:
    """Bring a session's status up to date with its browser: ended when the browser is
    gone, current page noted, and -- while the assistant is working -- paused when the
    page has moved to an unrelated website. Cheap: reads the URL, nothing else."""
    if session.status not in ACTIVE_STATUSES:
        return
    live = registry.get(session.id)
    if live is None and session.status is S.STARTING:
        age = datetime.now(UTC) - session.created_at
        if age.total_seconds() < 120:
            return  # the browser is still being started by another request
    if live is None or not live.browser.is_alive():
        _end_session(
            db,
            session,
            "BROWSER_GONE",
            "The browser session ended (the server restarted or the browser was closed).",
        )
        return
    url = live.browser.page.url
    redacted = safety.redact_url(url)
    changed = redacted != session.current_url
    if changed:
        session.current_url = redacted
    if session.status in _WORKING and not safety.host_in_scope(
        safety.host_of(url), list(session.allowed_hosts)
    ):
        _set_status(db, session, S.PAUSED, attention=messages.UNEXPECTED_DOMAIN)
        record(db, session, EventType.DOMAIN_CHANGED)
        invalidation.invalidate(db, session, invalidation.DOMAIN_CHANGED)
        changed = True
    # V0.11: a navigation or reload since the final review means the reviewed page is gone.
    channel = review.channel_of(live)
    if channel is not None:
        try:
            marker = channel.page_marker()
        except BrowserError:
            marker = ""
        if invalidation.invalidate_if_navigated(db, session, marker):
            changed = True
    if changed:
        db.commit()


# --- Mapping and documents: the user's decisions -------------------------------------------------


def owned_field(
    db: Session, user: User, session: AutomationSession, field_id: uuid.UUID
) -> AutomationField:
    row = db.scalar(
        select(AutomationField)
        .where(
            AutomationField.id == field_id,
            AutomationField.session_id == session.id,
            AutomationField.user_id == user.id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError("Field not found.")
    return row


def _discard_if_orphan(
    db: Session, application_id: uuid.UUID, question: ApplicationQuestion | None
) -> None:
    """A browser question the user moved a field away from, with nothing on it, is
    removed (it only existed for that field)."""
    if question is None or question.source != mapping.BROWSER_SOURCE:
        return
    answer = question.answer
    if answer is None or answer.status.value != "NOT_STARTED" or question.generations:
        return
    still_used = db.scalar(
        select(AutomationField.id).where(AutomationField.question_id == question.id).limit(1)
    )
    if still_used is None:
        db.delete(question)


def set_mapping(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
    request: MappingRequest,
) -> AutomationField:
    """The user's decision about a field's question. Their word is final and is
    remembered for later sessions of this application."""
    session = owned_session(db, user, application_id, session_id)
    row = owned_field(db, user, session, field_id)
    application = get_owned_application(db, user, application_id)
    if row.input_type == "file" or row.user_action_reason is not None:
        raise AppError(
            "NOT_A_QUESTION",
            "This field is not answered from a question: only you can fill it"
            + (" (choose its file instead)." if row.input_type == "file" else "."),
            409,
        )
    previous = (
        db.scalar(
            select(ApplicationQuestion)
            .where(ApplicationQuestion.id == row.question_id)
            .options(selectinload(ApplicationQuestion.answer))
        )
        if row.question_id
        else None
    )

    if request.action == "confirm":
        target_id = row.proposed_question_id or row.question_id
        if target_id is None:
            raise AppError("NO_PROPOSAL", "There is no question to confirm for this field.", 409)
        row.question_id = target_id
    elif request.action == "change":
        target = db.scalar(
            select(ApplicationQuestion).where(
                ApplicationQuestion.id == request.question_id,
                ApplicationQuestion.application_id == application.id,
                ApplicationQuestion.user_id == user.id,
            )
        )
        if target is None:
            raise NotFoundError("Question not found.")
        row.question_id = target.id
    elif request.action == "unmap":
        row.question_id = None
        row.mapping_status = MappingStatus.UNMAPPED
        row.mapping_method = "USER"
        row.mapping_confidence = None
        row.mapping_reason = "LEFT_UNMAPPED"
        row.proposed_question_id = None
    else:  # create
        if not row.label.strip():
            raise AppError(
                "NO_LABEL",
                "This field has no label, so there is no question to add for it. Choose an "
                "existing question instead, or fill the field yourself.",
                409,
            )
        item = DiscoveredField(
            field_key=row.field_key,
            position=row.position,
            input_type=row.input_type,
            label=row.label,
            name=row.name,
            dom_id=row.dom_id,
            form_context=row.form_context,
            required=row.required,
            max_length=row.max_length,
            options=list(row.options or []),
            meta=dict(row.meta or {}),
            locator=filler.locator_of(row),
        )
        existing = db.scalar(
            select(ApplicationQuestion).where(
                ApplicationQuestion.application_id == application.id,
                ApplicationQuestion.external_key == mapping.external_key_of(row.field_key),
            )
        )
        created = existing or mapping.create_browser_question(db, user.id, application.id, item)
        if created is None:
            raise AppError(
                "TOO_MANY_QUESTIONS",
                "This application already has the maximum number of questions.",
                409,
            )
        row.question_id = created.id

    if request.action != "unmap":
        row.mapping_status = MappingStatus.USER_CONFIRMED
        row.mapping_method = "USER"
        row.mapping_confidence = "HIGH"
        row.mapping_reason = "USER_DECISION"
        row.proposed_question_id = None
        # A different question means the earlier value is no longer "what was approved".
    if previous is not None and previous.id != row.question_id:
        db.flush()
        _discard_if_orphan(db, application.id, previous)
    record(db, session, EventType.MAPPING_CHANGED, row.id, action=request.action)
    invalidation.invalidate(db, session, invalidation.MAPPING_CHANGED)
    db.commit()
    return row


def set_document(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
    document_type: str,
    document_id: uuid.UUID,
) -> AutomationField:
    """ "Use this file": pin a specific document to a file field. Only a document of this
    user's application can be chosen, and it stays that version."""
    session = owned_session(db, user, application_id, session_id)
    row = owned_field(db, user, session, field_id)
    application = get_owned_application(db, user, application_id)
    if row.input_type != "file":
        raise AppError("NOT_A_FILE_FIELD", "This field does not take a file.", 409)
    choice = documents.find_choice(db, user, application, document_type, document_id)
    if choice is None:
        raise NotFoundError("Document not found.")
    row.document_type = choice.type
    row.document_id = choice.id
    row.document_confirmed = True
    row.mapping_status = MappingStatus.USER_CONFIRMED
    row.mapping_method = "USER"
    row.mapping_confidence = "HIGH"
    row.mapping_reason = "USER_DECISION"
    if row.fill_status in (FillStatus.FILL_FAILED, FillStatus.USER_ACTION_REQUIRED):
        row.fill_status = FillStatus.NOT_FILLED
        row.fill_error_code = None
    record(db, session, EventType.DOCUMENT_SELECTED, row.id, document_type=choice.type)
    invalidation.invalidate(db, session, invalidation.DOCUMENT_CHANGED)
    db.commit()
    return row


def clear_document(
    db: Session, user: User, application_id: uuid.UUID, session_id: uuid.UUID, field_id: uuid.UUID
) -> AutomationField:
    session = owned_session(db, user, application_id, session_id)
    row = owned_field(db, user, session, field_id)
    row.document_type = None
    row.document_id = None
    row.document_confirmed = False
    invalidation.invalidate(db, session, invalidation.DOCUMENT_CHANGED)
    db.commit()
    return row


__all__ = [
    "Interrupted",
    "active_session",
    "clear_document",
    "confirm_domain",
    "discover",
    "fill",
    "owned_field",
    "owned_session",
    "pause",
    "record",
    "refresh",
    "resume",
    "set_document",
    "set_mapping",
    "start",
    "stop",
    "take_control",
]
