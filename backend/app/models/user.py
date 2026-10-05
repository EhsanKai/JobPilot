import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.deal_breaker import DealBreaker
    from app.models.industry import UserIndustry
    from app.models.job_preferences import JobPreferences
    from app.models.language import UserLanguage
    from app.models.professional_profile import ProfessionalProfile
    from app.models.resume import Resume
    from app.models.skill import UserSkill
    from app.models.user_job import UserJob
    from app.models.user_profile import UserProfile


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    profile: Mapped["UserProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    professional_profile: Mapped["ProfessionalProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    job_preferences: Mapped["JobPreferences | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    resumes: Mapped[list["Resume"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    applications: Mapped[list["Application"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    skills: Mapped[list["UserSkill"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    languages: Mapped[list["UserLanguage"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    industries: Mapped[list["UserIndustry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    deal_breakers: Mapped[list["DealBreaker"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    user_jobs: Mapped[list["UserJob"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
