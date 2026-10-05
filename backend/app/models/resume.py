import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.resume_profile import ResumeProfile
    from app.models.user import User


class ResumeStatus(str, enum.Enum):
    """Lifecycle of a resume's (most recent) Resume Intelligence processing.

    Stored in the "resume_processing_status" Postgres enum. V0 used a
    differently-shaped "resume_status" type (UPLOADED/PROCESSING/PROCESSED/
    FAILED); the V0.2 migration converts the column and drops the old type
    rather than reusing its name with a different value set.
    """

    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    PARSING = "parsing"
    PARSED = "parsed"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    OCR_REQUIRED = "ocr_required"


IN_PROGRESS_RESUME_STATUSES = frozenset(
    {ResumeStatus.EXTRACTING, ResumeStatus.EXTRACTED, ResumeStatus.PARSING, ResumeStatus.PARSED}
)


class Resume(Base, TimestampMixin):
    """An uploaded resume file (one version among possibly many per user).

    The original file referenced by `storage_reference` is never modified:
    everything Resume Intelligence derives from it lives in `ResumeProfile`
    rows, so a resume can be re-processed later by an improved parser.
    """

    __tablename__ = "resumes"
    __table_args__ = (
        UniqueConstraint("user_id", "version_number", name="uq_resumes_user_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 1, 2, 3, ... per user, in upload order. Lets the model represent
    # "Resume 1 — Original", "Resume 2 — Updated", and future tailored
    # versions without assuming a single resume per user.
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_reference: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # SHA-256 of the original file. Nullable only for files uploaded before
    # V0.2; it is computed on first processing for those.
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[ResumeStatus] = mapped_column(
        Enum(ResumeStatus, name="resume_processing_status"),
        default=ResumeStatus.UPLOADED,
        nullable=False,
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="resumes")
    profiles: Mapped[list["ResumeProfile"]] = relationship(
        back_populates="resume",
        cascade="all, delete-orphan",
        order_by="ResumeProfile.created_at",
    )
