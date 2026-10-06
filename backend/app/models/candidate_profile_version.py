import enum
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, ImmutableMixin, require_sha256_hex
from app.schemas.candidate_profile import CandidateProfileDocument, SnapshotSources


class CandidateProfileVersionReason(str, enum.Enum):
    """What triggered building a snapshot. Informational: an unchanged
    profile never produces a new version, whatever the trigger."""

    INITIAL = "initial"
    PROFILE_UPDATED = "profile_updated"
    PREFERENCES_UPDATED = "preferences_updated"
    RESUME_CONFIRMED = "resume_confirmed"
    SCHEMA_UPGRADE = "schema_upgrade"
    TAXONOMY_UPGRADE = "taxonomy_upgrade"


class CandidateProfileVersion(Base, CreatedAtMixin, ImmutableMixin):
    """An immutable snapshot of what matching may know about a candidate (V0.5).

    The V0.1 profile tables stay the editable source of truth; a snapshot is
    a read model built from them plus the confirmed Resume Profile (see
    `app.schemas.candidate_profile` for the document and its rules). Every
    match result references the snapshot it was computed from, so it can be
    reproduced after the user edits their profile.

    - `version_number` counts 1, 2, 3, ... per user.
    - `content_hash` (SHA-256 of the canonical document) is unique per user:
      identical content is one version, never two.
    - `document` holds content and derived metadata; `schema_version` /
      `taxonomy_version` repeat the metadata's values as queryable columns
      (and are checked against it).
    - `sources` lists the exact input records (ids, timestamps) for audit.
      It is not part of the content hash.

    Write-once: no column can be changed after insert (`ImmutableMixin`).
    Snapshot building is a later V0.5 phase; nothing creates rows yet.
    """

    __tablename__ = "candidate_profile_versions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "version_number", name="uq_candidate_profile_versions_user_version"
        ),
        UniqueConstraint(
            "user_id", "content_hash", name="uq_candidate_profile_versions_user_content_hash"
        ),
        # Target of job_matches' composite foreign key: a match can only
        # reference a snapshot of its own user.
        UniqueConstraint("id", "user_id", name="uq_candidate_profile_versions_id_user"),
        CheckConstraint("version_number >= 1", name="ck_candidate_profile_versions_version_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Indexed through uq_candidate_profile_versions_user_version.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Validated `CandidateProfileDocument`, as JSON.
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    taxonomy_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Validated `SnapshotSources`, as JSON.
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    reason: Mapped[CandidateProfileVersionReason] = mapped_column(
        Enum(CandidateProfileVersionReason, name="candidate_profile_version_reason"),
        nullable=False,
    )

    @validates("content_hash")
    def _validate_hash(self, key: str, value: str) -> str:
        return require_sha256_hex(key, value)

    @validates("document")
    def _validate_document(self, key: str, value: Any) -> dict[str, Any]:
        document = CandidateProfileDocument.model_validate(value)
        metadata = document.metadata
        for column in ("schema_version", "taxonomy_version"):
            current = getattr(self, column)
            if current is not None and current != getattr(metadata, column):
                raise ValueError(f"{column} {current!r} does not match the document metadata")
        # The columns mirror the document; fill them in when not given.
        self.schema_version = metadata.schema_version
        self.taxonomy_version = metadata.taxonomy_version
        return document.model_dump(mode="json")

    @validates("schema_version", "taxonomy_version")
    def _validate_version(self, key: str, value: str | None) -> str | None:
        if self.document is not None and self.document["metadata"][key] != value:
            raise ValueError(f"{key} {value!r} does not match the document metadata")
        return value

    @validates("sources")
    def _validate_sources(self, key: str, value: Any) -> list[dict[str, Any]]:
        dumped: list[dict[str, Any]] = SnapshotSources.model_validate(value).model_dump(mode="json")
        return dumped

    def read_document(self) -> CandidateProfileDocument:
        return CandidateProfileDocument.model_validate(self.document)
