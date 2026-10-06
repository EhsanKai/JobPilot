"""The deterministic matching engine (`matching_v1`).

    CandidateProfileDocument + JobView
            |
            v
    components (skills, experience, ..., location, salary, ...)   -> evidence
            |
            v
    Job Match (0-100)      Preference Match (0-100)                -> coverage
            \\                  /
             Overall Match = 70% Job + 30% Preference
            |
            v
    caps (hard block -> max 40, required qualification missing -> max 69)
            |
            v
    recommendation (verdict)

A pure function: no database, no clock, no randomness and no AI. The same
inputs always give the same `MatchOutcome`. The AI explanation layer only
ever reads this outcome; it cannot change it.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.models.job_match import MatchVerdict
from app.schemas.candidate_profile import CandidateProfileDocument
from app.schemas.job_match import ComponentResult, DealBreakerResult, MatchEvidence
from app.services.matching import model
from app.services.matching.components import (
    JOB_COMPONENTS,
    PREFERENCE_COMPONENTS,
    Context,
    JobView,
    deal_breakers,
)

_CRITERION_STATUS = {
    "match": "met",
    "partial": "partial",
    "mismatch": "unmet",
    "unknown": "unknown",
}


@dataclass(frozen=True)
class MatchOutcome:
    job_score: int | None
    preference_score: int | None
    #: The Overall Match after caps -- what `job_matches.score` stores.
    overall_score: int | None
    #: The Overall Match before caps (never hidden: the API returns both).
    overall_uncapped: int | None
    job_coverage: float
    preference_coverage: float
    #: Share of the whole model's weight that could be evaluated.
    coverage: float
    verdict: MatchVerdict
    is_blocked: bool
    evidence: MatchEvidence
    criteria: list[dict[str, Any]]
    caps: list[dict[str, Any]]


def round_score(value: float) -> int:
    """0-100 integer, rounded half up (scores are not more precise than that)."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def aggregate(
    components: list[ComponentResult], weights: dict[str, int]
) -> tuple[int | None, float]:
    """(score 0-100 or None, coverage 0-1). UNKNOWN components are left out
    of the score; coverage says how much of the weight was evaluable."""
    total = sum(weights.values())
    known = [c for c in components if c.score is not None]
    known_weight = sum(weights[c.key] for c in known)
    if known_weight == 0:
        return None, 0.0
    raw = sum(weights[c.key] * (c.score or 0.0) for c in known) / known_weight
    return round_score(100 * raw), round(known_weight / total, 3)


def overall(job_score: int | None, preference_score: int | None) -> int | None:
    """70% Job Match + 30% Preference Match, from the two *displayed*
    integer scores so anyone can check the arithmetic. Without a Job Match
    there is no Overall Match; without a Preference Match it is the Job
    Match."""
    if job_score is None:
        return None
    if preference_score is None:
        return job_score
    weights = model.OVERALL_WEIGHTS
    return round_score(weights["job"] * job_score + weights["preference"] * preference_score)


def recommendation(
    score: int | None, job_coverage: float, job_score: int | None, blocked: bool
) -> MatchVerdict:
    if blocked:
        return MatchVerdict.BLOCKED
    if job_score is None or score is None or job_coverage < model.MIN_JOB_COVERAGE:
        return MatchVerdict.INSUFFICIENT_DATA
    for threshold, verdict in model.RECOMMENDATION_THRESHOLDS:
        if score >= threshold:
            return verdict
    return MatchVerdict.WEAK


def _criteria(components: list[ComponentResult]) -> list[dict[str, Any]]:
    criteria = []
    for c in components:
        requirement_ids = {
            str(e.requirement_id) for e in c.job if e.requirement_id is not None
        } | {
            str(i.job.requirement_id)
            for i in c.items
            if i.job is not None and i.job.requirement_id is not None
        }
        refs = {e.ref for e in c.candidate if e.ref} | {
            i.candidate.ref for i in c.items if i.candidate is not None and i.candidate.ref
        }
        criteria.append(
            {
                "key": f"{c.group}.{c.key}",
                "status": _CRITERION_STATUS[c.status],
                "weight": c.weight,
                "score": c.score,
                "requirement_ids": sorted(requirement_ids),
                "candidate_refs": sorted(refs),
                "reason_code": c.reason_code,
            }
        )
    return criteria


def _caps(
    blocks: list[DealBreakerResult], flags: dict[str, str]
) -> list[dict[str, Any]]:
    caps: list[dict[str, Any]] = []
    for block in blocks:
        caps.append(
            {
                "key": f"hard_block.{block.type}",
                "max_score": model.HARD_BLOCK_CAP,
                "blocking": True,
                "reason_code": block.reason_code,
                "criterion_key": None,
            }
        )
    for component, reason in sorted(flags.items()):
        caps.append(
            {
                "key": f"required_missing.{component}",
                "max_score": model.REQUIRED_MISSING_CAP,
                "blocking": False,
                "reason_code": reason,
                "criterion_key": f"job.{component}",
            }
        )
    return caps


def evaluate(candidate: CandidateProfileDocument, job: JobView) -> MatchOutcome:
    """Score one candidate against one job (see module docs)."""
    ctx = Context(candidate=candidate, job=job)
    job_components = [component(ctx) for component in JOB_COMPONENTS]
    # Location runs first among the preferences: deal breakers read what it
    # established (relocation needed, inside preferred locations).
    preference_components = [component(ctx) for component in PREFERENCE_COMPONENTS]
    breakers = deal_breakers(ctx)

    job_score, job_coverage = aggregate(job_components, model.JOB_WEIGHTS)
    preference_score, preference_coverage = aggregate(
        preference_components, model.PREFERENCE_WEIGHTS
    )
    uncapped = overall(job_score, preference_score)
    blocks = [b for b in breakers if b.status == "hard_block"]
    caps = _caps(blocks, ctx.blocking_flags)
    score = uncapped
    if score is not None:
        for cap in caps:
            score = min(score, int(cap["max_score"]))
    blocked = bool(blocks)
    total_weight = sum(model.JOB_WEIGHTS.values()) + sum(model.PREFERENCE_WEIGHTS.values())
    known_weight = sum(
        c.weight for c in [*job_components, *preference_components] if c.score is not None
    )
    return MatchOutcome(
        job_score=job_score,
        preference_score=preference_score,
        overall_score=score,
        overall_uncapped=uncapped,
        job_coverage=job_coverage,
        preference_coverage=preference_coverage,
        coverage=round(known_weight / total_weight, 3),
        verdict=recommendation(score, job_coverage, job_score, blocked),
        is_blocked=blocked,
        evidence=MatchEvidence(
            components=[*job_components, *preference_components], deal_breakers=breakers
        ),
        criteria=_criteria([*job_components, *preference_components]),
        caps=caps,
    )
