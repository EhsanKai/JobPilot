"""Job Match result documents (V0.5, extended in V0.6).

`job_matches.criteria` and `job_matches.caps` record *how* a score came
about, per criterion, so a stored result can be explained and reproduced
exactly. V0.6 adds `job_matches.evidence` (`MatchEvidence`): every
component's comparison with the candidate and job values it used, and the
outcome of each deal breaker. The scoring itself lives in
`app.services.matching`; this module only defines and validates documents.
"""

import uuid
from typing import Literal

from pydantic import Field, RootModel, field_validator, model_validator

from app.schemas.document_types import EVIDENCE_MAX_CHARS, DocumentModel

# "unknown": the candidate or the job lacks the data to evaluate the
# criterion (it counts against coverage, never as a mismatch).
CriterionStatus = Literal["met", "partial", "unmet", "unknown", "not_applicable"]

_KEY_PATTERN = r"^[a-z0-9_]+(?:\.[a-z0-9_]+)*$"


class MatchCriterion(DocumentModel):
    #: Stable criterion id within the model, e.g. "skills.required".
    key: str = Field(min_length=1, max_length=100, pattern=_KEY_PATTERN)
    status: CriterionStatus
    #: The model's weight for this criterion, stored for reproducibility.
    weight: float = Field(ge=0)
    #: 0..1; None when the criterion could not be evaluated.
    score: float | None = Field(default=None, ge=0, le=1)
    #: `job_requirements` rows the criterion evaluated.
    requirement_ids: list[uuid.UUID] = Field(default_factory=list)
    #: Paths into the candidate snapshot document, e.g. "skills/3".
    candidate_refs: list[str] = Field(default_factory=list)
    #: Machine-readable explanation code, e.g. "skill_missing".
    reason_code: str | None = Field(default=None, max_length=100, pattern=_KEY_PATTERN)

    @field_validator("candidate_refs")
    @classmethod
    def _refs(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 200 for value in values):
            raise ValueError("candidate_refs must be 1-200 characters long")
        return values

    @model_validator(mode="after")
    def _score_matches_status(self) -> "MatchCriterion":
        if self.status in ("unknown", "not_applicable") and self.score is not None:
            raise ValueError(f"a criterion with status {self.status!r} has no score")
        return self


class MatchCap(DocumentModel):
    """A ceiling the model applied to the overall score."""

    key: str = Field(min_length=1, max_length=100, pattern=_KEY_PATTERN)
    #: 0..100, the same scale as `job_matches.score`.
    max_score: float = Field(ge=0, le=100)
    #: True for a hard block (e.g. a violated deal breaker).
    blocking: bool = False
    reason_code: str = Field(min_length=1, max_length=100, pattern=_KEY_PATTERN)
    criterion_key: str | None = Field(default=None, max_length=100, pattern=_KEY_PATTERN)


class MatchCriteria(RootModel[list[MatchCriterion]]):
    @field_validator("root")
    @classmethod
    def _unique(cls, values: list[MatchCriterion]) -> list[MatchCriterion]:
        if len({v.key for v in values}) != len(values):
            raise ValueError("criterion keys must be unique")
        return values


class MatchCaps(RootModel[list[MatchCap]]):
    @field_validator("root")
    @classmethod
    def _unique(cls, values: list[MatchCap]) -> list[MatchCap]:
        if len({v.key for v in values}) != len(values):
            raise ValueError("cap keys must be unique")
        return values


# ---------------------------------------------------------------------------
# V0.6: structured evidence (`job_matches.evidence`)
# ---------------------------------------------------------------------------

MATCH_EVIDENCE_SCHEMA_VERSION = "match_evidence_v1"

#: Component-level outcome. "unknown" means the candidate or the job lacks
#: the data -- it is excluded from the score, never counted as a mismatch.
ComponentStatus = Literal["match", "partial", "mismatch", "unknown"]
#: Item-level outcome (one skill, one language, ...). "missing" is reserved
#: for something the job asks for that the candidate's profile doesn't show.
ItemStatus = Literal["match", "partial", "missing", "unknown"]
RequirementImportance = Literal["required", "preferred", "unspecified", "contextual"]
ComponentGroup = Literal["job", "preference"]
ComponentKey = Literal[
    # Job Match: can the candidate do the job?
    "skills",
    "experience",
    "title",
    "seniority",
    "industry",
    "languages",
    "education",
    # Preference Match: does the job fit what the candidate wants?
    "location",
    "work_model",
    "salary",
    "employment_type",
    "desired_title",
    "preferred_industry",
    "travel",
]
DealBreakerStatus = Literal["hard_block", "clear", "unknown"]

DetailValue = str | int | float | bool | None


