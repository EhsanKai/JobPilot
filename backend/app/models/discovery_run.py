import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class DiscoveryRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    # Every selected source succeeded.
    COMPLETED = "completed"
    # At least one source succeeded and at least one failed.
    PARTIAL = "partial"
    # No source succeeded (or nothing could run).
    FAILED = "failed"


IN_PROGRESS_RUN_STATUSES = frozenset({DiscoveryRunStatus.PENDING, DiscoveryRunStatus.RUNNING})


class DiscoveryRun(Base, TimestampMixin):
    """One execution of job discovery for a user.

    `POST /jobs/discover` creates the row and returns immediately; the work
    runs as a background task and the frontend polls the run. The criteria
    and the per-source summary are kept so a run can be inspected later
    (debugging, analytics, "what did I search for?").
    """

    __tablename__ = "discovery_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[DiscoveryRunStatus] = mapped_column(
        Enum(DiscoveryRunStatus, name="discovery_run_status"),
        default=DiscoveryRunStatus.PENDING,
        nullable=False,
    )
    # Validated `JobSearchCriteria`, as JSON.
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Validated `DiscoverySummary`, as JSON. NULL until the run finishes.
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # User-safe; never a stack trace.
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
