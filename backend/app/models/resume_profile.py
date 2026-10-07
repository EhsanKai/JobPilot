import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.resume import Resume


class ResumeProfileStatus(str, enum.Enum):
    """State of one Resume Intelligence processing run and its result."""

    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    # A newer successful run for the same resume replaced this one.
    SUPERSEDED = "superseded"
    FAILED = "failed"
    OCR_REQUIRED = "ocr_required"


SUCCESSFUL_PROFILE_STATUSES = frozenset(
    {ResumeProfileStatus.NEEDS_REVIEW, ResumeProfileStatus.CONFIRMED}
)


class ResumeProfile(Base, TimestampMixin):
    """The structured result of processing one resume file once.

    Every processing attempt (including failed ones) is its own row, so a
    resume can be re-processed by a newer parser without losing the previous
    result. At most one row per resume is "current" (needs_review/confirmed).

    Two JSONB documents are kept deliberately separate:

    - `raw_data`: the validated parser output exactly as produced (after the
      deterministic truth checks). Never edited. Answers "what did the AI
      extract?".
    - `reviewed_data`: the same document carrying the user's per-item
      accept/edit/reject decisions. Once `status` is CONFIRMED, the
      accepted/edited items in it are the trusted Resume Profile.

    Both are validated by `app.schemas.resume_profile.ResumeProfileData` on
    every write and read, so malformed data is never persisted as trusted.
    """

    __tablename__ = "resume_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized owner for straightforward authorization filtering.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ResumeProfileStatus] = mapped_column(
        Enum(ResumeProfileStatus, name="resume_profile_status"),
        default=ResumeProfileStatus.PROCESSING,
        nullable=False,
    )

    # Provenance -- what produced this result, so identical inputs aren't
    # re-processed (cost) and old results can be re-run by newer parsers.
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ai_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reused_from_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resume_profiles.id", ondelete="SET NULL"), nullable=True
    )

    # Extraction metadata + the normalized text that was parsed. The text is
    # as sensitive as the original file; it is only exposed to its owner via
    # the dedicated /profile/source endpoint (for "where did this come from?")
    # and is never logged.
    extraction_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reviewed_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    # User-safe error description for failed runs (never provider internals).
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    resume: Mapped["Resume"] = relationship(back_populates="profiles")
