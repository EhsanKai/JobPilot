"""The application tracking timeline (V0.12): every change of an application's status,
and every note the user adds, as an append-only record.

    Application                       status = the current status (fast queries)
     +-- ApplicationStatusHistory     one row per event; the latest row's `new_status`
          (many, ordered by sequence) always equals `Application.status`

A row is written, never edited: if the user changes the status again, that is another
row. `previous_status` / `new_status` / `changed_at` / `actor` come from the server --
the current status it read under a row lock, the clock, and who the request was
authenticated as -- never from a request body.

Entry types:

- STATUS_CHANGE  a status moved along an allowed transition
- CORRECTION     the user moved the status along a transition the lifecycle does not
                 allow, saying so explicitly (a correction needs a note)
- NOTE           the user added a note; the status did not change
                 (`previous_status == new_status`)

The first row of an application has no `previous_status`.

`note` is user-authored text, stored exactly as written. System events carry no note:
what they mean is shown from their source and statuses, so nothing JobPilot wrote can
be mistaken for something the user said.

The V0.11 submission records are *not* copied here: a timeline reads them where they
are, so a submission's audit stays one immutable record. A row caused by a submission
only points at its attempt (`submission_attempt_id`).
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.application import ApplicationStatus
from app.models.mixins import ImmutableMixin

if TYPE_CHECKING:
    from app.models.application import Application


class StatusHistoryEntryType(str, enum.Enum):
    STATUS_CHANGE = "STATUS_CHANGE"
    CORRECTION = "CORRECTION"
    NOTE = "NOTE"


class StatusChangeSource(str, enum.Enum):
    """Where the event came from."""

    #: JobPilot itself, from a deterministic event (an application was created).
    SYSTEM = "SYSTEM"
    #: The user changed it in the tracking UI / API.
    USER = "USER"
    #: A V0.11 submission: its confirmed result, or what the user found after an
    #: unknown one.
    SUBMISSION = "SUBMISSION"


class StatusChangeActor(str, enum.Enum):
    """Who acted: the authenticated user, or JobPilot on its own."""

    USER = "USER"
    SYSTEM = "SYSTEM"


class ApplicationStatusHistory(Base, ImmutableMixin):
    __tablename__ = "application_status_history"
    # Append-only: ImmutableMixin's empty `__mutable_columns__` lets no column change
    # after the row is written.
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "user_id"],
            ["applications.id", "applications.user_id"],
            ondelete="CASCADE",
            name="fk_application_status_history_application",
        ),
        # Ordering is the sequence, not the clock: two events can share a timestamp.
        UniqueConstraint(
            "application_id", "sequence", name="uq_application_status_history_sequence"
        ),
        Index("ix_application_status_history_user_id", "user_id"),
        CheckConstraint("sequence >= 1", name="ck_application_status_history_sequence"),
        CheckConstraint(
            "(entry_type = 'NOTE') = (previous_status IS NOT DISTINCT FROM new_status "
            "AND previous_status IS NOT NULL)",
            name="ck_application_status_history_note_keeps_status",
        ),
        CheckConstraint(
            "entry_type <> 'CORRECTION' OR (note IS NOT NULL AND source = 'USER')",
            name="ck_application_status_history_correction",
        ),
        CheckConstraint(
            "entry_type <> 'NOTE' OR note IS NOT NULL", name="ck_application_status_history_note"
        ),
        CheckConstraint(
            "source <> 'USER' OR actor = 'USER'", name="ck_application_status_history_user_actor"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: 1, 2, 3 ... per application, assigned under the application's row lock.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[StatusHistoryEntryType] = mapped_column(
        Enum(StatusHistoryEntryType, name="status_history_entry_type"), nullable=False
    )
    #: NULL only for an application's first entry.
    previous_status: Mapped[ApplicationStatus | None] = mapped_column(
        Enum(ApplicationStatus, name="application_status"), nullable=True
    )
    new_status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[StatusChangeSource] = mapped_column(
        Enum(StatusChangeSource, name="status_change_source"), nullable=False
    )
    actor: Mapped[StatusChangeActor] = mapped_column(
        Enum(StatusChangeActor, name="status_change_actor"), nullable=False
    )
    #: The user's own words, exactly as written.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The V0.11 attempt behind a SUBMISSION entry (a reference only).
    submission_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_submission_attempts.id", ondelete="CASCADE"),
        nullable=True,
    )

    application: Mapped["Application"] = relationship(back_populates="status_history")
