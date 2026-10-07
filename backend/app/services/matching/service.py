"""Job matching orchestration (V0.6): the database side of the engine.

    calculate(user, job)
        UserJob visibility check (404 otherwise -- never another user's job)
        -> candidate snapshot      (stored once per distinct content)
        -> job intelligence        (stored once per job content / extractor)
        -> engine.evaluate          (pure, deterministic)
        -> job_matches row          (one per input set; becomes current)

Results are user-specific: keyed by (user_id, job_id), the natural key of
the user's `UserJob`, and only ever read through it. A global `Job` has no
score of its own.

Calculation is explicit ("Calculate match" / "Recalculate"); reads never
recalculate. Batch calculation is deterministic only and never calls the
AI. The AI explanation is a separate, explicit, cached request.
"""

import uuid

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.ai.provider import (
    AIError,
    AINotConfiguredError,
    AIProvider,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.ai.providers.disabled import AIProcessingDisabledByUserError
from app.core.errors import AppError, ConflictError
from app.core.logging import get_logger
from app.models.candidate_profile_version import CandidateProfileVersion
from app.models.job import Job
from app.models.job_intelligence import JobIntelligence
from app.models.job_match import JobMatch
from app.models.job_match_explanation import JobMatchExplanation
from app.models.job_requirement import JobRequirement
from app.models.user import User
from app.models.user_job import UserJob
from app.schemas.matching import (
    AIExplanationRead,
    BatchMatchItem,
    BatchMatchRequest,
    BatchMatchResponse,
    MatchStateRead,
)
from app.services.job_discovery.job_service import get_visible_job
from app.services.matching import model
from app.services.matching.candidate import build_snapshot, ensure_snapshot
from app.services.matching.components import JobView, RequirementView
from app.services.matching.engine import evaluate
from app.services.matching.explanation import (
    MATCH_EXPLANATION_PROMPT_VERSION,
    ExplanationRejectedError,
    ai_facts,
    generate_ai_explanation,
    summarize,
)
from app.services.matching.extraction import JOB_EXTRACTOR_VERSION, JobContent, extract
from app.services.matching.status import (
    current_matches,
    stale_reasons,
    stored_explanation,
    to_ai_read,
    to_result,
    to_summary,
)
from app.services.taxonomy import TAXONOMY_VERSION

logger = get_logger(__name__)

#: Upper bound on jobs scanned when a batch request names no jobs.
_BATCH_SCAN = 200


# --- Job intelligence ---------------------------------------------------------------


def ensure_job_intelligence(db: Session, job: Job) -> JobIntelligence:
    """The current fact sheet for the job's current content, extracted and
    stored only when the content (or extractor/taxonomy version) changed.
    Global per job: every user's match reuses it."""
    content = JobContent.from_job(job)
    input_hash = content.input_hash()
    reusable = db.scalar(
        select(JobIntelligence)
        .where(
            JobIntelligence.job_id == job.id,
            JobIntelligence.input_hash == input_hash,
            JobIntelligence.extractor_version == JOB_EXTRACTOR_VERSION,
            JobIntelligence.taxonomy_version == TAXONOMY_VERSION,
        )
        .order_by(JobIntelligence.is_current.desc(), JobIntelligence.created_at.desc())
        .limit(1)
    )
    if reusable is not None:
        _make_current(db, job, reusable)
        return reusable

    extraction = extract(content)
    result_hash = extraction.result_hash()
    same_result = db.scalar(
        select(JobIntelligence).where(
            JobIntelligence.job_id == job.id,
            JobIntelligence.result_hash == result_hash,
            JobIntelligence.extractor_version == JOB_EXTRACTOR_VERSION,
            JobIntelligence.taxonomy_version == TAXONOMY_VERSION,
        )
    )
    if same_result is not None:
        _make_current(db, job, same_result)
        return same_result

    try:
        with db.begin_nested():
            _make_current(db, job, None)
            intelligence = JobIntelligence(
                job_id=job.id,
                extractor_version=JOB_EXTRACTOR_VERSION,
                taxonomy_version=TAXONOMY_VERSION,
                input_hash=input_hash,
                result_hash=result_hash,
                facts=extraction.facts.model_dump(mode="json"),
                is_current=True,
                source_posting_id=job.preferred_source_posting_id,
            )
            intelligence.requirements = [
                JobRequirement(**vars(requirement)) for requirement in extraction.requirements
            ]
            db.add(intelligence)
            db.flush()
        return intelligence
    except IntegrityError:
        # Another request stored the same extraction first.
        existing = db.scalar(
            select(JobIntelligence).where(
                JobIntelligence.job_id == job.id, JobIntelligence.is_current
            )
        )
        if existing is None:
            raise
        return existing


def _make_current(db: Session, job: Job, intelligence: JobIntelligence | None) -> None:
    """Point the job's `is_current` at `intelligence` (or at nothing)."""
    current = db.scalar(
        select(JobIntelligence).where(JobIntelligence.job_id == job.id, JobIntelligence.is_current)
    )
    if current is not None and current is not intelligence:
        current.is_current = False
        db.flush()
    if intelligence is not None and not intelligence.is_current:
        intelligence.is_current = True
        db.flush()


def job_view(db: Session, intelligence: JobIntelligence) -> JobView:
    requirements = db.scalars(
        select(JobRequirement)
        .where(JobRequirement.job_intelligence_id == intelligence.id)
        .order_by(JobRequirement.category, JobRequirement.id)
    ).all()
    return JobView(
        facts=intelligence.read_facts(),
        requirements=tuple(RequirementView.from_row(r) for r in requirements),
    )


# --- Calculation --------------------------------------------------------------------


def calculate(
    db: Session,
    user: User,
    job: Job,
    snapshot: CandidateProfileVersion | None = None,
) -> tuple[JobMatch, bool]:
    """Compute (or reuse) the user's result for the job's current inputs and
    make it current. Returns (match, created). Does not commit."""
    snapshot = snapshot or ensure_snapshot(db, user)
    intelligence = ensure_job_intelligence(db, job)
    current = db.scalar(
        select(JobMatch).where(
            JobMatch.user_id == user.id, JobMatch.job_id == job.id, JobMatch.is_current
        )
    )
    existing = db.scalar(
        select(JobMatch).where(
            JobMatch.user_id == user.id,
            JobMatch.job_id == job.id,
            JobMatch.candidate_profile_version_id == snapshot.id,
            JobMatch.job_intelligence_id == intelligence.id,
            JobMatch.model_id == model.MATCHING_ALGORITHM_VERSION,
        )
    )
    if existing is not None:
        if existing.model_hash != model.MODEL_HASH:
            # The pinned model-hash test makes this unreachable in practice:
            # a changed definition must come with a new version.
            raise RuntimeError("matching model changed without a new MATCHING_ALGORITHM_VERSION")
        if current is not None and current.id != existing.id:
            current.is_current = False
            db.flush()
        if not existing.is_current:
            existing.is_current = True
            db.flush()
        return existing, False

    outcome = evaluate(snapshot.read_document(), job_view(db, intelligence))
    if current is not None:
        current.is_current = False
        db.flush()
    match = JobMatch(
        user_id=user.id,
        job_id=job.id,
        candidate_profile_version_id=snapshot.id,
        job_intelligence_id=intelligence.id,
        model_id=model.MATCHING_ALGORITHM_VERSION,
        model_hash=model.MODEL_HASH,
        score=outcome.overall_score,
        coverage=outcome.coverage,
        verdict=outcome.verdict,
        is_blocked=outcome.is_blocked,
        criteria=outcome.criteria,
        caps=outcome.caps,
        is_current=True,
        job_score=outcome.job_score,
        preference_score=outcome.preference_score,
        job_coverage=outcome.job_coverage,
        preference_coverage=outcome.preference_coverage,
        taxonomy_version=TAXONOMY_VERSION,
        evidence=outcome.evidence.model_dump(mode="json"),
    )
    db.add(match)
    db.flush()
    db.refresh(match)
    return match, True


def match_state(db: Session, user: User, job_id: uuid.UUID) -> MatchStateRead:
    """The user's match state for one of *their* jobs (404 otherwise)."""
    job, user_job = get_visible_job(db, user, job_id)
    match = current_matches(db, user, [job.id]).get(job.id)
    if match is None:
        return MatchStateRead(job_id=job.id, status="not_calculated", result=None)
    reasons = stale_reasons(match, job, build_snapshot(db, user).content_hash)
    return MatchStateRead(
        job_id=job.id,
        status="stale" if reasons else "calculated",
        result=to_result(db, match, user_job, reasons),
    )


def calculate_for_job(db: Session, user: User, job_id: uuid.UUID) -> MatchStateRead:
    job, _ = get_visible_job(db, user, job_id)
    calculate(db, user, job)
    db.commit()
    db.expire_all()
    return match_state(db, user, job_id)


def calculate_batch(db: Session, user: User, request: BatchMatchRequest) -> BatchMatchResponse:
    """Deterministic matching for several of the user's jobs. Never calls
    the AI. With `job_ids`, exactly those (that are visible); without,
    the most recently discovered jobs that lack an up-to-date result, at
    most `limit`."""
    visible = (
        select(Job, UserJob)
        .join(UserJob, and_(UserJob.job_id == Job.id, UserJob.user_id == user.id))
        .options(selectinload(Job.source_postings))
    )
    not_found: list[uuid.UUID] = []
    if request.job_ids:
        requested = list(dict.fromkeys(request.job_ids))
        rows = db.execute(visible.where(Job.id.in_(requested))).all()
        found = {job.id for job, _ in rows}
        not_found = [job_id for job_id in requested if job_id not in found]
        order = {job_id: index for index, job_id in enumerate(requested)}
        candidates = sorted(rows, key=lambda row: order[row[0].id])
        limit = len(candidates)
    else:
        candidates = list(
            db.execute(
                visible.order_by(
                    UserJob.last_discovered_at.desc().nulls_last(), Job.id
                ).limit(_BATCH_SCAN)
            ).all()
        )
        limit = request.limit

    snapshot = ensure_snapshot(db, user)
    existing = current_matches(db, user, [job.id for job, _ in candidates])
    results: list[BatchMatchItem] = []
    calculated = unchanged = 0
    for job, _user_job in candidates:
        if calculated + unchanged >= limit:
            break
        match = existing.get(job.id)
        fresh = match is not None and not stale_reasons(match, job, snapshot.content_hash)
        if fresh and not request.force:
            if request.job_ids:
                assert match is not None
                unchanged += 1
                results.append(
                    BatchMatchItem(job_id=job.id, outcome="unchanged", match=to_summary(match, []))
                )
            continue
        new_match, created = calculate(db, user, job, snapshot)
        if created:
            calculated += 1
        else:
            unchanged += 1
        results.append(
            BatchMatchItem(
                job_id=job.id,
                outcome="calculated" if created else "unchanged",
                match=to_summary(new_match, []),
            )
        )
    db.commit()
    return BatchMatchResponse(
        results=results, calculated=calculated, unchanged=unchanged, not_found=not_found
    )


# --- AI explanation -----------------------------------------------------------------

_AI_ERRORS: tuple[tuple[type[AIError], int, str], ...] = (
    (
        AIProcessingDisabledByUserError,
        409,
        AIProcessingDisabledByUserError.user_message,
    ),
    (
        AINotConfiguredError,
        503,
        "AI explanations are not available because no AI provider is configured. "
        "JobPilot's own explanation below is complete without it.",
    ),
    (AIRateLimitError, 429, AIRateLimitError.user_message),
    (AITimeoutError, 504, AITimeoutError.user_message),
    (AIProviderUnavailableError, 503, AIProviderUnavailableError.user_message),
    (ExplanationRejectedError, 502, ExplanationRejectedError.user_message),
)


def _ai_error(exc: AIError) -> AppError:
    for kind, status_code, message in _AI_ERRORS:
        if isinstance(exc, kind):
            return AppError(exc.code, message, status_code)
    return AppError(
        exc.code,
        "The AI explanation couldn't be generated. JobPilot's own explanation is shown instead.",
        502,
    )


async def explain(
    db: Session, user: User, job_id: uuid.UUID, provider: AIProvider
) -> AIExplanationRead:
    """An AI-written explanation of the user's current, up-to-date result.
    Generated once per result and prompt version, then reused."""
    job, _ = get_visible_job(db, user, job_id)
    match = current_matches(db, user, [job.id]).get(job.id)
    if match is None:
        raise ConflictError("Calculate the match first.")
    if stale_reasons(match, job, build_snapshot(db, user).content_hash):
        raise ConflictError("This match is out of date. Recalculate it first.")
    cached = stored_explanation(db, match)
    if cached is not None:
        return to_ai_read(cached)

    facts = ai_facts(
        job_title=job.title,
        company_name=job.company_name,
        job_match=int(match.job_score) if match.job_score is not None else None,
        preference_match=(
            int(match.preference_score) if match.preference_score is not None else None
        ),
        overall_match=int(match.score) if match.score is not None else None,
        verdict=match.verdict,
        summary=summarize(match.read_evidence()),
    )
    try:
        content = await generate_ai_explanation(provider, facts)
    except AIError as exc:
        # Codes and exception types only: never prompt or profile content.
        logger.warning("Match explanation failed: %s (%s)", exc.code, type(exc).__name__)
        raise _ai_error(exc) from None

    explanation = JobMatchExplanation(
        job_match_id=match.id,
        prompt_version=MATCH_EXPLANATION_PROMPT_VERSION,
        ai_provider=provider.name,
        ai_model=provider.model,
        content=content.model_dump(mode="json"),
    )
    try:
        with db.begin_nested():
            db.add(explanation)
            db.flush()
    except IntegrityError:
        cached = stored_explanation(db, match)
        if cached is None:
            raise
        return to_ai_read(cached)
    db.commit()
    db.refresh(explanation)
    return to_ai_read(explanation)