class CandidateEvidence(DocumentModel):
    """What the candidate side of a comparison was, and where it came from."""

    value: str = Field(min_length=1, max_length=500)
    #: "user" (V0.1 profile / preferences) or "resume" (confirmed Resume
    #: Profile); None for values derived from both.
    origin: Literal["user", "resume"] | None = None
    #: Path into the candidate snapshot document, e.g. "skills/2".
    ref: str | None = Field(default=None, max_length=200)
    #: For resume-derived values: "resume_profile:<id>/<item>".
    evidence_ref: str | None = Field(default=None, max_length=200)


class JobEvidence(DocumentModel):
    """What the job side of a comparison was, and where it came from. `text`
    is a verbatim quote from the posting when one exists -- never invented."""

    value: str = Field(min_length=1, max_length=500)
    source_field: str | None = Field(default=None, max_length=50)
    section: str | None = Field(default=None, max_length=50)
    text: str | None = Field(default=None, max_length=EVIDENCE_MAX_CHARS)
    requirement_id: uuid.UUID | None = None


class ItemResult(DocumentModel):
    """One compared item inside a component (a skill, a language, ...)."""

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=255)
    importance: RequirementImportance | None = None
    status: ItemStatus
    score: float | None = Field(default=None, ge=0, le=1)
    reason_code: str = Field(min_length=1, max_length=100, pattern=_KEY_PATTERN)
    candidate: CandidateEvidence | None = None
    job: JobEvidence | None = None
    #: Alternatives ("Python or Java"): items sharing a group are satisfied
    #: by any one of them.
    group: str | None = Field(default=None, max_length=50)


class ComponentResult(DocumentModel):
    """The outcome of one scoring component, with everything it compared."""

    key: ComponentKey
    group: ComponentGroup
    status: ComponentStatus
    #: Component-specific label, e.g. "strong_match" / "related_role" for
    #: the title component.
    label: str | None = Field(default=None, max_length=50, pattern=_KEY_PATTERN)
    #: 0..1; None exactly when the status is "unknown".
    score: float | None = Field(default=None, ge=0, le=1)
    #: The model's weight for this component (stored for reproducibility).
    weight: float = Field(ge=0)
    reason_code: str = Field(min_length=1, max_length=100, pattern=_KEY_PATTERN)
    candidate: list[CandidateEvidence] = Field(default_factory=list)
    job: list[JobEvidence] = Field(default_factory=list)
    items: list[ItemResult] = Field(default_factory=list)
    details: dict[str, DetailValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _score_matches_status(self) -> "ComponentResult":
        if (self.status == "unknown") != (self.score is None):
            raise ValueError("a component has a score exactly when its status is known")
        return self


class DealBreakerResult(DocumentModel):
    """How one explicit user constraint fared against the job."""

    #: A `DealBreakerType` value, or "relocation_preference" for the
    #: Job Preferences "relocation: no" answer.
    type: str = Field(min_length=1, max_length=50, pattern=_KEY_PATTERN)
    source: Literal["deal_breaker", "preference"]
    status: DealBreakerStatus
    reason_code: str = Field(min_length=1, max_length=100, pattern=_KEY_PATTERN)
    details: dict[str, DetailValue] = Field(default_factory=dict)


class MatchEvidence(DocumentModel):
    """Root of `job_matches.evidence` (V0.6). Empty for pre-V0.6 rows."""

    schema_version: str = Field(
        default=MATCH_EVIDENCE_SCHEMA_VERSION, min_length=1, max_length=50
    )
    components: list[ComponentResult] = Field(default_factory=list)
    deal_breakers: list[DealBreakerResult] = Field(default_factory=list)

    @field_validator("components")
    @classmethod
    def _unique_components(cls, values: list[ComponentResult]) -> list[ComponentResult]:
        if len({v.key for v in values}) != len(values):
            raise ValueError("component keys must be unique")
        return values

    def component(self, key: str) -> ComponentResult | None:
        return next((c for c in self.components if c.key == key), None)


# ---------------------------------------------------------------------------
# V0.6: AI explanation (`job_match_explanations.content`)
# ---------------------------------------------------------------------------


class ExplainedPoint(DocumentModel):
    #: The id of one fact the AI was given (e.g. "skills:python"). Only
    #: facts that exist can be referenced.
    fact_id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=300)


class AIMatchExplanation(DocumentModel):
    """What the AI explanation layer must return. It deliberately has no
    score, percentage, recommendation or requirement field: the AI explains
    the deterministic result, it cannot change it."""

    summary: str = Field(min_length=1, max_length=700)
    strengths: list[ExplainedPoint] = Field(default_factory=list, max_length=6)
    concerns: list[ExplainedPoint] = Field(default_factory=list, max_length=6)
