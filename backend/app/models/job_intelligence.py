import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, ImmutableMixin, require_sha256_hex
from app.schemas.job_facts import JobFacts

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.job_requirement import JobRequirement


class JobIntelligence(Base, CreatedAtMixin, ImmutableMixin):
    """A versioned, derived fact sheet for a canonical `Job` (V0.5).

        Job (canonical, V0.4 -- unchanged)
         +-- JobIntelligence   extractor v1 / taxonomy v1   (historical)
         +-- JobIntelligence   extractor v2 / taxonomy v1   is_current
               +-- JobRequirement ...

    Derived data only: it never changes the `jobs` row it describes, and can
    be thrown away and recomputed from it. Each version records what
    produced it (`extractor_version`, `taxonomy_version`), what it was
    computed from (`input_hash` of the job content, `source_posting_id`) and
    a `result_hash` of its facts and requirements, so an identical result is
    stored once per extractor/taxonomy version.

    At most one version per job is current (partial unique index). Earlier
    versions stay as long as a match references them. Only `is_current` and
    `verified_at` (set by the later extraction review checkpoint) change
    after insert. Extraction is a later V0.5 phase; nothing creates rows yet.
    """

    __tablename__ = "job_intelligence"
    __mutable_columns__: ClassVar[frozenset[str]] = frozenset({"is_current", "verified_at"})
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "result_hash",
            "extractor_version",
            "taxonomy_version",
            name="uq_job_intelligence_result",
        ),
        # Target of job_matches' composite foreign key: a match can only
        # reference intelligence of its own job.
        UniqueConstraint("id", "job_id", name="uq_job_intelligence_id_job"),
        Index(
            "uq_job_intelligence_current",
            "job_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Indexed through uq_job_intelligence_result.
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    extractor_version: Mapped[str] = mapped_column(String(50), nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Validated `JobFacts`, as JSON.
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The posting whose content was the input. Kept (as NULL) if the posting
    # is ever removed.
    source_posting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_source_postings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    job: Mapped["Job"] = relationship()
    requirements: Mapped[list["JobRequirement"]] = relationship(
        back_populates="job_intelligence", cascade="all, delete-orphan", passive_deletes=True
    )

    @validates("input_hash", "result_hash")
    def _validate_hash(self, key: str, value: str) -> str:
        return require_sha256_hex(key, value)

    @validates("facts")
    def _validate_facts(self, key: str, value: Any) -> dict[str, Any]:
        return JobFacts.model_validate(value).model_dump(mode="json")

    def read_facts(self) -> JobFacts:
        return JobFacts.model_validate(self.facts)
