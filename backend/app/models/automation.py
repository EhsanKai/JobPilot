"""Browser automation: sessions, the fields found in a form, and an event log (V0.10).

    Application
     +-- AutomationSession (many over time, one live at a time)   a controlled browser
          +-- AutomationField (many)       a form field discovered on the page, what it was mapped
          |                                to, and what the assistant did to it
          +-- AutomationEvent (many)       a lightweight, write-once log

**JobPilot fills forms; the user remains in control.** The assistant is a helper,
never the owner of the application: it can be paused, stopped or taken over at
any moment, it never overwrites what the user typed, and it never submits.
There is deliberately no `SUBMITTED` status.

What is stored -- and what is not:

- A field is stored as *neutral, serializable metadata* (label, name, id, type,
  options, a locator description). No Playwright objects, no raw HTML.
- No form *values* are stored. The assistant remembers only a salted hash of the
  value it last wrote (`last_agent_value_hash`), which is how a later pass tells
  "still what I wrote" from "the user changed it". Passwords, cookies, tokens and
  other browser secrets stay inside the browser; the URLs stored have their query
  string and fragment removed.
- Events carry codes and counts, never values.

Ownership: every row carries `user_id`, and composite foreign keys
(`(application_id, user_id)`, `(session_id, user_id)`) make it impossible to
store a session, field or event for someone else. A field's mapping to a
question is a plain reference (`ON DELETE SET NULL`): a composite key cannot
null only one of its columns in PostgreSQL's SQLAlchemy dialect, and the service
maps only to questions of the field's own application.

Questions are not duplicated here: a discovered field is mapped to the existing
V0.9 `ApplicationQuestion` (`question_id`), or creates one with
`source = "browser"` and a stable `external_key`.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, ImmutableMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.application import Application


class AutomationStatus(str, enum.Enum):
    """Where a browser session is. Values equal names (as in the V0.10 spec).

        STARTING -> RUNNING <-> PAUSED
                       |  ^
                       v  |
                  USER_CONTROL          the user took the browser over
                       RUNNING -> BLOCKED     a security challenge / login needs the user
                       RUNNING -> COMPLETED   the assistant has nothing left to do
        any live status -> STOPPED     the user stopped the assistant
        STARTING / RUNNING -> FAILED   the browser or the page failed

    There is no SUBMITTED: V0.10 never submits an application.
    """

    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    USER_CONTROL = "USER_CONTROL"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


#: A session in one of these holds (or may hold) a live browser.
ACTIVE_STATUSES = frozenset(
    {
        AutomationStatus.STARTING,
        AutomationStatus.RUNNING,
        AutomationStatus.PAUSED,
        AutomationStatus.USER_CONTROL,
        AutomationStatus.BLOCKED,
        AutomationStatus.COMPLETED,
    }
)
TERMINAL_STATUSES = frozenset({AutomationStatus.STOPPED, AutomationStatus.FAILED})

_ACTIVE_SQL = "status IN ('STARTING', 'RUNNING', 'PAUSED', 'USER_CONTROL', 'BLOCKED', 'COMPLETED')"


class MappingStatus(str, enum.Enum):
    """How a discovered field relates to an application question.

    Only AUTO_MAPPED with HIGH confidence and USER_CONFIRMED may be filled
    automatically; NEEDS_REVIEW waits for the user.
    """

    UNMAPPED = "UNMAPPED"
    AUTO_MAPPED = "AUTO_MAPPED"
    USER_CONFIRMED = "USER_CONFIRMED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class FillStatus(str, enum.Enum):
    """What the assistant did (or must leave) with a field."""

    NOT_FILLED = "NOT_FILLED"
    #: Written and read back: the browser shows exactly the approved value.
    FILLED = "FILLED"
    #: Written but the browser shows something else (or the write failed).
    FILL_FAILED = "FILL_FAILED"
    #: The user changed it after the assistant (or before it): left untouched.
    USER_MODIFIED = "USER_MODIFIED"
    #: Only the user can do it (consent, an unconfirmed file, a custom control).
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"


class AutomationSession(Base, TimestampMixin):
    """One controlled-browser session for an application (V0.10).

    At most one is live per application (partial unique index), so two clicks
    cannot open two browsers. `target_url` comes from the application's context
    snapshot and is never supplied by a client. `current_url` and every other
    stored URL have their query string and fragment removed.
    """

    __tablename__ = "automation_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "user_id"],
            ["applications.id", "applications.user_id"],
            ondelete="CASCADE",
            name="fk_automation_sessions_application",
        ),
        # The target of the fields' and events' ownership keys.
        UniqueConstraint("id", "user_id", name="uq_automation_sessions_id_user_id"),
        Index(
            "uq_automation_sessions_one_live",
            "application_id",
            unique=True,
            postgresql_where=text(_ACTIVE_SQL),
        ),
        Index("ix_automation_sessions_application_created", "application_id", "created_at"),
        Index("ix_automation_sessions_user_id", "user_id"),
        CheckConstraint(
            "(status IN ('STOPPED', 'FAILED')) = (ended_at IS NOT NULL)",
            name="ck_automation_sessions_ended_iff_terminal",
        ),
        CheckConstraint(
            "browser_mode IN ('visible', 'headless')", name="ck_automation_sessions_browser_mode"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[AutomationStatus] = mapped_column(
        Enum(AutomationStatus, name="automation_status"),
        default=AutomationStatus.STARTING,
        nullable=False,
    )
    #: Where the session was told to go: the application URL of the context snapshot.
    target_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    #: Where the browser is now (scheme, host and path only).
    current_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    page_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: Hosts the assistant may work on: the target's, plus those the user confirmed.
    allowed_hosts: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: What needs the user's attention right now: LOGIN_REQUIRED, SECURITY_CHALLENGE,
    #: UNEXPECTED_DOMAIN, NO_FORM_FOUND ... (see `services.automation.messages`).
    attention_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: "visible" (the user sees and uses the browser window) or "headless" (live
    #: view by screenshot only).
    browser_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="visible")
    #: Challenge markers the user has dealt with ("recaptcha", ...) after taking the
    #: browser over: the assistant does not stop for them again.
    acknowledged_markers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: A final submission control was seen. It is never clicked.
    submit_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application: Mapped["Application"] = relationship()
    fields: Mapped[list["AutomationField"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AutomationField.position",
    )
    events: Mapped[list["AutomationEvent"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AutomationEvent.created_at",
    )

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


class AutomationField(Base, TimestampMixin):
    """One form field found on the page, and the assistant's relation to it."""

    __tablename__ = "automation_fields"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["automation_sessions.id", "automation_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_automation_fields_session",
        ),
        UniqueConstraint("session_id", "field_key", name="uq_automation_fields_session_key"),
        UniqueConstraint("id", "user_id", name="uq_automation_fields_id_user_id"),
        Index("ix_automation_fields_session_position", "session_id", "position"),
        Index("ix_automation_fields_user_id", "user_id"),
        CheckConstraint("position >= 0", name="ck_automation_fields_position"),
        CheckConstraint(
            "mapping_confidence IS NULL OR mapping_confidence IN ('HIGH', 'MEDIUM', 'LOW')",
            name="ck_automation_fields_mapping_confidence",
        ),
        CheckConstraint(
            "options IS NULL OR jsonb_typeof(options) = 'array'",
            name="ck_automation_fields_options_array",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # --- What the page says (neutral, serializable) -----------------------------------------
    #: Stable identity of the field on this form (see `services.automation.discovery`).
    field_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Document order, 0-based.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: text, email, tel, url, number, date, textarea, select, radio, checkbox,
    #: checkbox_group, file, custom ...
    input_type: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dom_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    form_context: Mapped[str | None] = mapped_column(String(255), nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: [{"value": ..., "label": ...}] for select / radio / checkbox group.
    options: Mapped[list[dict[str, str]] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    #: Small extras: accept, multiple, placeholder, autocomplete, min, max, pattern ...
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: How to find the field again: id / name / label / ordinal. Never a selector
    #: that depends on position alone.
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: In the latest discovery. A field that disappeared (a collapsed section) stays
    #: on record, with what was done to it.
    present: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Why only the user can fill it: LEGAL_CONSENT, CUSTOM_CONTROL, READ_ONLY ...
    user_action_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # --- The mapping to an ApplicationQuestion ---------------------------------------------------
    mapping_status: Mapped[MappingStatus] = mapped_column(
        Enum(MappingStatus, name="automation_mapping_status"),
        default=MappingStatus.UNMAPPED,
        nullable=False,
    )
    #: IDENTITY, EXACT_TEXT, TOPIC, AI, CREATED or USER.
    mapping_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mapping_confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)
    mapping_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: How an AI mapping was produced (prompt version / hash, provider, model).
    mapping_provenance: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    #: A reference, not ownership: deleting a question (V0.9) leaves the field,
    #: unmapped. The service only ever maps a field to a question of the same
    #: application (and so the same user).
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: The question a NEEDS_REVIEW mapping proposes (never filled until confirmed).
    proposed_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_questions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- File inputs: which document (never a path) -----------------------------------------------
    #: What the field appears to ask for: "tailored_cv" or "cover_letter".
    detected_document_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: The user's choice: "tailored_cv", "cover_letter" (a generated version) or
    #: "resume_file" (the file they uploaded to JobPilot) -- and which one.
    document_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: The user pressed "Use this file". Nothing is uploaded before.
    document_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- What the assistant did ------------------------------------------------------------------
    fill_status: Mapped[FillStatus] = mapped_column(
        Enum(FillStatus, name="automation_fill_status"),
        default=FillStatus.NOT_FILLED,
        nullable=False,
    )
    fill_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: Salted SHA-256 of what the browser showed right after the assistant wrote it.
    #: The value itself is never stored.
    last_agent_value_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Where the written value came from: USER, DETERMINISTIC, ACCEPTED_AI, DOCUMENT.
    last_value_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped["AutomationSession"] = relationship(back_populates="fields")


class AutomationEvent(Base, CreatedAtMixin, ImmutableMixin):
    """One thing that happened in a session (write-once). Codes and counts only:
    never a form value, a URL query or anything the user typed."""

    __tablename__ = "automation_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["automation_sessions.id", "automation_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_automation_events_session",
        ),
        Index("ix_automation_events_session_created", "session_id", "created_at"),
        Index("ix_automation_events_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The field the event is about, when it is about one (plain reference: events
    #: are an audit trail and outlive nothing -- they are removed with the session).
    field_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: DISCOVERY_STARTED, FIELD_FILLED, USER_TAKEOVER ... (`services.automation.events`).
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    session: Mapped["AutomationSession"] = relationship(back_populates="events")
