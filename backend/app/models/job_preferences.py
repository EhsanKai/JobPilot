import enum
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class RemotePreference(str, enum.Enum):
    """A single work-model option. A user may select several (see
    `JobPreferenceWorkModel`) -- e.g. "Hybrid + Remote"."""

    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"
    FLEXIBLE = "flexible"


class TravelWillingness(str, enum.Enum):
    NONE = "none"
    OCCASIONAL = "occasional"
    FREQUENT = "frequent"
    FLEXIBLE = "flexible"


class RelocationPreference(str, enum.Enum):
    YES = "yes"
    NO = "no"
    MAYBE = "maybe"


class EmploymentTypeValue(str, enum.Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    FREELANCE = "freelance"
    WORKING_STUDENT = "working_student"


class JobTitlePriority(str, enum.Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class JobPreferences(Base, TimestampMixin):
    """Foundational job-preference record for a user.

    List-like attributes (job titles, locations, employment types, preferred
    industries, work models) are normalized into their own child tables
    instead of being stored as delimited strings. Deal breakers also hang off
    the user directly (see `DealBreaker`) since they describe Job Preferences
    in the product's conceptual model but don't need a FK back to this table.
    """

    __tablename__ = "job_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    minimum_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    desired_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    relocation: Mapped[RelocationPreference | None] = mapped_column(
        Enum(RelocationPreference, name="relocation_preference"), nullable=True
    )
    travel_willingness: Mapped[TravelWillingness | None] = mapped_column(
        Enum(TravelWillingness, name="travel_willingness"), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="job_preferences")
    job_titles: Mapped[list["JobPreferenceJobTitle"]] = relationship(
        back_populates="job_preferences", cascade="all, delete-orphan"
    )
    locations: Mapped[list["JobPreferenceLocation"]] = relationship(
        back_populates="job_preferences", cascade="all, delete-orphan"
    )
    employment_types: Mapped[list["JobPreferenceEmploymentType"]] = relationship(
        back_populates="job_preferences", cascade="all, delete-orphan"
    )
    industries: Mapped[list["JobPreferenceIndustry"]] = relationship(
        back_populates="job_preferences", cascade="all, delete-orphan"
    )
    work_models: Mapped[list["JobPreferenceWorkModel"]] = relationship(
        back_populates="job_preferences", cascade="all, delete-orphan"
    )


class JobPreferenceJobTitle(Base):
    __tablename__ = "job_preference_job_titles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_preferences_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_preferences.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[JobTitlePriority] = mapped_column(
        Enum(JobTitlePriority, name="job_title_priority"),
        default=JobTitlePriority.SECONDARY,
        nullable=False,
    )

    job_preferences: Mapped["JobPreferences"] = relationship(back_populates="job_titles")


class JobPreferenceLocation(Base):
    __tablename__ = "job_preference_locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_preferences_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_preferences.id", ondelete="CASCADE"), nullable=False
    )
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    # Not used for matching yet -- reserved so a future version can filter by
    # distance without another migration.
    radius_km: Mapped[int | None] = mapped_column(Integer, nullable=True)

    job_preferences: Mapped["JobPreferences"] = relationship(back_populates="locations")


class JobPreferenceEmploymentType(Base):
    __tablename__ = "job_preference_employment_types"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_preferences_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_preferences.id", ondelete="CASCADE"), nullable=False
    )
    employment_type: Mapped[EmploymentTypeValue] = mapped_column(
        Enum(EmploymentTypeValue, name="employment_type"), nullable=False
    )

    job_preferences: Mapped["JobPreferences"] = relationship(back_populates="employment_types")


class JobPreferenceIndustry(Base):
    """An industry the user wants to work in. See `app.models.industry` for
    the industries the user has already worked in (a professional-profile
    attribute, not a preference)."""

    __tablename__ = "job_preference_industries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_preferences_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_preferences.id", ondelete="CASCADE"), nullable=False
    )
    industry: Mapped[str] = mapped_column(String(255), nullable=False)

    job_preferences: Mapped["JobPreferences"] = relationship(back_populates="industries")


class JobPreferenceWorkModel(Base):
    __tablename__ = "job_preference_work_models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_preferences_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_preferences.id", ondelete="CASCADE"), nullable=False
    )
    work_model: Mapped[RemotePreference] = mapped_column(
        Enum(RemotePreference, name="remote_preference"), nullable=False
    )

    job_preferences: Mapped["JobPreferences"] = relationship(back_populates="work_models")


EmploymentType = EmploymentTypeValue
