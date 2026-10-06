import enum
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.models.job import SalaryPeriod
from app.models.mixins import CreatedAtMixin, ImmutableMixin

if TYPE_CHECKING:
    from app.models.job_intelligence import JobIntelligence


class ExtractionConfidence(str, enum.Enum):
    """Extraction certainty (not importance), the same scale Resume
    Intelligence uses for extracted facts."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RequirementKind(str, enum.Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    # Mentioned by the posting (e.g. the team's stack) without being asked
    # of the candidate.
    CONTEXTUAL = "contextual"


class RequirementCategory(str, enum.Enum):
    SKILL = "skill"
    EXPERIENCE = "experience"
    LANGUAGE = "language"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    SENIORITY = "seniority"
    WORK_MODEL = "work_model"
    EMPLOYMENT_TYPE = "employment_type"
    SALARY = "salary"
    AUTHORIZATION = "authorization"
    TRAVEL = "travel"


class JobRequirement(Base, CreatedAtMixin, ImmutableMixin):
    """One requirement a `JobIntelligence` version found in its job (V0.5).

    Value columns (all optional; which ones apply depends on `category`):

    - `skill_key`: skill taxonomy key (SKILL only; NULL until skill
      normalization exists).
    - `value_key`: a normalized vocabulary value -- a language code, an
      enum value such as "remote" or "senior", an ISO country code, ...
    - `value_text`: the requirement as stated, when there is no vocabulary
      value (e.g. a certification name).
    - `level`: a qualifier such as a CEFR level.
    - `min_years`: required years (experience, or experience with a skill).
    - `salary_*`: a salary range (SALARY only).

    Provenance: `source_field` (canonical job field read), `section_kind`
    (posting section, e.g. "requirements" or "nice_to_have"),
    `extraction_method`, `confidence`, and the verbatim `evidence_text` with
    its character offset in the source field. The posting the input came
    from is `job_intelligence.source_posting_id`.

    Write-once, and deleted with its JobIntelligence. Extraction is a later
    V0.5 phase; nothing creates rows yet.
    """

    __tablename__ = "job_requirements"
    __table_args__ = (
        CheckConstraint(
            "skill_key IS NULL OR category = 'SKILL'", name="ck_job_requirements_skill_key"
        ),
        CheckConstraint(
            "category = 'SALARY' OR (salary_min IS NULL AND salary_max IS NULL "
            "AND salary_currency IS NULL AND salary_period IS NULL)",
            name="ck_job_requirements_salary_fields",
        ),
        CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max",
            name="ck_job_requirements_salary_range",
        ),
        CheckConstraint(
            "min_years IS NULL OR min_years >= 0", name="ck_job_requirements_min_years"
        ),
        CheckConstraint(
            "evidence_offset IS NULL OR (evidence_offset >= 0 AND evidence_text IS NOT NULL)",
            name="ck_job_requirements_evidence_offset",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_intelligence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_intelligence.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[RequirementKind] = mapped_column(
        Enum(RequirementKind, name="job_requirement_kind"), nullable=False
    )
    category: Mapped[RequirementCategory] = mapped_column(
        Enum(RequirementCategory, name="job_requirement_category"), nullable=False
    )

    # --- Value ----------------------------------------------------------------------
    skill_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    value_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    min_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    salary_period: Mapped[SalaryPeriod | None] = mapped_column(
        Enum(SalaryPeriod, name="salary_period"), nullable=True
    )

    # --- Provenance -----------------------------------------------------------------
    source_field: Mapped[str] = mapped_column(String(50), nullable=False)
    section_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[ExtractionConfidence] = mapped_column(
        Enum(ExtractionConfidence, name="extraction_confidence"), nullable=False
    )
    evidence_text: Mapped[str | None] = mapped_column(String(600), nullable=True)
    evidence_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)

    job_intelligence: Mapped["JobIntelligence"] = relationship(back_populates="requirements")

    @validates("salary_currency")
    def _validate_currency(self, key: str, value: str | None) -> str | None:
        if value is not None and not (len(value) == 3 and value.isascii() and value.isupper()):
            raise ValueError("salary_currency must be a 3-letter upper-case ISO 4217 code")
        return value
