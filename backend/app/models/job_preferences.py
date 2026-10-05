import enum
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class SeniorityLevel(str, enum.Enum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"
    EXECUTIVE = "executive"


class RemotePreference(str, enum.Enum):
    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"
    FLEXIBLE = "flexible"


class TravelWillingness(str, enum.Enum):
    NONE = "none"
    OCCASIONAL = "occasional"
    FREQUENT = "frequent"


class EmploymentTypeValue(str, enum.Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    FREELANCE = "freelance"


class JobPreferences(Base, TimestampMixin):
    """Foundational job-preference record for a user.

    List-like attributes (job titles, locations, employment types, industries,
    languages) are normalized into their own child tables instead of being
    stored as delimited strings.
    """

    __tablename__ = "job_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    minimum_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    desired_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    seniority: Mapped[SeniorityLevel | None] = mapped_column(
        Enum(SeniorityLevel, name="seniority_level"), nullable=True
    )
    remote_preference: Mapped[RemotePreference | None] = mapped_column(
        Enum(RemotePreference, name="remote_preference"), nullable=True
    )
    relocation_willingness: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    languages: Mapped[list["JobPreferenceLanguage"]] = relationship(
        back_populates="job_preferences", cascade="all, delete-orphan"
    )


class JobPreferenceJobTitle(Base):
    __tablename__ = "job_preference_job_titles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_preferences_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_preferences.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    job_preferences: Mapped["JobPreferences"] = relationship(back_populates="job_titles")


class JobPreferenceLocation(Base):
    __tablename__ = "job_preference_locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_preferences_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_preferences.id", ondelete="CASCADE"), nullable=False
    )
    location: Mapped[str] = mapped_column(String(255), nullable=False)

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
    __tablename__ = "job_preference_industries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_preferences_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_preferences.id", ondelete="CASCADE"), nullable=False
    )
    industry: Mapped[str] = mapped_column(String(255), nullable=False)

    job_preferences: Mapped["JobPreferences"] = relationship(back_populates="industries")


class JobPreferenceLanguage(Base):
    __tablename__ = "job_preference_languages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_preferences_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_preferences.id", ondelete="CASCADE"), nullable=False
    )
    language: Mapped[str] = mapped_column(String(100), nullable=False)
    proficiency: Mapped[str | None] = mapped_column(String(50), nullable=True)

    job_preferences: Mapped["JobPreferences"] = relationship(back_populates="languages")


# Backwards-friendly alias kept distinct from the per-row EmploymentTypeValue enum
# so API schemas can import a single, clearly named enum.
EmploymentType = EmploymentTypeValue
