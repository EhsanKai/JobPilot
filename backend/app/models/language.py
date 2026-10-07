import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class LanguageProficiency(str, enum.Enum):
    BASIC = "basic"
    CONVERSATIONAL = "conversational"
    PROFESSIONAL = "professional"
    FLUENT = "fluent"
    NATIVE = "native"


class Language(Base):
    """A reusable catalog of spoken-language names, shared across all users."""

    __tablename__ = "languages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    user_languages: Mapped[list["UserLanguage"]] = relationship(back_populates="language")


class UserLanguage(Base, TimestampMixin):
    __tablename__ = "user_languages"
    __table_args__ = (
        UniqueConstraint("user_id", "language_id", name="uq_user_languages_user_language"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    language_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("languages.id", ondelete="CASCADE"), nullable=False
    )
    proficiency: Mapped[LanguageProficiency] = mapped_column(
        SAEnum(LanguageProficiency, name="language_proficiency"), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="languages")
    language: Mapped["Language"] = relationship(back_populates="user_languages")
