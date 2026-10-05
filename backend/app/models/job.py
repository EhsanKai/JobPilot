import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.application import Application


class Job(Base, TimestampMixin):
    """Foundation table for a normalized job posting.

    Jobs are not populated by any scraper or connector in V0. This table only
    exists so `applications.job_id` has somewhere to point once job discovery
    (V0.3+) is implemented against the `JobSource` interface.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    description: Mapped[str | None] = mapped_column(nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    applications: Mapped[list["Application"]] = relationship(back_populates="job")
