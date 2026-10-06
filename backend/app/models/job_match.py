import enum
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, ImmutableMixin, require_sha256_hex
from app.schemas.job_match import MatchCaps, MatchCriteria, MatchEvidence

if TYPE_CHECKING:
    from app.models.candidate_profile_version import CandidateProfileVersion
    from app.models.job_intelligence import JobIntelligence
    from app.models.job_match_explanation import JobMatchExplanation


class MatchVerdict(str, enum.Enum):
    STRONG = "strong"
    GOOD = "good"
    PARTIAL = "partial"
    WEAK = "weak"
    # A hard block (e.g. a violated deal breaker); always with is_blocked.
    BLOCKED = "blocked"
    # Too little known about the candidate or the job to judge.
    INSUFFICIENT_DATA = "insufficient_data"


class JobMatch(Base, CreatedAtMixin, ImmutableMixin):
    """A reproducible match result for one user and one job (V0.5).

    A result is a pure function of its inputs, and each input is pinned:

    - `candidate_profile_version_id`: the immutable candidate snapshot;
    - `job_intelligence_id`: the job fact sheet version;
    - `model_id` + `model_hash`: the scoring model and a hash of its exact
      definition (weights, thresholds, caps), so a result computed by an
      earlier definition is never mistaken for the current one.

    `criteria` / `caps` record how the score came about. `score` (0-100) may
    be NULL when nothing could be scored; `coverage` (0-1) is the share of
    the model's weight that could be evaluated with known data.

    The same inputs give one row (`uq_job_matches_inputs`); at most one row
    per user and job is current. Historical rows are kept, and the versions
    they reference cannot be deleted while they exist (NO ACTION foreign
    keys). Deleting the user or the job removes everything. Only
    `is_current` changes after insert.

    V0.6 (`app.services.matching`) writes these rows. `score` is the
    Overall Match; `job_score` / `preference_score` are the two separate
    sub-scores it is built from (each NULL when nothing in it could be
    evaluated), with their own coverage. `evidence` holds every component's
    structured comparison and the deal-breaker outcomes;
    `taxonomy_version` is the taxonomy the comparison used. The (user_id,
    job_id) pair is the natural key of the user's `UserJob`: results are
    only ever computed and read through it (see the matching service).
    """

    __tablename__ = "job_matches"
    __mutable_columns__: ClassVar[frozenset[str]] = frozenset({"is_current"})
    __table_args__ = (
        # Composite keys tie each referenced version to this row's own user /
        # job. No ON DELETE action: a referenced version cannot be deleted on
        # its own (user/job deletion cascades to both sides).
        ForeignKeyConstraint(
            ["candidate_profile_version_id", "user_id"],
            ["candidate_profile_versions.id", "candidate_profile_versions.user_id"],
            name="fk_job_matches_candidate_profile_version",
        ),
        ForeignKeyConstraint(
            ["job_intelligence_id", "job_id"],
            ["job_intelligence.id", "job_intelligence.job_id"],
            name="fk_job_matches_job_intelligence",
        ),
        UniqueConstraint(
            "user_id",
            "job_id",
            "candidate_profile_version_id",
            "job_intelligence_id",
            "model_id",
            name="uq_job_matches_inputs",
        ),
        Index(
            "uq_job_matches_current",
            "user_id",
            "job_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        # "My current matches, best first."
        Index(
            "ix_job_matches_user_current_score",
            "user_id",
            text("score DESC NULLS LAST"),
            postgresql_where=text("is_current"),
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)", name="ck_job_matches_score"
        ),
        CheckConstraint("coverage >= 0 AND coverage <= 1", name="ck_job_matches_coverage"),
        CheckConstraint("is_blocked = (verdict = 'BLOCKED')", name="ck_job_matches_blocked"),
        CheckConstraint(
            "job_score IS NULL OR (job_score >= 0 AND job_score <= 100)",
            name="ck_job_matches_job_score",
        ),
        CheckConstraint(
            "preference_score IS NULL OR (preference_score >= 0 AND preference_score <= 100)",
            name="ck_job_matches_preference_score",
        ),
        CheckConstraint(
            "job_coverage IS NULL OR (job_coverage >= 0 AND job_coverage <= 1)",
            name="ck_job_matches_job_coverage",
        ),
        CheckConstraint(
            "preference_coverage IS NULL OR "
            "(preference_coverage >= 0 AND preference_coverage <= 1)",
            name="ck_job_matches_preference_coverage",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Indexed through uq_job_matches_inputs.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_profile_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    job_intelligence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    model_id: Mapped[str] = mapped_column(String(50), nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    coverage: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    verdict: Mapped[MatchVerdict] = mapped_column(
        Enum(MatchVerdict, name="job_match_verdict"), nullable=False
    )
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Validated `MatchCriteria` / `MatchCaps`, as JSON.
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    caps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # --- V0.6 ------------------------------------------------------------------------
    job_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    preference_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    job_coverage: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    preference_coverage: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    taxonomy_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Validated `MatchEvidence`, as JSON ({} for rows written before V0.6).
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    candidate_profile_version: Mapped["CandidateProfileVersion"] = relationship()
    job_intelligence: Mapped["JobIntelligence"] = relationship()
    explanations: Mapped[list["JobMatchExplanation"]] = relationship(
        back_populates="job_match", cascade="all, delete-orphan", passive_deletes=True
    )

    @validates("model_hash")
    def _validate_hash(self, key: str, value: str) -> str:
        return require_sha256_hex(key, value)

    @validates("criteria")
    def _validate_criteria(self, key: str, value: Any) -> list[dict[str, Any]]:
        dumped: list[dict[str, Any]] = MatchCriteria.model_validate(value).model_dump(mode="json")
        return dumped

    @validates("caps")
    def _validate_caps(self, key: str, value: Any) -> list[dict[str, Any]]:
        dumped: list[dict[str, Any]] = MatchCaps.model_validate(value).model_dump(mode="json")
        return dumped

    @validates("evidence")
    def _validate_evidence(self, key: str, value: Any) -> dict[str, Any]:
        return MatchEvidence.model_validate(value).model_dump(mode="json")

    def read_evidence(self) -> MatchEvidence:
        return MatchEvidence.model_validate(self.evidence or {})

    def read_criteria(self) -> MatchCriteria:
        return MatchCriteria.model_validate(self.criteria)

    def read_caps(self) -> MatchCaps:
        return MatchCaps.model_validate(self.caps)
