"""The photo on an application's CV (V0.9).

A photo is a **user-owned asset**, not generated content. It is stored
beside the application, never inside a generated document, and nothing in the
AI pipeline can read, write, replace or describe it: the document schemas the
AI fills have no field for a photo, and regenerating or humanizing a
document leaves it exactly as the user left it. Only the user uploads,
replaces or removes it.

One per application (`uq_application_photos_application_id`). The file lives in
the configured `FileStorage` (like resumes); this row holds its reference.
Ownership follows the application through a composite foreign key.
"""

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ApplicationPhoto(Base, TimestampMixin):
    __tablename__ = "application_photos"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "user_id"],
            ["applications.id", "applications.user_id"],
            ondelete="CASCADE",
            name="fk_application_photos_application",
        ),
        UniqueConstraint("application_id", name="uq_application_photos_application_id"),
        CheckConstraint("size_bytes > 0", name="ck_application_photos_size"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Reference in the configured `FileStorage`.
    storage_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
