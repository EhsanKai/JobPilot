"""Application question endpoints (V0.9).

Questions belong to an application and therefore to the user who owns its
`UserJob`: another user's application or question -- or one that doesn't exist
-- is a 404. Nothing a client sends can set ownership, a suggestion, a
validation result or provenance (request bodies reject unknown keys).

Human Control at Every Stage: `generate` / `regenerate` only ever produce a
*suggestion* and never change the user's answer; `accept`, `manual`, `reject`
and `clear` are the user's decisions, and `manual` lets the user answer
without any AI at all.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.ai.provider import AIProvider
from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.application_question import (
    AcceptRequest,
    GenerateRequest,
    GenerationListResponse,
    ManualAnswerRequest,
    QuestionCreate,
    QuestionListResponse,
    QuestionRead,
    QuestionUpdate,
)
from app.services.questions import service

router = APIRouter(prefix="/applications/{application_id}/questions", tags=["questions"])


@router.post("", response_model=QuestionRead, status_code=status.HTTP_201_CREATED)
def create_question(
    application_id: uuid.UUID,
    request: QuestionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionRead:
    """Add a question the employer asks. V0.9 questions are entered by the user;
    the model is source-neutral so V0.10 can add discovered ones."""
    return service.create_question(db, current_user, application_id, request)


@router.get("", response_model=QuestionListResponse)
def list_questions(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionListResponse:
    """The application's questions with their answers, and whether an AI
    suggestion can be generated now (answering manually never needs that)."""
    return service.list_questions(db, current_user, application_id)


@router.get("/{question_id}", response_model=QuestionRead)
def get_question(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionRead:
    return service.get_question(db, current_user, application_id, question_id)


@router.patch("/{question_id}", response_model=QuestionRead)
def update_question(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    request: QuestionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionRead:
    """Edit the question itself. A change the user's answer would no longer
    satisfy (a lower limit, a removed option) is refused with 409."""
    return service.update_question(db, current_user, application_id, question_id, request)


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    service.delete_question(db, current_user, application_id, question_id)


@router.get("/{question_id}/generations", response_model=GenerationListResponse)
def list_generations(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GenerationListResponse:
    """Every attempt to suggest an answer, newest first: what was suggested,
    from which facts, how it was validated -- also rejected and failed ones."""
    return service.list_generations(db, current_user, application_id, question_id)


@router.post("/{question_id}/generate", response_model=QuestionRead)
async def generate_answer(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    request: GenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> QuestionRead:
    """Suggest a first answer: from the application's own data when it states
    it (no AI), else with the AI, grounded in your confirmed resume. Your
    answer is never changed."""
    return await service.generate_answer(
        db, current_user, application_id, question_id, provider, request, regenerate=False
    )


@router.post("/{question_id}/regenerate", response_model=QuestionRead)
async def regenerate_answer(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    request: GenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> QuestionRead:
    """A new suggestion. The earlier attempt stays in the history; your
    answer, if you have one, is not touched (and the request must
    acknowledge that you have one)."""
    return await service.generate_answer(
        db, current_user, application_id, question_id, provider, request, regenerate=True
    )


@router.post("/{question_id}/accept", response_model=QuestionRead)
def accept_suggestion(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    request: AcceptRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionRead:
    """Make the current suggestion your answer, unchanged. The content is
    the server-held, validated suggestion: a client cannot supply it."""
    return service.accept_suggestion(db, current_user, application_id, question_id, request)


@router.post("/{question_id}/reject", response_model=QuestionRead)
def reject_suggestion(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionRead:
    """Turn the current suggestion down. Your answer, if any, stays."""
    return service.reject_suggestion(db, current_user, application_id, question_id)


@router.post("/{question_id}/manual", response_model=QuestionRead)
def write_answer(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    request: ManualAnswerRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionRead:
    """Your own answer, with or without any AI involved. Authoritative: it is
    stored exactly as you wrote it (never truncated; over the limit is
    refused) and nothing the AI does changes it."""
    return service.write_answer(db, current_user, application_id, question_id, request)


@router.post("/{question_id}/clear", response_model=QuestionRead)
def clear_answer(
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionRead:
    """Remove your answer. The suggestion and the history stay."""
    return service.clear_answer(db, current_user, application_id, question_id)
