"""Job Match result documents (V0.5).

`job_matches.criteria` and `job_matches.caps` record *how* a score came
about, per criterion, so a stored result can be explained and reproduced
exactly. Scoring itself is a later V0.5 phase; this module only defines and
validates the documents.
"""

import uuid
from typing import Literal

from pydantic import Field, RootModel, field_validator, model_validator

from app.schemas.document_types import DocumentModel

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
