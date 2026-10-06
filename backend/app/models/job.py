import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.mixins import TimestampMixin
from app.models.professional_profile import SeniorityLevel

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.job_source_posting import JobSourcePosting
    from app.models.user_job import UserJob


class SalaryPeriod(str, enum.Enum):
    YEAR = "year"
    MONTH = "month"
    WEEK = "week"
    DAY = "day"
    HOUR = "hour"


class Job(Base, TimestampMixin):
    """A canonical job: one real-world vacancy, however many sources list it.

        Job                      what the vacancy is (normalized, source-independent)
         +-- JobSourcePosting    where it was found (one per source posting, V0.4)
         +-- UserJob             what a user did with it (discovered, saved)
               +-- Application   (future) applying to it

    Jobs are **global**: one row per vacancy, however many users discover it.
    User state lives in `UserJob` (and later `applications`), so refreshing
    or merging source postings never touches it, and a job's `id` never
    changes once established -- user jobs and applications point at it.

    Content columns (title ... posted_at, canonical_url, apply_url) are the
    *canonical* view, built from the preferred posting
    (`preferred_source_posting_id`, see
    `app.services.job_discovery.deduplication.merger`). Source identity,
    original URLs, raw payloads and content hashes live on the postings.

    `normalized_title`, `normalized_company_name`, `normalized_location` and
    `city` are deterministic comparison keys (V0.4) -- queryable without
    knowing any source's schema. `company_name` / `location` keep the
    display values.

    Enumerated fields reuse the Job Preference vocabularies
    (`employment_type`, `remote_preference`, `professional_seniority_level`)
    so the Matching Engine (V0.5) compares like with like. List-like fields
    (requirements, responsibilities, technologies, locations) are JSONB
    arrays, the same structured-JSON convention as Resume Profiles.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_posted_at", "posted_at"),
        Index("ix_jobs_identity", "normalized_company_name", "normalized_title"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # The posting the canonical content comes from, and the one future
    # application automation should use. NULL only transiently.
    preferred_source_posting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "job_source_postings.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_jobs_preferred_source_posting_id",
        ),
        nullable=True,
    )

    # --- URLs (of the preferred posting) -------------------------------------------
    # Normalized URL of the original posting -- what "View Job" opens.
    canonical_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    apply_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # --- Canonical content ----------------------------------------------------------
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
    # The salary as the posting wrote it, kept even when it was too ambiguous
    # to structure into min/max/currency/period.
    salary_text: Mapped[str | None] = mapped_column(String(500), nullable=True)

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

    # --- Comparison keys (V0.4, deterministic) ---------------------------------------
    normalized_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    normalized_company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_location: Mapped[str | None] = mapped_column(String(600), nullable=True)
    # Canonical city name from the location vocabulary (e.g. "Cologne" for
    # "Köln"), when recognized.
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Timeline -------------------------------------------------------------------
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # First time any discovery run received any of its postings. Never
    # moves later.
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Latest `last_seen_at` of its postings. Jobs are never deleted just
    # because a run didn't return them.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source_postings: Mapped[list["JobSourcePosting"]] = relationship(
        back_populates="job",
        foreign_keys="JobSourcePosting.job_id",
        cascade="all, delete-orphan",
        order_by="JobSourcePosting.first_seen_at",
    )
    applications: Mapped[list["Application"]] = relationship(back_populates="job")
    user_jobs: Mapped[list["UserJob"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
