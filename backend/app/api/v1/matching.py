"""Job Matching & Explainability endpoints (V0.6).

Every endpoint works on the authenticated user's own jobs only (their
`UserJob` rows): another user's job -- or their match result -- is a 404,
exactly like a job that doesn't exist.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.ai.provider import AIProvider
from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.matching import (
    AIExplanationRead,
    BatchMatchRequest,
    BatchMatchResponse,
    MatchStateRead,
)
from app.services.matching import service

router = APIRouter(prefix="/jobs", tags=["matching"])


@router.post("/match", response_model=BatchMatchResponse)
def match_jobs(
    request: BatchMatchRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BatchMatchResponse:
    """Calculate deterministic matches for several of your jobs (at most
    25 per request). Never calls the AI."""
    return service.calculate_batch(db, current_user, request or BatchMatchRequest())


@router.get("/{job_id}/match", response_model=MatchStateRead)
def get_match(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MatchStateRead:
    """Your match for the job: `not_calculated`, `calculated`, or `stale`
    (calculated from inputs that have since changed). Never recalculates."""
    return service.match_state(db, current_user, job_id)


@router.post("/{job_id}/match", response_model=MatchStateRead)
def calculate_match(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MatchStateRead:
    """Calculate (or recalculate) your match for the job from your current
    profile, preferences and confirmed resume. Deterministic; no AI."""
    return service.calculate_for_job(db, current_user, job_id)


@router.post("/{job_id}/match/explanation", response_model=AIExplanationRead)
async def explain_match(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> AIExplanationRead:
    """An AI-written explanation of your current match, validated against
    the deterministic result before it is stored. Generated once, then
    reused. It never changes the score."""
    return await service.explain(db, current_user, job_id, provider)
