"""Reading match results: staleness and API read models (V0.6).

A stored result is **stale** when anything it was computed from has
changed since:

    profile_changed     the candidate snapshot built from today's profile,
                        preferences and confirmed resume has a different
                        content hash
    job_changed         the canonical job content (extractor input hash) or
                        the extractor version changed
    algorithm_changed   the scoring model (`MATCHING_ALGORITHM_VERSION` /
                        `MODEL_HASH`) changed
    taxonomy_changed    the taxonomy version changed

Stale results are still returned -- clearly marked, never presented as
current -- so the user sees what they had and can recalculate. Nothing is
recalculated implicitly on read.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.job import Job
from app.models.job_match import JobMatch
from app.models.job_match_explanation import JobMatchExplanation
from app.models.user import User
from app.models.user_job import UserJob
from app.schemas.job_match import MatchCaps
from app.schemas.matching import (
    AIExplanationRead,
    MatchCap,
    MatchResultRead,
    MatchSummaryRead,
    MatchVersions,
    ScoreBand,
    StaleReason,
)
from app.services.matching import model
from app.services.matching.candidate import build_snapshot
from app.services.matching.engine import overall
from app.services.matching.explanation import (
    MATCH_EXPLANATION_PROMPT_VERSION,
    RECOMMENDATIONS,
    summarize,
)
from app.services.matching.extraction import JOB_EXTRACTOR_VERSION, JobContent
from app.services.taxonomy import TAXONOMY_VERSION


def current_matches(
    db: Session, user: User, job_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, JobMatch]:
    """The user's current result per job (only ever the user's own rows)."""
    ids = list(job_ids)
    if not ids:
        return {}
    rows = db.scalars(
        select(JobMatch)
        .where(JobMatch.user_id == user.id, JobMatch.job_id.in_(ids), JobMatch.is_current)
        .options(
            selectinload(JobMatch.candidate_profile_version),
            selectinload(JobMatch.job_intelligence),
        )
    ).all()
    return {row.job_id: row for row in rows}


def stale_reasons(match: JobMatch, job: Job, candidate_hash: str) -> list[StaleReason]:
    reasons: list[StaleReason] = []
    if match.candidate_profile_version.content_hash != candidate_hash:
        reasons.append("profile_changed")
    intelligence = match.job_intelligence
    if (
        intelligence.extractor_version != JOB_EXTRACTOR_VERSION
        or intelligence.input_hash != JobContent.from_job(job).input_hash()
    ):
        reasons.append("job_changed")
    if (
        match.model_id != model.MATCHING_ALGORITHM_VERSION
        or match.model_hash != model.MODEL_HASH
    ):
        reasons.append("algorithm_changed")
    if match.taxonomy_version != TAXONOMY_VERSION:
        reasons.append("taxonomy_changed")
    return reasons


def _int(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]


def to_summary(match: JobMatch, reasons: list[StaleReason]) -> MatchSummaryRead:
    code, label = RECOMMENDATIONS[match.verdict]
    overall = _int(match.score)
    band = model.band(overall)
    return MatchSummaryRead(
        status="stale" if reasons else "calculated",
        stale_reasons=reasons,
        overall_match=overall,
        job_match=_int(match.job_score),
        preference_match=_int(match.preference_score),
        recommendation=code,  # type: ignore[arg-type]
        recommendation_label=label,
        band=ScoreBand(key=band[0], label=band[1]) if band else None,
        is_blocked=match.is_blocked,
        calculated_at=match.created_at,
    )


def summaries(db: Session, user: User, jobs: Iterable[Job]) -> dict[uuid.UUID, MatchSummaryRead]:
    """Match summaries for a page of jobs (one snapshot build, one query)."""
    by_id = {job.id: job for job in jobs}
    matches = current_matches(db, user, by_id)
    if not matches:
        return {}
    candidate_hash = build_snapshot(db, user).content_hash
    return {
        job_id: to_summary(match, stale_reasons(match, by_id[job_id], candidate_hash))
        for job_id, match in matches.items()
    }


def stored_explanation(db: Session, match: JobMatch) -> JobMatchExplanation | None:
    return db.scalar(
        select(JobMatchExplanation).where(
            JobMatchExplanation.job_match_id == match.id,
            JobMatchExplanation.prompt_version == MATCH_EXPLANATION_PROMPT_VERSION,
        )
    )


def to_ai_read(explanation: JobMatchExplanation) -> AIExplanationRead:
    content = explanation.read_content()
    return AIExplanationRead(
        prompt_version=explanation.prompt_version,
        ai_provider=explanation.ai_provider,
        ai_model=explanation.ai_model,
        summary=content.summary,
        strengths=content.strengths,
        concerns=content.concerns,
        created_at=explanation.created_at,
    )


def to_result(
    db: Session, match: JobMatch, user_job: UserJob, reasons: list[StaleReason]
) -> MatchResultRead:
    evidence = match.read_evidence()
    summary = summarize(evidence)
    snapshot = match.candidate_profile_version
    explanation = stored_explanation(db, match)
    caps = MatchCaps.model_validate(match.caps).root
    uncapped = overall(_int(match.job_score), _int(match.preference_score))
    return MatchResultRead(
        **to_summary(match, reasons).model_dump(),
        job_id=match.job_id,
        user_job_id=user_job.id,
        overall_match_uncapped=uncapped,
        job_match_coverage=float(match.job_coverage) if match.job_coverage is not None else None,
        preference_match_coverage=(
            float(match.preference_coverage) if match.preference_coverage is not None else None
        ),
        coverage=float(match.coverage),
        strong_matches=summary.strong_matches,
        gaps=summary.gaps,
        unknowns=summary.unknowns,
        deal_breakers=summary.deal_breakers,
        components=evidence.components,
        deal_breaker_results=evidence.deal_breakers,
        caps=[MatchCap(**cap.model_dump()) for cap in caps],
        versions=MatchVersions(
            matching_algorithm_version=match.model_id,
            model_hash=match.model_hash,
            taxonomy_version=match.taxonomy_version,
            job_extractor_version=match.job_intelligence.extractor_version,
            candidate_profile_version=snapshot.version_number,
            candidate_schema_version=snapshot.schema_version,
        ),
        ai_explanation=to_ai_read(explanation) if explanation else None,
    )
