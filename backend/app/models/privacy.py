"""Security & privacy records (V0.13): sign-in sessions and consent decisions.

Both are deliberately minimal (data minimization): a session stores no IP address, user
agent or device fingerprint, and a consent record stores no free text -- only which
category, which policy version, the decision and when.
"""

import enum
import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthSession(Base):
    """One sign-in. The access token carries this row's id (`sid`); a token is only valid
    while its session exists, is not revoked and has not expired. Logging out revokes it
    on the server, so a copied token stops working immediately -- not only when it
    expires. Deleted with the user."""

    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConsentCategory(str, enum.Enum):
    """What a consent / privacy decision is about.

    EXTERNAL_AI_PROCESSING: whether JobPilot may send the user's data (resume text,
    profile facts, job and question text) to the configured external AI provider. Whether
    this must be opt-in consent or may rely on another legal basis is a legal decision
    (docs/privacy.md); technically it is a revocable, recorded user choice either way.

    Cookie / tracking categories are added here when JobPilot first uses non-essential
    storage. It uses none today (docs/privacy.md, "Cookies and local storage").
    """

    EXTERNAL_AI_PROCESSING = "external_ai_processing"


#: Bump when the text the user decides on changes; older decisions stay on record with
#: the version they were made under.
CONSENT_POLICY_VERSIONS: Final[dict[ConsentCategory, str]] = {
    ConsentCategory.EXTERNAL_AI_PROCESSING: "2026-10-ai-v1",
}


class ConsentRecord(Base):
    """An append-only log of the user's decisions; the latest per category is in force.
    Deleted with the user."""

    __tablename__ = "consent_records"
    __table_args__ = (
        Index("ix_consent_records_user_category", "user_id", "category", "decided_at"),
        CheckConstraint(
            "category IN ('external_ai_processing')", name="ck_consent_records_category"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
