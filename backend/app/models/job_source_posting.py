import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.job import Job


class JobSourcePosting(Base, TimestampMixin):
    """One posting of a job at one source (V0.4).

        Job (canonical, "the real-world vacancy")
         +-- JobSourcePosting  greenhouse / 4012345 / https://...
         +-- JobSourcePosting  lever      / 5ac2... / https://...

    A posting is how JobPilot *found* a job: the source, the source's own id,
    the original URL, what the source said (title, company, location as
    written, its normalized content and raw payload) and when it was seen.
    Postings are never discarded when a job is matched across sources --
    future application automation needs every original URL.

    Identity:

    - `dedup_key` (unique) is the posting's identity within its source:
      `source:id:<source_job_id>`, or `source:url:<hash>` for sources without
      stable ids. Rows migrated from V0.3 with an ambiguous identity keep
      `legacy:<id>`.
    - (`source`, `source_job_id`) is unique as well (partial index; legacy
      rows excluded).
    - `normalized_*` and `url_key` are the comparison keys cross-source
      deduplication uses (see `app.services.job_discovery.deduplication`).
      `identity_version` records which rules computed them.

    Freshness: `first_seen_at` / `last_seen_at` are when a discovery run
    first / last received the posting from its source (whether or not it
    matched that run's criteria). `is_active` turns false only when the
    source's complete board listing no longer contains the posting; nothing
    is ever deleted.

    `raw_data` and `normalized_data` are internal and never returned by the API.
    """

    __tablename__ = "job_source_postings"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_job_source_postings_dedup_key"),
        Index(
            "uq_job_source_postings_source_job_id",
            "source",
            "source_job_id",
            unique=True,
            postgresql_where=text("source_job_id IS NOT NULL AND dedup_key NOT LIKE 'legacy:%'"),
        ),
        Index("ix_job_source_postings_identity", "normalized_company_name", "normalized_title"),
        Index("ix_job_source_postings_url_key", "url_key"),
        Index("ix_job_source_postings_board", "source", "source_company_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- Source identity ------------------------------------------------------
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(400), nullable=False)
    # The board / site within the source (e.g. a Greenhouse board token).
    source_company_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- URLs ---------------------------------------------------------------------
    # Exactly as the source returned it.
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Normalized but still clickable (tracking parameters, fragment removed).
    canonical_source_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    apply_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # --- What the source said (display values, as written) --------------------
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Comparison keys (cross-source identity) ---------------------------------
    url_key: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    normalized_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    normalized_company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_location: Mapped[str | None] = mapped_column(String(600), nullable=True)
    identity_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- Provenance ---------------------------------------------------------------
    # The posting's normalized content (`NormalizedJob.content_fields()` as
    # JSON). The canonical Job is built from it. NULL for postings migrated
    # from V0.3 until their source returns them again.
    normalized_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalizer_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- Freshness ----------------------------------------------------------------
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    job: Mapped["Job"] = relationship(back_populates="source_postings", foreign_keys=[job_id])
