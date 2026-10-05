import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class DealBreakerType(str, enum.Enum):
    MINIMUM_SALARY = "minimum_salary"
    REMOTE_REQUIREMENT = "remote_requirement"
    EMPLOYMENT_TYPE = "employment_type"
    LOCATION = "location"
    RELOCATION = "relocation"
    TRAVEL = "travel"


class DealBreaker(Base, TimestampMixin):
    """A flexible, user-defined condition for the future Matching Engine.

    `value` is an optional free-form payload whose shape depends on `type`
    (e.g. an employment type name for EMPLOYMENT_TYPE). No matching logic
    reads this table yet in V0.1 -- it only stores the structured intent.
    """

    __tablename__ = "deal_breakers"
    __table_args__ = (UniqueConstraint("user_id", "type", name="uq_deal_breakers_user_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[DealBreakerType] = mapped_column(
        SAEnum(DealBreakerType, name="deal_breaker_type"), nullable=False
    )
    value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship(back_populates="deal_breakers")
