"""API schemas of the browser assistant (V0.10).

Responses carry only what the user needs: statuses, labels, the approved values
that are about to be (or were) filled, and plain-language reasons. They never
carry cookies, tokens, browser process or connection details, filesystem paths,
Playwright objects, or URL queries. Requests forbid unknown keys, so a client
can never send an owner, a status, a URL, a locator, a mapping decision or a
file path.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.application_question import AnswerStatus, QuestionType
from app.models.automation import AutomationStatus, FillStatus, MappingStatus

DisplayStatus = Literal[
    "filled",
    "user_modified",
    "needs_input",
    "needs_review",
    "action_required",
    "ready",
    "failed",
    "unmapped",
]
AnswerState = Literal["APPROVED", "NEEDS_INPUT", "SUGGESTION_AVAILABLE", "UNFILLABLE"]
AnswerSourceLabel = Literal["USER", "DETERMINISTIC", "ACCEPTED_AI"]
DocumentType = Literal["tailored_cv", "cover_letter", "resume_file"]


# --- Requests ----------------------------------------------------------------------------------


class StartRequest(BaseModel):
    """Starting a session takes nothing from the client: the page it opens is the
    application URL of the application's own context snapshot."""

    model_config = ConfigDict(extra="forbid")


class FillRequest(BaseModel):
    """Fill the approved answers. `field_ids` limits the pass to those fields.
    `overwrite_field_ids` names fields the user explicitly asks to be written again
    even though the page already holds something else (their own edit, say)."""

    model_config = ConfigDict(extra="forbid")

    field_ids: list[uuid.UUID] | None = Field(default=None, max_length=300)
    overwrite_field_ids: list[uuid.UUID] = Field(default_factory=list, max_length=300)

    @model_validator(mode="after")
    def _overwrite_is_named(self) -> "FillRequest":
        if self.overwrite_field_ids and self.field_ids is not None:
            extra = set(self.overwrite_field_ids) - set(self.field_ids)
            if extra:
                raise ValueError("overwrite_field_ids must be among field_ids")
        return self


