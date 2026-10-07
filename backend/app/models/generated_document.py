import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship, validates

from app.db.base import Base
from app.models.mixins import ImmutableRecordError, TimestampMixin, require_sha256_hex
from app.schemas.document_content import (
    CONTENT_ADAPTER,
    SourceFact,
    ValidationReport,
)

if TYPE_CHECKING:
    from app.models.application import Application


class GeneratedDocumentType(str, enum.Enum):
    TAILORED_CV = "tailored_cv"
    COVER_LETTER = "cover_letter"


class GeneratedDocumentOrigin(str, enum.Enum):
    """How a version came to be (V0.9). Every version is an immutable row; the
    origin says whose words it holds.

    AI         generated from the confirmed resume and truth-validated (V0.8)
    HUMANIZED  an earlier version re-worded by the AI for a more natural tone,
               and truth-validated like any generation
    USER_EDITED  a version the user saved after editing: the user's own words,
               never checked, never overwritten by the AI
    """

    AI = "ai"
    HUMANIZED = "humanized"
    USER_EDITED = "user_edited"


class GeneratedDocumentStatus(str, enum.Enum):
    """Where one generation attempt is.

    The pipeline runs inside one request, so a document is only ever
    `GENERATING` while that request is in flight:

        GENERATING --AI call failed or returned unusable output--> FAILED
        GENERATING --truth validation found unsupported claims---> REJECTED
        GENERATING --validation passed---------------------------> VALIDATED

    Only `VALIDATED` is a usable document: it alone has `content` and a
    version number (database checks), and every other state is terminal.
    """

    GENERATING = "generating"
    VALIDATED = "validated"
    REJECTED = "rejected"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset(
    {
        GeneratedDocumentStatus.VALIDATED,
        GeneratedDocumentStatus.REJECTED,
        GeneratedDocumentStatus.FAILED,
    }
)


