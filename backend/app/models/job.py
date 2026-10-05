import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.mixins import TimestampMixin
from app.models.professional_profile import SeniorityLevel

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.user_job import UserJob


class SalaryPeriod(str, enum.Enum):
    YEAR = "year"
    MONTH = "month"
    WEEK = "week"
    DAY = "day"
    HOUR = "hour"


class Job(Base, TimestampMixin):
    """A canonical, source-independent job posting (V0.3 Job Discovery).

    Jobs are **global**: one row per posting, however many users discover
    it. What a particular user did with a job (discovered it, saved it, and
    later applied to it) lives in `UserJob`, so refreshing a job from its
    source never touches user state.

    Identity:

    - `source` + `source_job_id` identify the posting at its source
      (e.g. `greenhouse` / `4012345`).
    - `dedup_key` is the unique key used for basic deduplication. It is
      derived from `source` + `source_job_id`, or from `source` + a hash of
      `canonical_url` when a source has no stable id (see
      `app.services.job_discovery.normalizer.build_dedup_key`). Cross-source
      deduplication is a V0.4 concern and deliberately not attempted here.

    Enumerated fields reuse the Job Preference vocabularies
    (`employment_type`, `remote_preference`, `professional_seniority_level`)
    so a future Matching Engine compares like with like. List-like fields
    (requirements, responsibilities, technologies, locations) are JSONB
    arrays, the same structured-JSON convention as Resume Profiles.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_jobs_dedup_key"),
        Index("ix_jobs_source_source_job_id", "source", "source_job_id"),
        Index("ix_jobs_posted_at", "posted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # --- Source identity ------------------------------------------------------
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(400), nullable=False)
    # The board / site within the source that listed the job (e.g. a
    # Greenhouse board token). Needed for source-specific refresh later.
    source_company_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- URLs ------------------------------------------------------------------
    # Normalized URL of the original posting -- what "View Job" opens.
    canonical_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    # The URL exactly as the source returned it.
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    apply_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # --- Posting content ----------------------------------------------------------
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    locations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirements: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    responsibilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    technologies: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    salary_period: Mapped[SalaryPeriod | None] = mapped_column(
        Enum(SalaryPeriod, name="salary_period"), nullable=True
    )

    employment_type: Mapped[EmploymentTypeValue | None] = mapped_column(
        Enum(EmploymentTypeValue, name="employment_type"), nullable=True
    )
    work_model: Mapped[RemotePreference | None] = mapped_column(
        Enum(RemotePreference, name="remote_preference"), nullable=True
    )
    seniority: Mapped[SeniorityLevel | None] = mapped_column(
        Enum(SeniorityLevel, name="professional_seniority_level"),
        nullable=True,
    )
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Timeline -------------------------------------------------------------------
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # First time any discovery run returned this posting. Never overwritten.
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Last time a discovery run returned it. Jobs are never deleted just
    # because a later run didn't return them; this lets V0.4+ mark stale ones.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # --- Provenance -------------------------------------------------------------
    # The source's own payload for this posting, kept for debugging and
    # re-normalization. Never returned by the API.
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Hash of the normalized content; a refresh only counts as an update
    # when it changes.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalizer_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    applications: Mapped[list["Application"]] = relationship(back_populates="job")
    user_jobs: Mapped[list["UserJob"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
