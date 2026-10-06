import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.user import User


class UserJob(Base, TimestampMixin):
    """A user's relationship with a canonical `Job`.

    Created when one of the user's discovery runs returns the job, or when
    the user saves it. It is also the visibility boundary: a user can only
    list and open jobs they have a `UserJob` row for.

    V0.3 only tracks discovery and the saved flag. Application status
    (Applied / Interview / Rejected / No Response / Offer ...) is meant to
    hang off this row (or the existing `applications` table) in a later
    version, so the canonical `Job` never needs per-user columns.
    """

    __tablename__ = "user_jobs"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_user_jobs_user_job"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # First / latest time one of this user's discovery runs returned the job.
    # NULL when the relationship exists for another reason.
    first_discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    last_discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    # NULL = not saved. A timestamp rather than a boolean so "saved on" can be
    # shown and sorted by, and so un-saving is just clearing it.
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="user_jobs")
    job: Mapped["Job"] = relationship(back_populates="user_jobs")
