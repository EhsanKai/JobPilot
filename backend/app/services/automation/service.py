"""The browser assistant's read side and the API-facing operations (V0.10).

What a response contains is decided here: statuses in the user's words, the approved
values about to be filled, plain-language reasons. Never cookies, tokens, browser
process or connection details, filesystem paths or URL queries.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.provider import AIProvider
from app.core.errors import AppError
from app.models.application import Application
from app.models.application_question import AnswerStatus, ApplicationQuestion
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
from app.schemas.automation import (
    AttentionRead,
    DisplayStatus,
    DocumentChoiceRead,
    DocumentChoicesResponse,
    DocumentFieldRead,
    ErrorRead,
    EventListResponse,
    EventRead,
    FieldAnswerRead,
    FieldListResponse,
    FieldRead,
    FillRead,
    MappingRead,
    QuestionBrief,
    SessionControls,
    SessionCounts,
    SessionListResponse,
    SessionRead,
    SuggestResponse,
)
from app.services.applications.service import get_owned_application
from app.services.automation import controller, documents, messages
from app.services.automation.registry import registry
from app.services.automation.resolver import AnswerResolver, AnswerState
from app.services.questions import service as question_service
from app.services.questions.classify import classify

S = AutomationStatus

_STATUS_LABELS = {
    S.STARTING: "Starting",
    S.RUNNING: "Running",
    S.PAUSED: "Paused",
    S.USER_CONTROL: "User control",
    S.BLOCKED: "Blocked",
    S.COMPLETED: "Filled",
    S.STOPPED: "Stopped",
    S.FAILED: "Failed",
}

_DISPLAY_MESSAGES = {
    "filled": "Filled and checked.",
    "user_modified": "You changed this field. JobPilot leaves your edit alone.",
    "ready": "Approved. It will be filled when you run the assistant.",
    "unmapped": "Not matched to a question, so it is never filled.",
}


def _choice_read(choice: documents.DocumentChoice) -> DocumentChoiceRead:
    return DocumentChoiceRead(
        type=choice.type,  # type: ignore[arg-type]
        id=choice.id,
        label=choice.label,
        version_number=choice.version_number,
        origin=choice.origin,
        is_current=choice.is_current,
        file_name=choice.file_name,
    )


def _brief(question: ApplicationQuestion) -> QuestionBrief:
    assert question.answer is not None
    return QuestionBrief(
        id=question.id,
        question_text=question.question_text,
        question_type=question.question_type,
        required=question.required,
        source=question.source,
        answer_status=question.answer.status,
    )


class _Views:
    """Builds the read models of one session's fields; loads what they share once."""

    def __init__(self, db: Session, user: User, application: Application) -> None:
        self.db = db
        self.user = user
        self.application = application
        self.questions: dict[uuid.UUID, ApplicationQuestion] = {
            q.id: q
            for q in db.scalars(
                select(ApplicationQuestion)
                .where(
                    ApplicationQuestion.application_id == application.id,
                    ApplicationQuestion.user_id == user.id,
                )
                .options(selectinload(ApplicationQuestion.answer))
                .order_by(ApplicationQuestion.sort_order, ApplicationQuestion.created_at)
                .execution_options(populate_existing=True)
            )
        }
        self.resolver = AnswerResolver(db, user, application)
        self._choices: list[documents.DocumentChoice] | None = None

    @property
    def choices(self) -> list[documents.DocumentChoice]:
        if self._choices is None:
            self._choices = documents.list_choices(self.db, self.user, self.application)
        return self._choices

    def _document(self, row: AutomationField) -> DocumentFieldRead:
        selected = next(
            (c for c in self.choices if c.type == row.document_type and c.id == row.document_id),
            None,
        )
        proposed = next(
            (
                c
                for c in self.choices
                if row.detected_document_type is not None
                and c.type == row.detected_document_type
                and c.is_current
            ),
            None,
        )
        newer = False
        if selected is not None and selected.type != "resume_file" and not selected.is_current:
            newer = any(c.type == selected.type and c.is_current for c in self.choices)
        shown = selected or proposed
        accept = (row.meta or {}).get("accept")
        return DocumentFieldRead(
            detected_type=row.detected_document_type,  # type: ignore[arg-type]
            proposed=_choice_read(proposed) if proposed is not None else None,
            selected=_choice_read(selected) if selected is not None else None,
            confirmed=bool(row.document_confirmed and selected is not None),
            newer_available=newer,
            format_accepted=(
                documents.file_accepted(accept, shown.file_name, shown.mime_type)
                if shown is not None
                else True
            ),
        )

    def field(self, row: AutomationField) -> FieldRead:
        question = self.questions.get(row.question_id) if row.question_id else None
        proposed = (
            self.questions.get(row.proposed_question_id) if row.proposed_question_id else None
        )
        answer_read: FieldAnswerRead | None = None
        required_missing = False
        trusted = row.mapping_status is MappingStatus.USER_CONFIRMED or (
            row.mapping_status is MappingStatus.AUTO_MAPPED and row.mapping_confidence == "HIGH"
        )
        document = self._document(row) if row.input_type == "file" else None
        unfillable: str | None = None
        if question is not None and row.input_type != "file" and row.user_action_reason is None:
            resolved = self.resolver.resolve(question)
            if resolved.state is AnswerState.APPROVED:
                from app.services.automation import filler

                plan = filler.plan_value(row, resolved)
                if isinstance(plan, filler.Unfillable):
                    unfillable = plan.code
                    answer_read = FieldAnswerRead(
                        state="UNFILLABLE",
                        source=resolved.source,  # type: ignore[arg-type]
                        text=resolved.text,
                        options=resolved.options,
                        reason=plan.code,
                        message=messages.FILL_ERRORS.get(plan.code),
                    )
                else:
                    answer_read = FieldAnswerRead(
                        state="APPROVED",
                        source=resolved.source,  # type: ignore[arg-type]
                        text=resolved.text,
                        options=resolved.options,
                    )
            elif resolved.state is AnswerState.SUGGESTION_AVAILABLE:
                answer_read = FieldAnswerRead(
                    state="SUGGESTION_AVAILABLE",
                    text=resolved.text,
                    options=resolved.options,
                    reason=resolved.reason,
                    message=resolved.message,
                )
            else:
                answer_read = FieldAnswerRead(
                    state="NEEDS_INPUT", reason=resolved.reason, message=resolved.message
                )
                required_missing = bool(row.required or question.required)

        status, message = self._display(
            row, trusted, answer_read, document, required_missing, unfillable
        )
        return FieldRead(
            id=row.id,
            field_key=row.field_key,
            position=row.position,
            input_type=row.input_type,
            label=row.label,
            name=row.name,
            dom_id=row.dom_id,
            form_context=row.form_context,
            required=row.required,
            max_length=row.max_length,
            options=row.options,
            present=row.present,
            display_status=status,
            display_message=message,
            required_missing=required_missing,
            mapping=MappingRead(
                status=row.mapping_status,
                method=row.mapping_method,
                confidence=row.mapping_confidence,  # type: ignore[arg-type]
                reason=row.mapping_reason,
            ),
            question=_brief(question) if question is not None else None,
            proposed_question=_brief(proposed) if proposed is not None else None,
            answer=answer_read,
            fill=FillRead(
                status=row.fill_status,
                error_code=row.fill_error_code,
                error_message=messages.FILL_ERRORS.get(row.fill_error_code or ""),
                last_filled_at=row.last_filled_at,
            ),
            user_action_reason=row.user_action_reason,
            user_action_message=messages.USER_ACTION_REASONS.get(row.user_action_reason or ""),
            document=document,
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
        )

    @staticmethod
    def _display(
        row: AutomationField,
        trusted: bool,
        answer: FieldAnswerRead | None,
        document: DocumentFieldRead | None,
        required_missing: bool,
        unfillable: str | None,
    ) -> tuple[DisplayStatus, str | None]:
        if row.user_action_reason is not None:
            return "action_required", messages.USER_ACTION_REASONS.get(row.user_action_reason)
        if row.fill_status is FillStatus.FILL_FAILED:
            return "failed", messages.FILL_ERRORS.get(row.fill_error_code or "", "Filling failed.")
        if row.fill_status is FillStatus.USER_MODIFIED:
            return "user_modified", _DISPLAY_MESSAGES["user_modified"]
        if row.fill_status is FillStatus.FILLED:
            return "filled", _DISPLAY_MESSAGES["filled"]
        if row.fill_status is FillStatus.USER_ACTION_REQUIRED:
            code = row.fill_error_code
            return "action_required", messages.FILL_ERRORS.get(code or "", "Only you can do this.")
        if document is not None:
            if document.confirmed:
                return "ready", "The chosen file will be attached when you run the assistant."
            if document.proposed is not None:
                return (
                    "needs_review",
                    "Check which file will be attached: use this one or choose another.",
                )
            if document.detected_type is not None:
                name = "tailored CV" if document.detected_type == "tailored_cv" else "cover letter"
                return (
                    "needs_input",
                    f"No {name} exists yet. Create one, or choose another file.",
                )
            return "action_required", messages.USER_ACTION_REASONS["FILE_UNCLEAR"]
        if row.mapping_status is MappingStatus.NEEDS_REVIEW:
            return "needs_review", "JobPilot is not sure which question this is. Please check."
        if row.mapping_status is MappingStatus.UNMAPPED:
            return "unmapped", _DISPLAY_MESSAGES["unmapped"]
        if answer is None:
            return "unmapped", _DISPLAY_MESSAGES["unmapped"]
        if answer.state == "UNFILLABLE":
            return "needs_review", messages.FILL_ERRORS.get(unfillable or "", "Please check.")
        if answer.state == "SUGGESTION_AVAILABLE":
            return "needs_review", "A suggestion exists. Accept or edit it before it is filled."
        if answer.state == "NEEDS_INPUT":
            if required_missing:
                return "needs_input", "Required — no approved answer."
            return "needs_input", "No approved answer yet. Needs your input."
        if not trusted:
            return "needs_review", "JobPilot is not sure which question this is. Please check."
        return "ready", _DISPLAY_MESSAGES["ready"]


