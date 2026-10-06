import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class Industry(Base):
    """A reusable catalog of industry names, shared across all users.

    Represents industries the user has professional experience in. This is
    distinct from `job_preference_industries`, which holds the industries a
    user would like to work in going forward.
    """

    __tablename__ = "industries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    user_industries: Mapped[list["UserIndustry"]] = relationship(back_populates="industry")


class UserIndustry(Base, TimestampMixin):
    __tablename__ = "user_industries"
    __table_args__ = (
        UniqueConstraint("user_id", "industry_id", name="uq_user_industries_user_industry"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    industry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("industries.id", ondelete="CASCADE"), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="industries")
    industry: Mapped["Industry"] = relationship(back_populates="user_industries")