class ResumeRequest(BaseModel):
    """Return control to the assistant. It first inspects the page; with `fill` it
    then fills the empty approved fields (never what the user changed)."""

    model_config = ConfigDict(extra="forbid")

    fill: bool = False


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Close the browser window too. Otherwise it stays open for manual use (and is
    #: closed after a while); the assistant no longer touches it either way.
    close_browser: bool = False


class ConfirmDomainRequest(BaseModel):
    """Confirm that the website the browser is on is part of this application."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)


class MappingRequest(BaseModel):
    """The user's decision about a field's question."""

    model_config = ConfigDict(extra="forbid")

    #: confirm  use the proposed question
    #: change   use another existing question (`question_id`)
    #: unmap    leave the field unmapped (it is never filled)
    #: create   add a new question for the field
    action: Literal["confirm", "change", "unmap", "create"]
    question_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _needs_question(self) -> "MappingRequest":
        if (self.action == "change") != (self.question_id is not None):
            raise ValueError("question_id is required for (and only for) action 'change'")
        return self


class DocumentSelectionRequest(BaseModel):
    """The user's choice of document for a file field ("Use this file")."""

    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType
    document_id: uuid.UUID


class SuggestRequest(BaseModel):
    """Ask for AI suggestions for the mapped questions that have no answer yet."""

    model_config = ConfigDict(extra="forbid")


# --- Responses ------------------------------------------------------------------------------------


class AttentionRead(BaseModel):
    code: str
    message: str


class ErrorRead(BaseModel):
    code: str | None
    message: str


class SessionCounts(BaseModel):
    found: int = 0
    ready: int = 0
    filled: int = 0
    needs_review: int = 0
    needs_input: int = 0
    user_modified: int = 0
    action_required: int = 0
    failed: int = 0
    unmapped: int = 0


class SessionControls(BaseModel):
    """What the user can do right now (the buttons)."""

    can_pause: bool
    can_resume: bool
    can_take_control: bool
    can_stop: bool
    can_discover: bool
    can_fill: bool
    can_confirm_domain: bool


class SessionRead(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    status: AutomationStatus
    #: The status in the user's words ("Running", "User control", "Needs your input" ...).
    status_label: str
    target_url: str
    current_url: str | None
    page_title: str | None
    #: "visible": the assistant works in a browser window you can see and use.
    #: "headless": no window; the live view is a screenshot and you finish in your own browser.
    browser_mode: Literal["visible", "headless"]
    #: Whether the browser is still running (false after a restart or when it was closed).
    browser_alive: bool
    attention: AttentionRead | None
    submit_detected: bool
    submit_notice: str | None
    last_error: ErrorRead | None
    counts: SessionCounts
    controls: SessionControls
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    items: list[SessionRead]
    #: Whether the assistant could be started (the reason, if not).
    can_start: bool
    cannot_start_reason: str | None = None
    cannot_start_message: str | None = None


class QuestionBrief(BaseModel):
    id: uuid.UUID
    question_text: str
    question_type: QuestionType
    required: bool
    source: str | None
    answer_status: AnswerStatus


class MappingRead(BaseModel):
    status: MappingStatus
    method: str | None
    confidence: Literal["HIGH", "MEDIUM", "LOW"] | None
    reason: str | None


class FieldAnswerRead(BaseModel):
    """What the assistant would put into the field: only an approved answer is ever
    written; a suggestion is shown for review, never filled."""

    state: AnswerState
    source: AnswerSourceLabel | None = None
    #: The approved value (APPROVED) or the unreviewed suggestion (SUGGESTION_AVAILABLE).
    text: str | None = None
    options: list[str] | None = None
    reason: str | None = None
    message: str | None = None


class FillRead(BaseModel):
    status: FillStatus
    error_code: str | None
    error_message: str | None
    last_filled_at: datetime | None


class DocumentChoiceRead(BaseModel):
    type: DocumentType
    id: uuid.UUID
    label: str
    version_number: int | None = None
    #: ai | humanized | user_edited for generated documents; "upload" for the resume file.
    origin: str
    is_current: bool = False
    file_name: str


class DocumentFieldRead(BaseModel):
    #: What the field appears to ask for: "tailored_cv" / "cover_letter" / None (unclear).
    detected_type: Literal["tailored_cv", "cover_letter"] | None
    #: What would be offered ("Use this file"): the current version of the detected type.
    proposed: DocumentChoiceRead | None
    #: What the user chose. Pinned to that version: a newer one never replaces it.
    selected: DocumentChoiceRead | None
    confirmed: bool
    newer_available: bool
    #: Whether the form accepts the format JobPilot attaches (DOCX) -- or the chosen file's own.
    format_accepted: bool = True


class FieldRead(BaseModel):
    id: uuid.UUID
    field_key: str
    position: int
    input_type: str
    label: str
    name: str | None
    dom_id: str | None
    form_context: str | None
    required: bool
    max_length: int | None
    options: list[dict[str, str]] | None
    present: bool
    display_status: DisplayStatus
    #: One plain sentence for the status ("Required — no approved answer").
    display_message: str | None
    required_missing: bool
    mapping: MappingRead
    question: QuestionBrief | None
    proposed_question: QuestionBrief | None
    answer: FieldAnswerRead | None
    fill: FillRead
    user_action_reason: str | None
    user_action_message: str | None
    document: DocumentFieldRead | None
    first_seen_at: datetime
    last_seen_at: datetime


class FieldListResponse(BaseModel):
    items: list[FieldRead]
    #: Existing questions a field can be (re)mapped to.
    questions: list[QuestionBrief]


class DocumentChoicesResponse(BaseModel):
    items: list[DocumentChoiceRead]


class EventRead(BaseModel):
    id: uuid.UUID
    event_type: str
    field_id: uuid.UUID | None
    data: dict[str, str | int | bool | None]
    created_at: datetime


class EventListResponse(BaseModel):
    items: list[EventRead]


class SuggestResponse(BaseModel):
    requested: int
    suggested: int
    failed: int
    session: SessionRead
