"""Application questions and their answers (V0.9): the pipeline and the user's control.

Human Control at Every Stage. Two things never mix:

    the SUGGESTION   what JobPilot proposes (deterministic from the application's
                     own data, or an AI answer that passed truth validation)
    the ANSWER       what the user decided: accepted, edited, or written themselves

    generate / regenerate  -> only ever replaces the *suggestion*
    accept / manual / clear -> only ever change the *answer*, and only when the user asks

A regeneration can never overwrite an answer, and a client can never put
anything into the suggestion: it is built here, from trusted facts.

    generate(question)
        ownership (404 for anything not yours) -> existing-answer confirmation (409)
        -> is the answer already in structured data?   yes: deterministic, no AI call
        -> no: readiness (confirmed resume, language ...) -> deterministic context
        -> AI call through the provider abstraction     --failure-->  FAILED attempt
        -> structured output (schema-validated)
        -> truth validation                              --claims-->  REJECTED attempt
        -> SUGGESTED (or NEEDS_INPUT when the facts are not enough)

Every attempt, including a failed or rejected one, is a write-once row
(`ApplicationQuestionGeneration`); a rejected or failed attempt changes nothing
the user sees except an error. Provider errors, prompts and rejected output
never reach the user.
"""

import asyncio
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.ai.provider import (
    AIAuthenticationError,
    AIContextLimitError,
    AIEmptyResponseError,
    AIError,
    AIInvalidResponseError,
    AINotConfiguredError,
    AIProvider,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.ai.providers.disabled import AIProcessingDisabledByUserError
from app.core.errors import AppError, ConflictError, NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.models.application_question import (
    CHOICE_TYPES,
    AnswerSource,
    ApplicationQuestion,
    ApplicationQuestionAnswer,
    ApplicationQuestionGeneration,
    GenerationMethod,
    GenerationOutcome,
    QuestionType,
)
from app.models.generated_document import GeneratedDocumentType
from app.models.user import User
from app.schemas.application_question import (
    AcceptRequest,
    AnswerProvenance,
    AnswerRead,
    GenerateRequest,
    GenerationListResponse,
    GenerationRead,
    ManualAnswerRequest,
    QuestionAiReadiness,
    QuestionBlocker,
    QuestionCreate,
    QuestionListResponse,
    QuestionRead,
    QuestionUpdate,
    check_type_and_options,
)
from app.schemas.document_content import SourceFact, ValidationReport
from app.schemas.question_ai import AIQuestionAnswer
from app.services.applications.service import get_owned_application
from app.services.documents.context import Preparation, canonical_hash
from app.services.documents.facts import YEARS_FACT_ID
from app.services.documents.service import GenerationInputs, generation_readiness
from app.services.questions import deterministic, prompts, validation
from app.services.questions.classify import classify
from app.services.questions.context import build_question_context
from app.services.questions.deterministic import QuestionShape, Suggested
from app.services.questions.state import derive_status
from app.services.questions.text import normalize_question, question_fingerprint, question_hash

logger = get_logger(__name__)

#: A generous bound that keeps one application's list and its page small.
MAX_QUESTIONS_PER_APPLICATION = 200

GENERATION_FAILED_MESSAGE = "Generating a suggestion failed. You can try again or answer manually."
REJECTED_MESSAGE = (
    "The suggested answer contained claims your resume and application data do not support, "
    "so it was discarded. Your answer was not changed. You can try again or answer manually."
)

_AI_FAILURES: tuple[tuple[type[AIError], int, str], ...] = (
    (
        AIProcessingDisabledByUserError,
        409,
        AIProcessingDisabledByUserError.user_message,
    ),
    (
        AINotConfiguredError,
        503,
        "AI suggestions are not available because no AI provider is configured. "
        "You can still answer manually.",
    ),
    (AIRateLimitError, 429, AIRateLimitError.user_message),
    (AITimeoutError, 504, AITimeoutError.user_message),
    (AIProviderUnavailableError, 503, AIProviderUnavailableError.user_message),
    (
        AIContextLimitError,
        502,
        "Your resume or this job is too long for the AI service to process in one pass.",
    ),
    (
        AIInvalidResponseError,
        502,
        "The AI service returned a result JobPilot couldn't use, so nothing was saved. "
        "Please try again.",
    ),
    (
        AIEmptyResponseError,
        502,
        "The AI service returned an empty result, so nothing was saved. Please try again.",
    ),
    (AIAuthenticationError, 502, GENERATION_FAILED_MESSAGE),
)


def _ai_failure(exc: AIError) -> tuple[int, str]:
    for kind, status_code, message in _AI_FAILURES:
        if isinstance(exc, kind):
            return status_code, message
    return 502, GENERATION_FAILED_MESSAGE


# --- Loading ------------------------------------------------------------------------------------


def _owned_question(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    *,
    lock: bool = False,
) -> ApplicationQuestion:
    """The user's question of the user's application. Anything else -- another
    user's, another application's, one that doesn't exist -- is the same 404."""
    application = get_owned_application(db, user, application_id)
    statement = (
        select(ApplicationQuestion)
        .where(
            ApplicationQuestion.id == question_id,
            ApplicationQuestion.application_id == application.id,
            ApplicationQuestion.user_id == user.id,
        )
        .options(selectinload(ApplicationQuestion.answer))
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(of=ApplicationQuestion)
    question = db.scalar(statement)
    if question is None:
        raise NotFoundError("Question not found.")
    if question.answer is None:  # pragma: no cover - created together, never missing
        question.answer = ApplicationQuestionAnswer(user_id=user.id)
        db.flush()
    return question


def _shape(question: ApplicationQuestion) -> QuestionShape:
    return QuestionShape(
        text=question.question_text,
        question_type=question.question_type,
        max_length=question.max_length,
        options=list(question.options) if question.options is not None else None,
    )


# --- Reading ------------------------------------------------------------------------------------


def _generations_by_id(
    db: Session, ids: set[uuid.UUID]
) -> dict[uuid.UUID, ApplicationQuestionGeneration]:
    if not ids:
        return {}
    rows = db.scalars(
        select(ApplicationQuestionGeneration).where(ApplicationQuestionGeneration.id.in_(ids))
    )
    return {row.id: row for row in rows}


def _answer_read(
    question: ApplicationQuestion,
    answer: ApplicationQuestionAnswer,
    generations: dict[uuid.UUID, ApplicationQuestionGeneration],
) -> AnswerRead:
    provenance = (
        AnswerProvenance.model_validate(answer.ai_provenance) if answer.ai_provenance else None
    )
    cited: dict[str, SourceFact] = {}
    generation = generations.get(answer.ai_generation_id) if answer.ai_generation_id else None
    if generation is not None:
        facts = generation.source_facts or {}
        cited = {
            fact_id: SourceFact.model_validate(facts[fact_id])
            for fact_id in answer.ai_source_fact_ids
            if fact_id in facts
        }
    outdated = (
        provenance is not None
        and answer.has_suggestion
        and provenance.question_fingerprint != question_fingerprint(question)
    )
    return AnswerRead(
        id=answer.id,
        status=answer.status,
        answer_source=answer.answer_source,
        ai_suggestion=answer.ai_suggestion,
        ai_selected_options=answer.ai_selected_options,
        ai_source_fact_ids=list(answer.ai_source_fact_ids or []),
        ai_provenance=provenance,
        ai_generation_id=answer.ai_generation_id,
        suggestion_method=provenance.method if provenance else None,
        suggestion_dismissed=answer.suggestion_dismissed,
        suggestion_outdated=bool(outdated),
        source_facts=cited,
        needs_input_reason=answer.needs_input_reason,
        needs_input_message=deterministic.needs_input_message(answer.needs_input_reason),
        final_answer=answer.final_answer,
        final_selected_options=answer.final_selected_options,
        basis_generation_id=answer.basis_generation_id,
        created_at=answer.created_at,
        updated_at=answer.updated_at,
    )


def _reads(db: Session, questions: list[ApplicationQuestion]) -> list[QuestionRead]:
    generations = _generations_by_id(
        db,
        {
            q.answer.ai_generation_id
            for q in questions
            if q.answer is not None and q.answer.ai_generation_id is not None
        },
    )
    reads: list[QuestionRead] = []
    for question in questions:
        assert question.answer is not None
        reads.append(
            QuestionRead(
                id=question.id,
                application_id=question.application_id,
                question_text=question.question_text,
                normalized_question_text=question.normalized_question_text,
                question_hash=question.question_hash,
                question_type=question.question_type,
                required=question.required,
                max_length=question.max_length,
                options=question.options,
                external_key=question.external_key,
                field_name=question.field_name,
                source=question.source,
                source_metadata=question.source_metadata,
                sort_order=question.sort_order,
                answer=_answer_read(question, question.answer, generations),
                created_at=question.created_at,
                updated_at=question.updated_at,
            )
        )
    return reads


def _read(db: Session, question: ApplicationQuestion) -> QuestionRead:
    return _reads(db, [question])[0]


def _ai_readiness(db: Session, user: User, application_id: uuid.UUID) -> QuestionAiReadiness:
    application = get_owned_application(db, user, application_id)
    state = generation_readiness(
        GenerationInputs(db, user, application), GeneratedDocumentType.TAILORED_CV
    )
    return QuestionAiReadiness(
        can_generate=state.can_generate,
        blockers=[QuestionBlocker(code=b.code, message=b.message) for b in state.blockers],
    )


def list_questions(db: Session, user: User, application_id: uuid.UUID) -> QuestionListResponse:
    application = get_owned_application(db, user, application_id)
    questions = list(
        db.scalars(
            select(ApplicationQuestion)
            .where(
                ApplicationQuestion.application_id == application.id,
                ApplicationQuestion.user_id == user.id,
            )
            .options(selectinload(ApplicationQuestion.answer))
            .order_by(ApplicationQuestion.sort_order, ApplicationQuestion.created_at)
            .execution_options(populate_existing=True)
        )
    )
    return QuestionListResponse(
        items=_reads(db, questions), ai=_ai_readiness(db, user, application_id)
    )


def get_question(
    db: Session, user: User, application_id: uuid.UUID, question_id: uuid.UUID
) -> QuestionRead:
    return _read(db, _owned_question(db, user, application_id, question_id))


def list_generations(
    db: Session, user: User, application_id: uuid.UUID, question_id: uuid.UUID
) -> GenerationListResponse:
    """Every attempt for the question, newest first -- also rejected and failed."""
    question = _owned_question(db, user, application_id, question_id)
    rows = db.scalars(
        select(ApplicationQuestionGeneration)
        .where(
            ApplicationQuestionGeneration.question_id == question.id,
            ApplicationQuestionGeneration.user_id == user.id,
        )
        .order_by(ApplicationQuestionGeneration.created_at.desc(), ApplicationQuestionGeneration.id)
    )
    return GenerationListResponse(
        items=[
            GenerationRead(
                id=row.id,
                question_id=row.question_id,
                method=row.method,
                outcome=row.outcome,
                needs_input_reason=row.needs_input_reason,
                suggestion=row.suggestion,
                selected_options=row.selected_options,
                source_fact_ids=list(row.source_fact_ids or []),
                source_facts={
                    fact_id: SourceFact.model_validate(fact)
                    for fact_id, fact in (row.source_facts or {}).items()
                },
                confidence=row.confidence,
                validation=ValidationReport.model_validate(row.validation)
                if row.validation
                else None,
                error_code=row.error_code,
                error_message=row.error_message,
                question_fingerprint=row.question_fingerprint,
                input_hash=row.input_hash,
                max_length=row.max_length,
                prompt_version=row.prompt_version,
                prompt_hash=row.prompt_hash,
                ai_provider=row.ai_provider,
                ai_model=row.ai_model,
                resume_id=row.resume_id,
                resume_version_number=row.resume_version_number,
                application_context_snapshot_id=row.application_context_snapshot_id,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


# --- Creating, editing, deleting questions ------------------------------------------------------


def create_question(
    db: Session, user: User, application_id: uuid.UUID, request: QuestionCreate
) -> QuestionRead:
    application = get_owned_application(db, user, application_id)
    count, newest = db.execute(
        select(func.count(), func.max(ApplicationQuestion.sort_order)).where(
            ApplicationQuestion.application_id == application.id
        )
    ).one()
    if count >= MAX_QUESTIONS_PER_APPLICATION:
        raise ConflictError(
            f"An application can have at most {MAX_QUESTIONS_PER_APPLICATION} questions."
        )
    question = ApplicationQuestion(
        application_id=application.id,
        user_id=user.id,
        question_text=request.question_text,
        normalized_question_text=normalize_question(request.question_text),
        question_hash=question_hash(request.question_text),
        question_type=request.question_type,
        required=request.required,
        max_length=request.max_length,
        options=request.options,
        external_key=request.external_key,
        field_name=request.field_name,
        source=request.source,
        source_metadata=request.source_metadata,
        sort_order=(newest + 1) if newest is not None else 0,
    )
    question.answer = ApplicationQuestionAnswer(user_id=user.id)
    db.add(question)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(
            "QUESTION_EXISTS",
            "A question with this external key already exists for this application.",
            409,
        ) from None
    return _read(db, _owned_question(db, user, application_id, question.id))


def update_question(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    request: QuestionUpdate,
) -> QuestionRead:
    question = _owned_question(db, user, application_id, question_id, lock=True)
    changes = request.model_dump(exclude_unset=True)
    for name, value in changes.items():
        setattr(question, name, value)
    if "question_text" in changes:
        question.normalized_question_text = normalize_question(question.question_text)
        question.question_hash = question_hash(question.question_text)
    try:
        check_type_and_options(question.question_type, question.options)
    except ValueError as exc:
        db.rollback()
        raise ValidationAppError(str(exc)) from None

    # The user's answer is never silently changed or cut to fit a new limit or
    # option list: a change it would no longer satisfy is refused.
    answer = question.answer
    assert answer is not None
    if answer.has_final:
        problem = check_user_answer(question, answer.final_answer, answer.final_selected_options)
        if problem is not None:
            db.rollback()
            raise AppError(
                "ANSWER_CONFLICT",
                "Your current answer would no longer fit this question. Edit or clear your "
                f"answer first. ({problem.message})",
                409,
            )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(
            "QUESTION_EXISTS",
            "A question with this external key already exists for this application.",
            409,
        ) from None
    return _read(db, _owned_question(db, user, application_id, question_id))


def delete_question(
    db: Session, user: User, application_id: uuid.UUID, question_id: uuid.UUID
) -> None:
    question = _owned_question(db, user, application_id, question_id)
    db.delete(question)
    db.commit()


# --- What a user may answer ---------------------------------------------------------------------

_YES_NO_WORDS = ("Yes", "No", "Ja", "Nein")


class AnswerProblem(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _canonical(value: str, options: list[str]) -> str | None:
    wanted = " ".join(value.split()).casefold()
    for option in options:
        if " ".join(option.split()).casefold() == wanted:
            return option
    return None


def check_user_answer(
    question: ApplicationQuestion, text: str | None, options: list[str] | None
) -> AnswerProblem | None:
    """Whether an answer satisfies the question: its limit (never truncated),
    its options (never invented) and its number format. None when it does."""
    qtype = question.question_type
    if qtype in CHOICE_TYPES:
        if text is not None or not options:
            return AnswerProblem("INVALID_OPTION", "Choose from the listed options.")
        listed = question.options or []
        if any(_canonical(option, listed) != option for option in options):
            return AnswerProblem("INVALID_OPTION", "Choose only options the question lists.")
        if len(set(options)) != len(options):
            return AnswerProblem("INVALID_OPTION", "An option is selected twice.")
        if qtype is QuestionType.SINGLE_CHOICE and len(options) != 1:
            return AnswerProblem("INVALID_OPTION", "Choose exactly one option.")
        return None
    if options is not None or text is None:
        return AnswerProblem("INVALID_ANSWER", "This question takes a text answer.")
    if question.max_length is not None and len(text) > question.max_length:
        return AnswerProblem(
            "ANSWER_TOO_LONG",
            f"The answer has {len(text)} characters; this question allows "
            f"{question.max_length}.",
            {"length": len(text), "max_length": question.max_length},
        )
    numeric = qtype in (QuestionType.NUMERIC, QuestionType.SALARY)
    if numeric and not validation.NUMBER_FORMAT.match(text):
        return AnswerProblem("INVALID_NUMBER", "This question takes a number.")
    if qtype is QuestionType.YES_NO:
        allowed = question.options or list(_YES_NO_WORDS)
        if _canonical(text, allowed) != text:
            return AnswerProblem("INVALID_OPTION", "Answer with one of: " + ", ".join(allowed))
    return None


def _canonicalize_user_answer(
    question: ApplicationQuestion, text: str | None, options: list[str] | None
) -> tuple[str | None, list[str] | None]:
    """The user's input in its stored form: a choice is stored as the option as
    listed (so a different case is not a different answer); text only loses the
    whitespace around it. Nothing else is touched: never truncated, never
    rewritten."""
    if options is not None:
        listed = question.options or []
        return None, [_canonical(option, listed) or option for option in options]
    assert text is not None
    value = text.strip()
    if question.question_type is QuestionType.YES_NO:
        allowed = question.options or list(_YES_NO_WORDS)
        return _canonical(value, allowed) or value, None
    return value, None


# --- Recording an attempt -----------------------------------------------------------------------


def _provenance(row: ApplicationQuestionGeneration) -> dict[str, Any]:
    if row.validation is None:
        status = "not_applicable" if row.method is GenerationMethod.DETERMINISTIC else "not_run"
    else:
        status = ValidationReport.model_validate(row.validation).status
    return AnswerProvenance(
        generation_id=row.id,
        method=row.method,
        prompt_version=row.prompt_version,
        prompt_hash=row.prompt_hash,
        ai_provider=row.ai_provider,
        ai_model=row.ai_model,
        resume_id=row.resume_id,
        resume_version_number=row.resume_version_number,
        resume_profile_id=row.resume_profile_id,
        application_context_snapshot_id=row.application_context_snapshot_id,
        input_hash=row.input_hash,
        question_fingerprint=row.question_fingerprint,
        confidence=row.confidence,
        max_length=row.max_length,
        generated_at=row.created_at,
        validation_status=status,  # type: ignore[arg-type]
    ).model_dump(mode="json")


def _record(
    db: Session,
    user_id: uuid.UUID,
    question_id: uuid.UUID,
    fields: dict[str, Any],
) -> ApplicationQuestionGeneration:
    """Write the attempt, and apply it to the answer's *suggestion* when it is
    one. Locks the question so concurrent attempts apply one after another.
    Commits. Never touches the user's final answer."""
    db.rollback()
    db.execute(
        select(ApplicationQuestion.id)
        .where(ApplicationQuestion.id == question_id)
        .with_for_update()
    )
    row = ApplicationQuestionGeneration(user_id=user_id, question_id=question_id, **fields)
    db.add(row)
    db.flush()
    db.refresh(row)
    answer = db.scalar(
        select(ApplicationQuestionAnswer)
        .where(ApplicationQuestionAnswer.question_id == question_id)
        .execution_options(populate_existing=True)
    )
    assert answer is not None
    if row.outcome is GenerationOutcome.SUGGESTED:
        answer.ai_suggestion = row.suggestion
        answer.ai_selected_options = row.selected_options
        answer.ai_source_fact_ids = list(row.source_fact_ids)
        answer.ai_provenance = _provenance(row)
        answer.ai_generation_id = row.id
        answer.suggestion_dismissed = False
        answer.needs_input_reason = None
    elif row.outcome is GenerationOutcome.NEEDS_INPUT:
        answer.ai_suggestion = None
        answer.ai_selected_options = None
        answer.ai_source_fact_ids = []
        answer.ai_provenance = _provenance(row)
        answer.ai_generation_id = row.id
        answer.suggestion_dismissed = False
        answer.needs_input_reason = row.needs_input_reason
    answer.status = derive_status(answer)
    db.commit()
    return row


def _facts_of(ids: list[str], catalog: Any) -> dict[str, dict[str, Any]]:
    """The cited facts (text and origin), so each id stays resolvable later."""
    found: dict[str, dict[str, Any]] = {}
    for fact_id in ids:
        fact = catalog.get(fact_id)
        if fact is not None:
            found[fact_id] = SourceFact(text=fact.text, ref=fact.ref).model_dump(mode="json")
    return found


# --- Generating ---------------------------------------------------------------------------------


async def generate_answer(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    provider: AIProvider,
    request: GenerateRequest | None,
    *,
    regenerate: bool,
) -> QuestionRead:
    """Suggest an answer (deterministically when the application's data
    already states it, else with the AI). Only the suggestion changes."""
    acknowledged = bool(request and request.acknowledge_existing_answer)
    user_id = user.id
    question = _owned_question(db, user, application_id, question_id)
    answer = question.answer
    assert answer is not None
    if not regenerate and answer.ai_generation_id is not None:
        raise AppError(
            "ALREADY_GENERATED",
            "This question already has a suggestion. Use regenerate to get a new one.",
            409,
        )
    if answer.has_final and not acknowledged:
        raise AppError(
            "EXISTING_ANSWER_CONFIRMATION_REQUIRED",
            "You already have an answer to this question. A new suggestion will not change it; "
            "confirm that you want one anyway.",
            409,
            {"status": answer.status.value},
        )

    application = get_owned_application(db, user, application_id)
    inputs = GenerationInputs(db, user, application)
    shape = _shape(question)
    required = question.required
    fingerprint = question_fingerprint(question)
    account_name = (user.first_name, user.last_name)
    intent = classify(question.question_text, question.question_type)

    # --- Deterministic: the answer is already in structured data -----------------------------
    if intent is not None:
        data = deterministic.known_data(
            inputs.preparation,
            profile_id=inputs.profile.id if inputs.profile is not None else None,
            profile_data=inputs.profile_data,
        )
        resolution = deterministic.resolve(intent, shape, data)
        fact_ids = resolution.fact_ids if isinstance(resolution, Suggested) else []
        uses_resume = any(f.startswith("contact.") or f == YEARS_FACT_ID for f in fact_ids)
        fields: dict[str, Any] = {
            "method": GenerationMethod.DETERMINISTIC,
            "question_fingerprint": fingerprint,
            "input_hash": canonical_hash(
                {
                    "intent": intent.value,
                    "question": fingerprint,
                    "facts": {fact.id: fact.text for fact in data.facts},
                }
            ),
            "max_length": shape.max_length,
            "source_fact_ids": fact_ids,
            "source_facts": _facts_of(fact_ids, data.facts),
            "resume_id": inputs.resume.id if uses_resume and inputs.resume else None,
            "resume_profile_id": inputs.profile.id if uses_resume and inputs.profile else None,
            "resume_version_number": (
                inputs.resume.version_number if uses_resume and inputs.resume else None
            ),
            "application_context_snapshot_id": inputs.snapshot_id,
        }
        if isinstance(resolution, Suggested):
            fields.update(
                outcome=GenerationOutcome.SUGGESTED,
                suggestion=resolution.text,
                selected_options=resolution.options,
                confidence="HIGH",
            )
        else:
            fields.update(
                outcome=GenerationOutcome.NEEDS_INPUT,
                needs_input_reason=resolution.reason,
            )
        _record(db, user_id, question_id, fields)
        return get_question(db, user, application_id, question_id)

    # --- AI: ready? ----------------------------------------------------------------------------
    state = generation_readiness(inputs, GeneratedDocumentType.TAILORED_CV)
    if not state.can_generate:
        first = state.blockers[0]
        raise AppError(
            first.code,
            first.message,
            409,
            {"blockers": [blocker.model_dump() for blocker in state.blockers]},
        )
    assert inputs.profile is not None and inputs.profile_data is not None
    assert inputs.resume is not None and inputs.context_document is not None
    assert inputs.preparation.language is not None
    context = build_question_context(
        question=shape,
        required=required,
        language=inputs.preparation.language,
        resume_profile_id=inputs.profile.id,
        profile_data=inputs.profile_data,
        account_name=account_name,
        job=inputs.context_document.job,
        preparation=_without_salary(inputs.preparation),
    )
    base: dict[str, Any] = {
        "method": GenerationMethod.AI,
        "question_fingerprint": fingerprint,
        "input_hash": context.input_hash,
        "max_length": shape.max_length,
        "prompt_version": prompts.ANSWER_PROMPT_VERSION,
        "prompt_hash": prompts.current_prompt_hash(),
        "ai_provider": provider.name,
        "ai_model": provider.model,
        "resume_id": inputs.resume.id,
        "resume_profile_id": inputs.profile.id,
        "resume_version_number": inputs.resume.version_number,
        "application_context_snapshot_id": inputs.snapshot_id,
        "source_facts": context.generation.catalog.as_source_facts(),
    }
    system = prompts.system_prompt()
    prompt = prompts.user_prompt(context)
    # Plain values from here on: the session must not hold a transaction open
    # (or reload expired rows) while waiting for the AI.
    db.commit()

    def fail(code: str, message: str, status_code: int) -> AppError:
        row = _record(
            db,
            user_id,
            question_id,
            {
                **base,
                "outcome": GenerationOutcome.FAILED,
                "error_code": code,
                "error_message": message,
            },
        )
        return AppError(code, message, status_code, {"generation_id": str(row.id)})

    try:
        output = await provider.generate_structured(
            system=system, prompt=prompt, schema=AIQuestionAnswer
        )
    except AIError as exc:
        # Codes and exception types only: never prompts, facts or provider output.
        logger.warning("Answer generation failed: %s (%s)", exc.code, type(exc).__name__)
        status_code, message = _ai_failure(exc)
        raise fail(exc.code, message, status_code) from None
    except asyncio.CancelledError:
        _record(
            db,
            user_id,
            question_id,
            {
                **base,
                "outcome": GenerationOutcome.FAILED,
                "error_code": "GENERATION_CANCELLED",
                "error_message": GENERATION_FAILED_MESSAGE,
            },
        )
        raise
    except Exception as exc:
        logger.warning("Answer generation failed unexpectedly (%s)", type(exc).__name__)
        raise fail("GENERATION_ERROR", GENERATION_FAILED_MESSAGE, 500) from None

    try:
        result = validation.validate_answer(output, context)
        if result.needs_input:
            _record(
                db,
                user_id,
                question_id,
                {
                    **base,
                    "outcome": GenerationOutcome.NEEDS_INPUT,
                    "needs_input_reason": "FACTS_INSUFFICIENT",
                    "validation": result.report.model_dump(mode="json"),
                    "confidence": output.confidence,
                },
            )
        elif not result.passed:
            logger.warning(
                "Generated answer rejected: %s",
                ", ".join(sorted({v.code for v in result.report.violations})),
            )
            row = _record(
                db,
                user_id,
                question_id,
                {
                    **base,
                    "outcome": GenerationOutcome.REJECTED,
                    "error_code": "ANSWER_REJECTED",
                    "error_message": REJECTED_MESSAGE,
                    "validation": result.report.model_dump(mode="json"),
                    "confidence": output.confidence,
                },
            )
            raise AppError(
                "ANSWER_REJECTED",
                REJECTED_MESSAGE,
                502,
                {
                    "generation_id": str(row.id),
                    "violations": [v.model_dump() for v in result.report.violations],
                },
            )
        else:
            cited = list(dict.fromkeys(output.source_fact_ids))
            _record(
                db,
                user_id,
                question_id,
                {
                    **base,
                    "outcome": GenerationOutcome.SUGGESTED,
                    "suggestion": result.text,
                    "selected_options": result.options,
                    "source_fact_ids": cited,
                    "source_facts": _facts_of(cited, context.generation.catalog),
                    "validation": result.report.model_dump(mode="json"),
                    "confidence": output.confidence,
                },
            )
    except AppError:
        raise
    except Exception as exc:
        logger.warning("Answer finalization failed unexpectedly (%s)", type(exc).__name__)
        raise fail("GENERATION_ERROR", GENERATION_FAILED_MESSAGE, 500) from None
    return get_question(db, user, application_id, question_id)


def _without_salary(preparation: Preparation) -> Preparation:
    """The AI path never sees the salary: it is answered only deterministically
    (the context does not include it either; this keeps that true by construction)."""
    return Preparation(
        language=preparation.language,
        available_from=preparation.available_from,
        notice_period=preparation.notice_period,
        work_authorization=preparation.work_authorization,
    )


# --- The user's decisions -----------------------------------------------------------------------


def _same(
    a_text: str | None, a_options: list[str] | None, b_text: str | None, b_options: list[str] | None
) -> bool:
    return a_text == b_text and (a_options or None) == (b_options or None)


def accept_suggestion(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    request: AcceptRequest | None,
) -> QuestionRead:
    """Make the current suggestion the user's answer, unchanged. The client
    never sends the content: it copies the server-held, validated suggestion."""
    replace = bool(request and request.replace_existing)
    question = _owned_question(db, user, application_id, question_id, lock=True)
    answer = question.answer
    assert answer is not None
    if not answer.has_suggestion:
        raise AppError("NO_SUGGESTION", "There is no suggestion to accept.", 409)
    problem = check_user_answer(question, answer.ai_suggestion, answer.ai_selected_options)
    if problem is not None:
        raise AppError(
            "SUGGESTION_OUTDATED",
            "This suggestion no longer fits the question (it changed). Generate a new "
            f"suggestion. ({problem.message})",
            409,
        )
    method = _suggestion_method(answer)
    if method is GenerationMethod.DETERMINISTIC:
        _require_deterministic_current(db, user, question)

    if answer.has_final:
        if _same(
            answer.final_answer,
            answer.final_selected_options,
            answer.ai_suggestion,
            answer.ai_selected_options,
        ):
            return _read(db, question)
        if not replace:
            raise AppError(
                "ANSWER_WOULD_BE_REPLACED",
                "You already have a different answer. Accepting this suggestion would replace "
                "it; confirm to do that.",
                409,
            )
    answer.final_answer = answer.ai_suggestion
    answer.final_selected_options = answer.ai_selected_options
    answer.answer_source = (
        AnswerSource.DETERMINISTIC if method is GenerationMethod.DETERMINISTIC else AnswerSource.AI
    )
    answer.basis_generation_id = answer.ai_generation_id
    answer.suggestion_dismissed = False
    answer.status = derive_status(answer)
    db.commit()
    return _read(db, _owned_question(db, user, application_id, question_id))


def _suggestion_method(answer: ApplicationQuestionAnswer) -> GenerationMethod:
    provenance = answer.ai_provenance or {}
    return GenerationMethod(provenance.get("method", GenerationMethod.AI.value))


def _require_deterministic_current(db: Session, user: User, question: ApplicationQuestion) -> None:
    """A deterministic suggestion is a copy of saved data: accepting one made from
    data that has since changed would put a stale value into the answer."""
    answer = question.answer
    assert answer is not None
    application = get_owned_application(db, user, question.application_id)
    inputs = GenerationInputs(db, user, application)
    intent = classify(question.question_text, question.question_type)
    stale = AppError(
        "SUGGESTION_OUTDATED",
        "Your saved application data changed since this suggestion was made. Generate a "
        "new suggestion.",
        409,
    )
    if intent is None:
        raise stale
    data = deterministic.known_data(
        inputs.preparation,
        profile_id=inputs.profile.id if inputs.profile is not None else None,
        profile_data=inputs.profile_data,
    )
    resolution = deterministic.resolve(intent, _shape(question), data)
    if not isinstance(resolution, Suggested) or not _same(
        resolution.text, resolution.options, answer.ai_suggestion, answer.ai_selected_options
    ):
        raise stale


def write_answer(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    question_id: uuid.UUID,
    request: ManualAnswerRequest,
) -> QuestionRead:
    """The user's own answer (typed from scratch, or an edit of the suggestion).
    Authoritative: nothing the AI does changes it afterwards."""
    question = _owned_question(db, user, application_id, question_id, lock=True)
    answer = question.answer
    assert answer is not None
    text, options = _canonicalize_user_answer(
        question,
        request.answer if request.answer is not None else None,
        request.selected_options,
    )
    problem = check_user_answer(question, text, options)
    if problem is not None:
        raise AppError(problem.code, problem.message, 422, problem.details)

    has_suggestion = answer.has_suggestion
    if has_suggestion and _same(text, options, answer.ai_suggestion, answer.ai_selected_options):
        # Typing out the suggestion unchanged is accepting it.
        method = _suggestion_method(answer)
        source = (
            AnswerSource.DETERMINISTIC
            if method is GenerationMethod.DETERMINISTIC
            else AnswerSource.AI
        )
        basis: uuid.UUID | None = answer.ai_generation_id
    elif request.based_on_suggestion and has_suggestion:
        # An edit of the current suggestion.
        source, basis = AnswerSource.HYBRID, answer.ai_generation_id
    elif answer.has_final and answer.basis_generation_id is not None:
        # An edit of an answer that itself came from a suggestion: it stays
        # traceable to the attempt it started from.
        source, basis = AnswerSource.HYBRID, answer.basis_generation_id
    else:
        source, basis = AnswerSource.USER, None
    answer.final_answer = text
    answer.final_selected_options = options
    answer.answer_source = source
    answer.basis_generation_id = basis
    answer.status = derive_status(answer)
    db.commit()
    return _read(db, _owned_question(db, user, application_id, question_id))


def reject_suggestion(
    db: Session, user: User, application_id: uuid.UUID, question_id: uuid.UUID
) -> QuestionRead:
    """Turn the current suggestion down. The user's answer, if any, stays."""
    question = _owned_question(db, user, application_id, question_id, lock=True)
    answer = question.answer
    assert answer is not None
    if not answer.has_suggestion:
        raise AppError("NO_SUGGESTION", "There is no suggestion to reject.", 409)
    answer.suggestion_dismissed = True
    answer.status = derive_status(answer)
    db.commit()
    return _read(db, _owned_question(db, user, application_id, question_id))


def clear_answer(
    db: Session, user: User, application_id: uuid.UUID, question_id: uuid.UUID
) -> QuestionRead:
    """Remove the user's answer (only they can). The suggestion stays."""
    question = _owned_question(db, user, application_id, question_id, lock=True)
    answer = question.answer
    assert answer is not None
    answer.final_answer = None
    answer.final_selected_options = None
    answer.answer_source = None
    answer.basis_generation_id = None
    answer.status = derive_status(answer)
    db.commit()
    return _read(db, _owned_question(db, user, application_id, question_id))
