import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, ImmutableMixin
from app.schemas.job_match import AIMatchExplanation

if TYPE_CHECKING:
    from app.models.job_match import JobMatch


class JobMatchExplanation(Base, CreatedAtMixin, ImmutableMixin):
    """An AI-written explanation of one `JobMatch` (V0.6).

    Downstream of the deterministic score: the AI only receives the
    structured result (scores, strong matches, gaps, unknowns, deal
    breakers) and returns prose that has been validated against it before it
    is stored (see `app.services.matching.explanation`). It can never change
    the score or the recommendation -- those live on the match row.

    One row per match and prompt version (`prompt_version`, e.g.
    "matching_explanation_v1"), so a result is generated once, reused, and
    always records which prompt and model wrote it. Write-once; deleted with
    its match.
    """

    __tablename__ = "job_match_explanations"
    __table_args__ = (
        UniqueConstraint(
            "job_match_id", "prompt_version", name="uq_job_match_explanations_match_prompt"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Indexed through uq_job_match_explanations_match_prompt.
    job_match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_matches.id", ondelete="CASCADE"), nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    ai_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Validated `AIMatchExplanation`, as JSON.
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    job_match: Mapped["JobMatch"] = relationship(back_populates="explanations")

    @validates("content")
    def _validate_content(self, key: str, value: Any) -> dict[str, Any]:
        return AIMatchExplanation.model_validate(value).model_dump(mode="json")

    def read_content(self) -> AIMatchExplanation:
        return AIMatchExplanation.model_validate(self.content)
