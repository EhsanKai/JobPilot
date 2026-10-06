import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String
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
    MANAGER = "manager"
    DIRECTOR = "director"


class ProfessionalProfile(Base, TimestampMixin):
    """The user's own professional background (title, experience, seniority).

    Kept separate from `user_profiles` (personal contact info) and from
    `job_preferences` (what the user wants from a future job) because these
    describe three different things: who the user is, how to reach them, and
    what they're looking for.
    """

    __tablename__ = "professional_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    current_job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    years_of_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Named distinctly from V0's now-removed job_preferences.seniority column,
    # whose "seniority_level" Postgres enum type had a different value set
    # (PRINCIPAL/EXECUTIVE instead of MANAGER/DIRECTOR) -- reusing the name
    # would conflict with or silently misdefine the old type.
    seniority_level: Mapped[SeniorityLevel | None] = mapped_column(
        SAEnum(SeniorityLevel, name="professional_seniority_level"), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="professional_profile")
