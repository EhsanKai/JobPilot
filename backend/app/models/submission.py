"""Review & submit (V0.11): what the user reviewed, what they authorized, and what
JobPilot did with it.

    AutomationSession (V0.10)
     +-- ConsentAuthorization (many)    the user explicitly gave one legal consent, for
     |                                  exactly the text the page showed
     +-- FinalReview (many over time)   an immutable snapshot of what would be submitted,
     |                                  and its SHA-256 (`state_hash`)
          +-- SubmissionAuthorization   "I authorize JobPilot to submit this" -- scoped to
               |   (at most one)        that review, its hash, the session, the site
               +-- SubmissionAttempt    the one attempt the authorization allows: when it
                    (at most one)       started, when the final button was pressed, what
                                        the page showed afterwards

**Submission is a separate capability, not a looser fill.** The assistant of V0.10
still cannot press a button (`safety.SUBMIT_ACTIONS_ALLOWED` stays False); pressing
the final button needs a consumed `SubmissionAuthorization`, whose review hash still
matches the live page, and a one-shot permit issued for exactly that attempt.

Records here are write-once where it matters:

- `FinalReview` is immutable (ImmutableMixin); only its lifecycle columns (`status`,
  `invalidated_at`, `invalidation_reason`) move, and only forward.
- `ConsentAuthorization` is immutable except for being revoked once.
- `SubmissionAuthorization` moves AUTHORIZED -> CONSUMED / EXPIRED / INVALIDATED, once.
- `SubmissionAttempt` is append-only: once `finished_at` is set nothing changes, except
  that the user may record -- once -- what they found when they checked an unknown
  result themselves.

No value typed into a form is stored anywhere here except in the final review's
snapshot, which is the user's own record of what they reviewed (the same data their
answers already hold). Events and logs keep codes and counts only.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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
    event,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, ImmutableMixin, ImmutableRecordError


class FinalReviewStatus(str, enum.Enum):
    """ACTIVE: the latest review of the session, not yet invalidated. Anything that
    changes what would be submitted moves it to INVALIDATED; a newer review moves it to
    SUPERSEDED; the submission it authorized moves it to USED."""

    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"
    USED = "USED"


class SubmissionAuthorizationStatus(str, enum.Enum):
    AUTHORIZED = "AUTHORIZED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class SubmissionResult(str, enum.Enum):
    """What came of an attempt. A pressed button is not a success: SUCCESS needs a
    confirmation the page showed after the press. Anything uncertain after the press is
    SUBMISSION_UNKNOWN, and JobPilot never presses again by itself."""

    SUCCESS = "SUCCESS"
    FAILED_BEFORE_SUBMIT = "FAILED_BEFORE_SUBMIT"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"


class FinalReview(Base, CreatedAtMixin, ImmutableMixin):
    """Exactly what the user reviewed before authorizing a submission (immutable).

    `snapshot` is the canonical submission state -- destination, every field with its
    final value, source and verification, every document with its pinned version and
    content hash, every consent with its exact text and state, the final button -- and
    `state_hash` its SHA-256. `created_at` is when the user confirmed the review.
    """

    __tablename__ = "automation_final_reviews"
    __mutable_columns__ = frozenset({"status", "invalidated_at", "invalidation_reason"})
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "user_id"],
            ["applications.id", "applications.user_id"],
            ondelete="CASCADE",
            name="fk_automation_final_reviews_application",
        ),
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["automation_sessions.id", "automation_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_automation_final_reviews_session",
        ),
        UniqueConstraint("id", "user_id", name="uq_automation_final_reviews_id_user_id"),
        # One current review per session: a new one supersedes the old.
        Index(
            "uq_automation_final_reviews_one_active",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_automation_final_reviews_application", "application_id", "created_at"),
        Index("ix_automation_final_reviews_user_id", "user_id"),
        CheckConstraint(
            "state_hash ~ '^[0-9a-f]{64}$'", name="ck_automation_final_reviews_state_hash"
        ),
        CheckConstraint(
            "(status = 'INVALIDATED') = (invalidated_at IS NOT NULL)",
            name="ck_automation_final_reviews_invalidated",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: The version of the snapshot's shape (what the hash is computed over).
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Where it would be submitted: the exact host, its site, and the page (no query).
    destination_host: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    #: Which page instance was reviewed (a reload or navigation makes another).
    #: Not part of the hash -- it is compared on its own.
    page_marker: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[FinalReviewStatus] = mapped_column(
        Enum(FinalReviewStatus, name="final_review_status"),
        default=FinalReviewStatus.ACTIVE,
        nullable=False,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: FIELD_CHANGED, USER_TAKEOVER, NAVIGATION, MAPPING_CHANGED ... (a code, never a value).
    invalidation_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ConsentAuthorization(Base, CreatedAtMixin, ImmutableMixin):
    """The user explicitly gave one legal consent -- for exactly the text the page shows
    (`consent_text`, and its SHA-256). A changed text matches no authorization, so the
    consent is not authorized any more. Revoked once, never edited. `created_at` is when
    the user authorized it."""

    __tablename__ = "automation_consent_authorizations"
    __mutable_columns__ = frozenset({"revoked_at", "revoke_reason"})
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["automation_sessions.id", "automation_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_automation_consent_authorizations_session",
        ),
        ForeignKeyConstraint(
            ["field_id", "user_id"],
            ["automation_fields.id", "automation_fields.user_id"],
            ondelete="CASCADE",
            name="fk_automation_consent_authorizations_field",
        ),
        Index(
            "uq_automation_consent_authorizations_one_live",
            "field_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_automation_consent_authorizations_session", "session_id"),
        Index("ix_automation_consent_authorizations_user_id", "user_id"),
        CheckConstraint(
            "consent_text_hash ~ '^[0-9a-f]{64}$'",
            name="ck_automation_consent_authorizations_text_hash",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_key: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_text: Mapped[str] = mapped_column(Text, nullable=False)
    consent_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


class SubmissionAuthorization(Base, CreatedAtMixin):
    """The user's explicit "submit this", scoped to one final review and its hash, one
    session, one destination host. One-shot: consumed by the attempt it allows.
    `created_at` is when the user authorized it."""

    __tablename__ = "automation_submission_authorizations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "user_id"],
            ["applications.id", "applications.user_id"],
            ondelete="CASCADE",
            name="fk_automation_submission_authorizations_application",
        ),
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["automation_sessions.id", "automation_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_automation_submission_authorizations_session",
        ),
        ForeignKeyConstraint(
            ["final_review_id", "user_id"],
            ["automation_final_reviews.id", "automation_final_reviews.user_id"],
            ondelete="CASCADE",
            name="fk_automation_submission_authorizations_review",
        ),
        UniqueConstraint("id", "user_id", name="uq_automation_submission_authorizations_id_user"),
        # A review authorizes at most once.
        UniqueConstraint("final_review_id", name="uq_automation_submission_authorizations_review"),
        # At most one authorization waiting to be used per application.
        Index(
            "uq_automation_submission_authorizations_one_open",
            "application_id",
            unique=True,
            postgresql_where=text("status = 'AUTHORIZED'"),
        ),
        Index("ix_automation_submission_authorizations_session", "session_id"),
        Index("ix_automation_submission_authorizations_user_id", "user_id"),
        CheckConstraint(
            "(status = 'CONSUMED') = (consumed_at IS NOT NULL)",
            name="ck_automation_submission_authorizations_consumed",
        ),
        CheckConstraint(
            "(status = 'INVALIDATED') = (invalidated_at IS NOT NULL)",
            name="ck_automation_submission_authorizations_invalidated",
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_automation_submission_authorizations_expiry"
        ),
        CheckConstraint(
            "reviewed_state_hash ~ '^[0-9a-f]{64}$'",
            name="ck_automation_submission_authorizations_hash",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    final_review_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewed_state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_host: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SubmissionAuthorizationStatus] = mapped_column(
        Enum(SubmissionAuthorizationStatus, name="submission_authorization_status"),
        default=SubmissionAuthorizationStatus.AUTHORIZED,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


class SubmissionAttempt(Base):
    """The audit record of one submission attempt (append-only).

    `submit_triggered_at` is written -- and committed -- *before* the final button is
    pressed, so a crash in between is read as "maybe pressed" (SUBMISSION_UNKNOWN), never
    as "not pressed". The database itself refuses a SUCCESS or UNKNOWN without it, and a
    FAILED_BEFORE_SUBMIT with it.

    Safe metadata only: codes, timestamps, the destination without its query, a short
    confirmation phrase and reference the page showed. Never a form value, a cookie, a
    token or page content beyond that.
    """

    __tablename__ = "automation_submission_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "user_id"],
            ["applications.id", "applications.user_id"],
            ondelete="CASCADE",
            name="fk_automation_submission_attempts_application",
        ),
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["automation_sessions.id", "automation_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_automation_submission_attempts_session",
        ),
        ForeignKeyConstraint(
            ["authorization_id", "user_id"],
            [
                "automation_submission_authorizations.id",
                "automation_submission_authorizations.user_id",
            ],
            ondelete="CASCADE",
            name="fk_automation_submission_attempts_authorization",
        ),
        # Exactly once: an authorization allows one attempt ...
        UniqueConstraint(
            "authorization_id", name="uq_automation_submission_attempts_authorization"
        ),
        # ... and an application has at most one attempt in flight.
        Index(
            "uq_automation_submission_attempts_one_running",
            "application_id",
            unique=True,
            postgresql_where=text("finished_at IS NULL"),
        ),
        Index("ix_automation_submission_attempts_application", "application_id", "started_at"),
        Index("ix_automation_submission_attempts_user_id", "user_id"),
        CheckConstraint(
            "(result IS NULL) = (finished_at IS NULL)",
            name="ck_automation_submission_attempts_finished",
        ),
        CheckConstraint(
            "result IS NULL OR result <> 'FAILED_BEFORE_SUBMIT' OR submit_triggered_at IS NULL",
            name="ck_automation_submission_attempts_failed_not_pressed",
        ),
        CheckConstraint(
            "result IS NULL OR result = 'FAILED_BEFORE_SUBMIT' OR submit_triggered_at IS NOT NULL",
            name="ck_automation_submission_attempts_pressed",
        ),
        CheckConstraint(
            "user_verified_outcome IS NULL OR (result = 'SUBMISSION_UNKNOWN' "
            "AND user_verified_outcome IN ('SUBMITTED', 'NOT_SUBMITTED') "
            "AND user_verified_at IS NOT NULL)",
            name="ck_automation_submission_attempts_verified",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    authorization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: A reference (the review can be removed only with its session).
    final_review_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewed_state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submit_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[SubmissionResult | None] = mapped_column(
        Enum(SubmissionResult, name="submission_result"), nullable=True
    )
    #: An application reference the confirmation page showed ("ABC-12345").
    confirmation_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: The confirmation phrase recognized on the page ("Thank you for your application").
    confirmation_summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: After an unknown result, what the user found when they checked: SUBMITTED or
    #: NOT_SUBMITTED. Recorded once.
    user_verified_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    user_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


_VERIFICATION_COLUMNS = frozenset({"user_verified_outcome", "user_verified_at"})


@event.listens_for(SubmissionAttempt, "before_update")
def _attempts_are_append_only(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    """A finished attempt is history: nothing changes, except recording -- once -- what
    the user found after an unknown result."""
    state = inspect(target)
    finished = state.attrs["finished_at"].history
    was_finished = bool(finished.deleted and finished.deleted[0] is not None) or (
        not finished.has_changes() and target.finished_at is not None
    )
    if not was_finished:
        return
    changed = {
        attr.key for attr in mapper.column_attrs if state.attrs[attr.key].history.has_changes()
    }
    if changed - _VERIFICATION_COLUMNS:
        raise ImmutableRecordError(
            f"SubmissionAttempt is append-only once finished; cannot change {sorted(changed)}"
        )
    for key in changed:
        previous = state.attrs[key].history.deleted
        if previous and previous[0] is not None:
            raise ImmutableRecordError(f"SubmissionAttempt.{key} is recorded once")