def _counts(fields: list[FieldRead]) -> SessionCounts:
    counts = SessionCounts()
    for item in fields:
        if not item.present:
            continue
        counts.found += 1
        status = item.display_status
        if status == "filled":
            counts.filled += 1
        elif status == "ready":
            counts.ready += 1
        elif status == "needs_review":
            counts.needs_review += 1
        elif status == "needs_input":
            counts.needs_input += 1
        elif status == "user_modified":
            counts.user_modified += 1
        elif status == "action_required":
            counts.action_required += 1
        elif status == "failed":
            counts.failed += 1
        elif status == "unmapped":
            counts.unmapped += 1
    return counts


def _controls(session: AutomationSession) -> SessionControls:
    status = session.status
    live = registry.peek(session.id)
    alive = live is not None and live.browser.is_alive() and not live.detached
    domain = session.attention_code == messages.UNEXPECTED_DOMAIN
    working = status in (S.RUNNING, S.COMPLETED) and alive
    return SessionControls(
        can_pause=working,
        can_resume=status in (S.PAUSED, S.USER_CONTROL)
        and alive
        and not (status is S.PAUSED and domain),
        can_take_control=status in (S.RUNNING, S.PAUSED, S.BLOCKED, S.COMPLETED) and alive,
        can_stop=status in ACTIVE_STATUSES,
        can_discover=working,
        can_fill=working,
        can_confirm_domain=domain and status in (S.PAUSED, S.USER_CONTROL) and alive,
    )


