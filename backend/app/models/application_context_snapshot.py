import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, ImmutableMixin, require_sha256_hex
from app.schemas.application_context import ApplicationContextDocument

if TYPE_CHECKING:
    from app.models.application import Application


class ApplicationContextSnapshot(Base, CreatedAtMixin, ImmutableMixin):
    """What the job was when the user started the application (V0.7).

    Write-once (`ImmutableMixin`): the canonical `Job` keeps changing as
    discovery refreshes it, and source postings change or disappear, but
    this record -- and so the answer to "which job was the user applying to?"
    -- never does. Later versions generate documents from it.

    - `document` is the validated `ApplicationContextDocument` as JSON: the
      job's canonical content, and the selected source posting's identity and
      URLs. It never contains anything the user wrote.
    - `content_hash` is the SHA-256 of the canonical JSON document, so a
      generated artifact can pin exactly which context it was built from.
    - `schema_version` repeats the document's version as a queryable column
      (and is checked against it).

    One per application today (`uq_application_context_snapshots_application_id`).
    It is removed with its application.
    """

    __tablename__ = "application_context_snapshots"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_application_context_snapshots_application_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Indexed through uq_application_context_snapshots_application_id.
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    application: Mapped["Application"] = relationship(back_populates="context")

    @validates("content_hash")
    def _validate_hash(self, key: str, value: str) -> str:
        return require_sha256_hex(key, value)

    @validates("document")
    def _validate_document(self, key: str, value: Any) -> dict[str, Any]:
        document = ApplicationContextDocument.model_validate(value)
        current = self.schema_version
        if current is not None and current != document.schema_version:
            raise ValueError(f"schema_version {current!r} does not match the document")
        # The column mirrors the document; fill it in when not given.
        self.schema_version = document.schema_version
        return document.model_dump(mode="json")

    @validates("schema_version")
    def _validate_version(self, key: str, value: str) -> str:
        if self.document is not None and self.document["schema_version"] != value:
            raise ValueError(f"{key} {value!r} does not match the document")
        return value

    def read_document(self) -> ApplicationContextDocument:
        return ApplicationContextDocument.model_validate(self.document)
