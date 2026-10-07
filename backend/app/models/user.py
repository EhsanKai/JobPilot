import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, true
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # V0.13: may JobPilot send this user's data to the external AI provider? The user can
    # switch it off at any time (Settings > Privacy); every AI feature then refuses
    # server-side and the deterministic paths keep working. Each change is logged in
    # consent_records. The default for new accounts is AI_PROCESSING_DEFAULT_ALLOWED.
    external_ai_processing_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )

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