class GeneratedDocument(Base, TimestampMixin):
    """One generation attempt of a tailored document for an application (V0.8).

        Application
         +-- ApplicationContextSnapshot      the job, as captured when it started
         +-- GeneratedDocument (many)        TAILORED_CV / COVER_LETTER, newest last

    Every call to generate creates a *new* row; nothing is ever overwritten,
    so regeneration keeps the history. Per application and type the validated
    documents are numbered 1, 2, 3...; the highest is the current one. A
    rejected or failed attempt has no version and no content: AI output that
    did not pass truth validation is never stored as a document, only the
    validation report that says why.

    `content` is the validated result and is write-once: once an attempt is
    in a terminal state no column can change (`ImmutableRecordError`), so the
    AI original is always preserved. User edits are not part of V0.8 and, when
    they arrive, will live in their own column next to it.

    Provenance -- what the result was produced from -- is stored on the row:
    the resume version and its confirmed profile, the application context
    snapshot, a hash of the exact generation input (`input_hash`: it changes
    when the resume, the application data or the match change, which is how a
    document is recognized as outdated), the prompt version, and the AI
    provider and model. `source_facts` keeps the text of every fact the AI
    was given, so each `source_fact_ids` entry in the content stays
    resolvable after the resume is edited or deleted.

    Ownership: like the application, the document belongs to one user.
    `(application_id, user_id)` is a composite foreign key to the
    application, so a document cannot be stored for someone else's.
    """

    __tablename__ = "generated_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "user_id"],
            ["applications.id", "applications.user_id"],
            ondelete="CASCADE",
            name="fk_generated_documents_application",
        ),
        UniqueConstraint(
            "application_id",
            "document_type",
            "version_number",
            name="uq_generated_documents_version",
        ),
        # One generation per application and type at a time: a second request
        # while one is running is refused by the database, not by a race.
        Index(
            "uq_generated_documents_one_in_flight",
            "application_id",
            "document_type",
            unique=True,
            postgresql_where=text("status = 'GENERATING'"),
        ),
        # Only a validated attempt is a document: it alone has a version and
        # content. A failed or rejected attempt can never look like one.
        CheckConstraint(
            "(status = 'VALIDATED') = (version_number IS NOT NULL)",
            name="ck_generated_documents_version_iff_validated",
        ),
        CheckConstraint(
            "(status = 'VALIDATED') = (content IS NOT NULL)",
            name="ck_generated_documents_content_iff_validated",
        ),
        CheckConstraint(
            "version_number IS NULL OR version_number >= 1",
            name="ck_generated_documents_version_number",
        ),
        CheckConstraint("language ~ '^[a-z]{2}$'", name="ck_generated_documents_language"),
        # A humanized or edited version is derived from another version (V0.9).
        CheckConstraint(
            "(origin = 'AI') = (base_document_id IS NULL)",
            name="ck_generated_documents_base_iff_derived",
        ),
        Index(
            "ix_generated_documents_application_type_created",
            "application_id",
            "document_type",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Denormalized owner (see the composite foreign key above).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[GeneratedDocumentType] = mapped_column(
        Enum(GeneratedDocumentType, name="generated_document_type"), nullable=False
    )
    status: Mapped[GeneratedDocumentStatus] = mapped_column(
        Enum(GeneratedDocumentStatus, name="generated_document_status"),
        default=GeneratedDocumentStatus.GENERATING,
        nullable=False,
    )
    # 1, 2, 3... among the VALIDATED documents of this application and type.
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The application language the document is written in (never inferred).
    language: Mapped[str] = mapped_column(String(2), nullable=False)

    # --- Provenance -------------------------------------------------------------------------
    # The resume version and its confirmed profile the facts came from. NULL
    # once the user deletes the resume; the version number stays readable.
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    resume_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resume_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    resume_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The immutable context snapshot of the job this was generated for.
    application_context_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_context_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 of the exact generation input (facts, job, application data).
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    #: SHA-256 of the prompt file that produced it (V0.9; NULL for V0.8 rows).
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # --- Origin (V0.9) -----------------------------------------------------------------------
    origin: Mapped[GeneratedDocumentOrigin] = mapped_column(
        Enum(GeneratedDocumentOrigin, name="generated_document_origin"),
        default=GeneratedDocumentOrigin.AI,
        server_default=GeneratedDocumentOrigin.AI.name,
        nullable=False,
    )
    #: The version a humanized / edited one was made from. It stays untouched.
    base_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # NO ACTION: a version cannot be deleted from under the versions made from
        # it (and a SET NULL would break ck_generated_documents_base_iff_derived).
        # Deleting the whole application still removes them together.
        ForeignKey("generated_documents.id", name="fk_generated_documents_base_document"),
        nullable=True,
    )

    # --- Result -----------------------------------------------------------------------------
    # The validated `TailoredCvContent` / `CoverLetterContent`, as JSON. Only
    # ever set on a VALIDATED document.
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # id -> text of every fact the AI was given (`dict[str, SourceFact]`).
    source_facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # `ValidationReport`: why it passed or was rejected. NULL while generating
    # and for attempts that failed before validation.
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # User-safe description of a FAILED / REJECTED attempt (never provider
    # internals).
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application: Mapped["Application"] = relationship(back_populates="documents")

    @validates("input_hash")
    def _validate_hash(self, key: str, value: str) -> str:
        return require_sha256_hex(key, value)

    @validates("prompt_hash")
    def _validate_prompt_hash(self, key: str, value: str | None) -> str | None:
        return None if value is None else require_sha256_hex(key, value)

    @validates("content")
    def _validate_content(self, key: str, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return CONTENT_ADAPTER.validate_python(value).model_dump(mode="json")  # type: ignore[no-any-return]

    @validates("validation")
    def _validate_validation(self, key: str, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return ValidationReport.model_validate(value).model_dump(mode="json")

    @validates("source_facts")
    def _validate_source_facts(self, key: str, value: Any) -> dict[str, Any]:
        return {
            fact_id: SourceFact.model_validate(fact).model_dump(mode="json")
            for fact_id, fact in (value or {}).items()
        }


@event.listens_for(GeneratedDocument, "before_update")
def _freeze_finished_attempts(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    """A finished attempt (validated, rejected or failed) never changes: the
    AI original and its provenance are preserved. `updated_at` is database
    managed and not a column anyone sets."""
    state = inspect(target)
    status_history = state.attrs.status.history
    previous = (status_history.deleted or status_history.unchanged or [target.status])[0]
    if previous not in TERMINAL_STATUSES:
        return
    changed = {
        attr.key
        for attr in mapper.column_attrs
        if attr.key != "updated_at" and state.attrs[attr.key].history.has_changes()
    }
    if changed:
        raise ImmutableRecordError(
            f"GeneratedDocument is finished ({previous.value}); cannot change {sorted(changed)}"
        )
