"""Request/response schemas for Job Matching & Explainability (V0.6).

Everything the UI needs to show state is structured: scores, the
recommendation code, the band, per-item statuses and kinds. Texts are for
display only -- the frontend never parses them.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.job_match import ComponentResult, DealBreakerResult, ExplainedPoint
from app.services.matching.explanation import ExplanationItem

MatchStatus = Literal["not_calculated", "calculated", "stale"]
StaleReason = Literal["profile_changed", "job_changed", "algorithm_changed", "taxonomy_changed"]
Recommendation = Literal[
    "STRONG_MATCH",
    "GOOD_MATCH",
    "POTENTIAL_MATCH",
    "WEAK_MATCH",
    "NOT_RECOMMENDED",
    "INSUFFICIENT_DATA",
]


class ScoreBand(BaseModel):
    """Display band of a 0-100 score (configured in the scoring model)."""

    key: str
    label: str


class MatchCap(BaseModel):
    key: str
    max_score: float
    blocking: bool
    reason_code: str
    criterion_key: str | None


class MatchVersions(BaseModel):
    """What produced a result. A result whose versions differ from the
    current ones is stale."""

    matching_algorithm_version: str
    model_hash: str
    taxonomy_version: str | None
    job_extractor_version: str
    candidate_profile_version: int
    candidate_schema_version: str


class MatchSummaryRead(BaseModel):
    """The compact match state shown on job cards. Absent (null) when no
    match has been calculated for the job."""

    status: Literal["calculated", "stale"]
    stale_reasons: list[StaleReason]
    overall_match: int | None
    job_match: int | None
    preference_match: int | None
    recommendation: Recommendation
    recommendation_label: str
    band: ScoreBand | None
    is_blocked: bool
    calculated_at: datetime


class AIExplanationRead(BaseModel):
    prompt_version: str
    ai_provider: str
    ai_model: str | None
    summary: str
    strengths: list[ExplainedPoint]
    concerns: list[ExplainedPoint]
    created_at: datetime


class MatchResultRead(MatchSummaryRead):
    """A full, explainable match result."""

    job_id: uuid.UUID
    user_job_id: uuid.UUID
    #: Overall Match before caps; equal to `overall_match` unless a cap applied.
    overall_match_uncapped: int | None
    #: Share of each sub-score's weight that could be evaluated (0-1).
    job_match_coverage: float | None
    preference_match_coverage: float | None
    coverage: float
    strong_matches: list[ExplanationItem]
    gaps: list[ExplanationItem]
    unknowns: list[ExplanationItem]
    deal_breakers: list[ExplanationItem]
    #: Every component with its structured evidence (expandable detail).
    components: list[ComponentResult]
    deal_breaker_results: list[DealBreakerResult]
    caps: list[MatchCap]
    versions: MatchVersions
    #: The stored AI explanation for the current prompt version, if one
    #: was requested.
    ai_explanation: AIExplanationRead | None


class MatchStateRead(BaseModel):
    """`GET /jobs/{id}/match`: the user's match state for one job."""

    job_id: uuid.UUID
    status: MatchStatus
    result: MatchResultRead | None


class BatchMatchRequest(BaseModel):
    """`POST /jobs/match`. Deterministic only: never calls the AI."""

    model_config = ConfigDict(extra="forbid")

    #: Jobs to match (must be in the user's job list). Without ids, the
    #: user's most recently discovered jobs without a current match.
    job_ids: list[uuid.UUID] | None = Field(default=None, max_length=25)
    #: Upper bound on jobs processed in this request.
    limit: int = Field(default=10, ge=1, le=25)
    #: Recalculate even when a current, non-stale result exists.
    force: bool = False


class BatchMatchItem(BaseModel):
    job_id: uuid.UUID
    #: "calculated" (new result), "unchanged" (an up-to-date result existed).
    outcome: Literal["calculated", "unchanged"]
    match: MatchSummaryRead


class BatchMatchResponse(BaseModel):
    results: list[BatchMatchItem]
    calculated: int
    unchanged: int
    #: Requested ids that are not in the user's job list (never another
    #: user's data -- unknown and foreign ids look the same).
    not_found: list[uuid.UUID]