def _status_label(session: AutomationSession, counts: SessionCounts) -> str:
    if session.status in (S.RUNNING, S.COMPLETED):
        if counts.needs_review:
            return "Needs your review"
        if counts.needs_input:
            return "Needs your input"
    return _STATUS_LABELS[session.status]


def _read(session: AutomationSession, fields: list[FieldRead]) -> SessionRead:
    counts = _counts(fields)
    live = registry.peek(session.id)
    attention = (
        AttentionRead(
            code=session.attention_code,
            message=messages.ATTENTION_MESSAGES.get(session.attention_code, session.attention_code),
        )
        if session.attention_code
        else None
    )
    error = (
        ErrorRead(code=session.last_error_code, message=session.last_error or "")
        if session.last_error or session.last_error_code
        else None
    )
    return SessionRead(
        id=session.id,
        application_id=session.application_id,
        status=session.status,
        status_label=_status_label(session, counts),
        target_url=session.target_url,
        current_url=session.current_url,
        page_title=session.page_title,
        browser_mode=session.browser_mode,  # type: ignore[arg-type]
        browser_alive=bool(live is not None and live.browser.is_alive()),
        attention=attention,
        submit_detected=session.submit_detected,
        submit_notice=messages.SUBMISSION_NOTICE if session.submit_detected else None,
        last_error=error,
        counts=counts,
        controls=_controls(session),
        started_at=session.started_at,
        ended_at=session.ended_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _field_rows(db: Session, session: AutomationSession) -> list[AutomationField]:
    return list(
        db.scalars(
            select(AutomationField)
            .where(AutomationField.session_id == session.id)
            .order_by(AutomationField.position)
            .execution_options(populate_existing=True)
        )
    )


def session_read(db: Session, user: User, session: AutomationSession) -> SessionRead:
    application = get_owned_application(db, user, session.application_id)
    views = _Views(db, user, application)
    return _read(session, [views.field(row) for row in _field_rows(db, session)])


# --- Operations (each returns the session as the user sees it) -----------------------------------


def start(
    db: Session, user: User, application_id: uuid.UUID, provider: AIProvider | None
) -> SessionRead:
    session = controller.start(db, user, application_id, provider)
    return session_read(db, user, session)


def list_sessions(db: Session, user: User, application_id: uuid.UUID) -> SessionListResponse:
    application = get_owned_application(db, user, application_id)
    sessions = list(
        db.scalars(
            select(AutomationSession)
            .where(
                AutomationSession.application_id == application.id,
                AutomationSession.user_id == user.id,
            )
            .order_by(AutomationSession.created_at.desc())
            .limit(20)
            .execution_options(populate_existing=True)
        )
    )
    for session in sessions:
        controller.refresh(db, user, session)
    items = [session_read(db, user, session) for session in sessions]
    can_start, code, message = True, None, None
    if any(s.status in ACTIVE_STATUSES for s in sessions):
        can_start, code = False, "SESSION_ALREADY_ACTIVE"
        message = "The assistant is already running for this application."
    else:
        try:
            controller._application_url(db, user, application)  # noqa: SLF001
        except AppError as exc:
            can_start, code, message = False, exc.code, exc.message
    return SessionListResponse(
        items=items, can_start=can_start, cannot_start_reason=code, cannot_start_message=message
    )


def get_session(
    db: Session, user: User, application_id: uuid.UUID, session_id: uuid.UUID
) -> SessionRead:
    session = controller.owned_session(db, user, application_id, session_id)
    controller.refresh(db, user, session)
    return session_read(db, user, session)


def list_fields(
    db: Session, user: User, application_id: uuid.UUID, session_id: uuid.UUID
) -> FieldListResponse:
    session = controller.owned_session(db, user, application_id, session_id)
    controller.refresh(db, user, session)
    application = get_owned_application(db, user, application_id)
    views = _Views(db, user, application)
    return FieldListResponse(
        items=[views.field(row) for row in _field_rows(db, session)],
        questions=[_brief(q) for q in views.questions.values()],
    )


def get_field(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
) -> FieldRead:
    session = controller.owned_session(db, user, application_id, session_id)
    row = controller.owned_field(db, user, session, field_id)
    application = get_owned_application(db, user, application_id)
    return _Views(db, user, application).field(row)


def list_events(
    db: Session, user: User, application_id: uuid.UUID, session_id: uuid.UUID, limit: int = 100
) -> EventListResponse:
    session = controller.owned_session(db, user, application_id, session_id)
    rows = db.scalars(
        select(AutomationEvent)
        .where(AutomationEvent.session_id == session.id, AutomationEvent.user_id == user.id)
        .order_by(AutomationEvent.created_at.desc(), AutomationEvent.id)
        .limit(max(1, min(limit, 500)))
    )
    return EventListResponse(
        items=[
            EventRead(
                id=row.id,
                event_type=row.event_type,
                field_id=row.field_id,
                data=row.data,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


def document_choices(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    field_id: uuid.UUID,
) -> DocumentChoicesResponse:
    session = controller.owned_session(db, user, application_id, session_id)
    row = controller.owned_field(db, user, session, field_id)
    if row.input_type != "file":
        raise AppError("NOT_A_FILE_FIELD", "This field does not take a file.", 409)
    application = get_owned_application(db, user, application_id)
    return DocumentChoicesResponse(
        items=[_choice_read(c) for c in documents.list_choices(db, user, application)]
    )


def screenshot(db: Session, user: User, application_id: uuid.UUID, session_id: uuid.UUID) -> bytes:
    """A PNG of what the browser shows now, for the user's live view. Taken on demand
    and never stored."""
    session = controller.owned_session(db, user, application_id, session_id)
    if session.status not in ACTIVE_STATUSES:
        raise AppError("INVALID_SESSION_STATE", "This browser session has ended.", 409)
    live = registry.get(session.id)
    if live is None or not live.browser.is_alive():
        raise AppError("BROWSER_GONE", "The browser is no longer running.", 409)
    from app.services.automation.browser.base import BrowserError

    try:
        return live.browser.page.screenshot()
    except BrowserError as exc:
        raise AppError(exc.code, exc.message, 502) from None


async def suggest(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    session_id: uuid.UUID,
    provider: AIProvider,
) -> SuggestResponse:
    """Ask the V0.9 question AI for suggestions for the mapped questions that have no
    answer yet. Suggestions only: nothing is accepted or filled, and the questions that
    application data already answers are left to it."""
    session = controller.owned_session(db, user, application_id, session_id)
    application = get_owned_application(db, user, application_id)
    views = _Views(db, user, application)
    wanted: list[uuid.UUID] = []
    for row in _field_rows(db, session):
        question = views.questions.get(row.question_id) if row.question_id else None
        if question is None or not row.present or row.user_action_reason is not None:
            continue
        if row.mapping_status is MappingStatus.NEEDS_REVIEW:
            continue
        assert question.answer is not None
        if question.answer.status is not AnswerStatus.NOT_STARTED:
            continue
        if classify(question.question_text, question.question_type) is not None:
            continue
        if question.id not in wanted:
            wanted.append(question.id)
    suggested = failed = 0
    for question_id in wanted[:20]:
        try:
            await question_service.generate_answer(
                db, user, application_id, question_id, provider, None, regenerate=False
            )
            suggested += 1
        except AppError:
            failed += 1
    return SuggestResponse(
        requested=len(wanted[:20]),
        suggested=suggested,
        failed=failed,
        session=session_read(
            db, user, controller.owned_session(db, user, application_id, session_id)
        ),
    )
