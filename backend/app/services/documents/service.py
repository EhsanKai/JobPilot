"""Generating and reading tailored documents (V0.8): the pipeline.

    generate(application, type)
        ownership (404 for anything not yours) -> readiness (409 + what to do)
        -> deterministic generation context (facts, job snapshot, application data)
        -> a GENERATING row (the database allows one in flight per application and type)
        -> AI call through the provider abstraction  --failure-->  FAILED
        -> structured output (schema-validated by the provider)
        -> truth validation                          --claims-->  REJECTED
        -> assembly (AI text + facts copied from the resume)
        -> VALIDATED: next version number; becomes the current document

Only the last step produces a document. Every earlier exit leaves a terminal
FAILED / REJECTED attempt (so the UI is never stuck "Generating", and the
history shows what happened) and an application-level error: provider
errors, prompts and unvalidated output never reach the user.

Regeneration is the same call: it creates a new row and a new version and
never touches an earlier one. CV and letter are separate types with separate
versions; generating one never generates the other.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt, prompt_hash
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
from app.core.config import settings
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.models.application import Application
from app.models.generated_document import (
    GeneratedDocument,
    GeneratedDocumentOrigin,
    GeneratedDocumentStatus,
    GeneratedDocumentType,
)
from app.models.job import Job
from app.models.resume import Resume
from app.models.resume_profile import ResumeProfile, ResumeProfileStatus
from app.models.user import User
from app.schemas.application_context import ApplicationContextDocument
from app.schemas.document_ai import AICoverLetter, AIHumanizedLetter, AITailoredCV
from app.schemas.document_content import (
    CoverLetterContent,
    SourceFact,
    TailoredCvContent,
    ValidationReport,
)
from app.schemas.generated_documents import (
    CvEditRequest,
    DocumentListResponse,
    DocumentReadiness,
    GeneratedDocumentRead,
    GeneratedDocumentSummary,
    GenerateDocumentRequest,
    HumanizeRequest,
    LetterEditRequest,
)
from app.schemas.resume_profile import ResumeProfileData
from app.services.applications.service import get_owned_application
from app.services.documents import assembly, editing, photos, validation
from app.services.documents import humanize as humanize_logic
from app.services.documents.context import (
    GenerationContext,
    MatchGuidance,
    Preparation,
    build_generation_context,
)
from app.services.documents.facts import build_candidate_facts
from app.services.documents.prompts import (
    HUMANIZE_PROMPT_VERSION,
    PROMPT_VERSIONS,
    system_prompt,
    user_prompt,
)
from app.services.documents.readiness import ReadinessInputs, readiness
from app.services.matching import status as match_status
from app.services.matching.candidate import build_snapshot
from app.services.matching.explanation import summarize

logger = get_logger(__name__)

GENERATION_FAILED_MESSAGE = "Generation failed. Please try again."
ABANDONED_MESSAGE = "This generation did not finish. Please try again."

#: AI failure -> (HTTP status, user-safe message). Provider errors and their
#: technical reasons are logged by code and type only and never returned.
_AI_FAILURES: tuple[tuple[type[AIError], int, str], ...] = (
    (
        AINotConfiguredError,
        503,
        "Document generation is not available because no AI provider is configured. "
        "Please contact the administrator.",
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

_REJECTED_MESSAGE = (
    "The generated document contained claims your resume does not support, so it was "
    "discarded and nothing was saved as a document. Please try again."
)


def _ai_failure(exc: AIError) -> tuple[int, str]:
    for kind, status_code, message in _AI_FAILURES:
        if isinstance(exc, kind):
            return status_code, message
    return 502, GENERATION_FAILED_MESSAGE


# --- Loading what a generation depends on -------------------------------------------------------


class _Inputs:
    """The application's saved data, loaded once per request."""

    def __init__(self, db: Session, user: User, application: Application) -> None:
        self.application = application
        self.job: Job = application.user_job.job
        snapshot = application.context
        self.snapshot_id: uuid.UUID | None = snapshot.id if snapshot is not None else None
        self.context_document: ApplicationContextDocument | None = (
            snapshot.read_document() if snapshot is not None else None
        )
        resume = application.resume
        # A resume reference that isn't the user's own is never followed.
        self.resume: Resume | None = (
            resume if resume is not None and resume.user_id == user.id else None
        )
        self.profile: ResumeProfile | None = None
        self.profile_data: ResumeProfileData | None = None
        if self.resume is not None:
            self.profile = db.scalar(
                select(ResumeProfile)
                .where(
                    ResumeProfile.resume_id == self.resume.id,
                    ResumeProfile.user_id == user.id,
                    ResumeProfile.status == ResumeProfileStatus.CONFIRMED,
                )
                .order_by(ResumeProfile.confirmed_at.desc().nulls_last())
                .limit(1)
            )
            if self.profile is not None and self.profile.reviewed_data is not None:
                self.profile_data = ResumeProfileData.model_validate(self.profile.reviewed_data)
        notice = (
            (application.notice_period_value, application.notice_period_unit)
            if application.notice_period_value is not None
            and application.notice_period_unit is not None
            else None
        )
        self.preparation = Preparation(
            language=application.application_language,
            salary_expectation=application.salary_expectation,
            salary_currency=application.salary_currency,
            mention_salary_in_letter=application.mention_salary_in_letter,
            available_from=application.available_from,
            notice_period=notice,
            work_authorization=application.work_authorization,
        )


def _build_context(
    db: Session,
    user: User,
    inputs: _Inputs,
    document_type: GeneratedDocumentType,
) -> GenerationContext:
    """The generation context of an application that is ready (see `_readiness`)."""
    assert inputs.context_document is not None
    assert inputs.profile is not None and inputs.profile_data is not None
    assert inputs.preparation.language is not None
    match: MatchGuidance | None = None
    if document_type is GeneratedDocumentType.COVER_LETTER:
        match = _match_guidance(db, user, inputs.job)
    return build_generation_context(
        document_type=document_type,
        language=inputs.preparation.language,
        resume_profile_id=inputs.profile.id,
        profile_data=inputs.profile_data,
        account_name=(user.first_name, user.last_name),
        job=inputs.context_document.job,
        preparation=inputs.preparation,
        match=match,
    )


def _match_guidance(db: Session, user: User, job: Job) -> MatchGuidance | None:
    """The current V0.6 match as hints for the letter -- only when it is
    up to date, and never recalculated here."""
    match = match_status.current_matches(db, user, [job.id]).get(job.id)
    if match is None:
        return None
    if match_status.stale_reasons(match, job, build_snapshot(db, user).content_hash):
        return None
    summary = summarize(match.read_evidence())
    return MatchGuidance(
        strong_matches=tuple(item.text for item in summary.strong_matches),
        gaps=tuple(item.text for item in summary.gaps),
    )


def _readiness(
    inputs: _Inputs, document_type: GeneratedDocumentType, *, now: datetime | None = None
) -> DocumentReadiness:
    has_facts = False
    if inputs.profile is not None and inputs.profile_data is not None:
        has_facts = not build_candidate_facts(
            inputs.profile.id, inputs.profile_data, now=now
        ).is_empty
    return readiness(
        ReadinessInputs(
            has_context=inputs.context_document is not None and inputs.snapshot_id is not None,
            has_resume=inputs.resume is not None,
            resume_version_number=inputs.resume.version_number if inputs.resume else None,
            has_confirmed_profile=inputs.profile_data is not None,
            has_facts=has_facts,
            language=inputs.preparation.language,
            salary_expectation=inputs.preparation.salary_expectation,
            mention_salary_in_letter=inputs.preparation.mention_salary_in_letter,
        ),
        document_type,
    )


# --- Reading ------------------------------------------------------------------------------------


def _stale_cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=settings.DOCUMENT_GENERATION_STALE_MINUTES)


def _effective(
    row: GeneratedDocument,
) -> tuple[GeneratedDocumentStatus, str | None, str | None]:
    """Status, error code and message as shown: a generation still running
    long after it started was abandoned (e.g. a restart) and counts as failed."""
    if row.status is GeneratedDocumentStatus.GENERATING and row.created_at < _stale_cutoff():
        return GeneratedDocumentStatus.FAILED, "GENERATION_ABANDONED", ABANDONED_MESSAGE
    return row.status, row.error_code, row.error_message


def require_edited_current_acknowledged(
    db: Session,
    application_id: uuid.UUID,
    document_type: GeneratedDocumentType,
    request: GenerateDocumentRequest | None,
) -> None:
    """Human Control: a new AI version becomes the *current* one, so when the
    current version holds the user's own edits the user must say they know. The
    edited version is never deleted or changed -- it stays in the history -- but
    it is never superseded silently."""
    if request is not None and request.acknowledge_edited_current:
        return
    current = db.scalar(
        select(GeneratedDocument)
        .where(
            GeneratedDocument.application_id == application_id,
            GeneratedDocument.document_type == document_type,
            GeneratedDocument.status == GeneratedDocumentStatus.VALIDATED,
        )
        .order_by(GeneratedDocument.version_number.desc())
        .limit(1)
    )
    if current is not None and current.origin is GeneratedDocumentOrigin.USER_EDITED:
        raise AppError(
            "EDITED_DOCUMENT_CONFIRMATION_REQUIRED",
            "The current version contains your own edits. A new version will not delete them "
            "(they stay in the history) but it will replace them as the current version. "
            "Confirm to continue.",
            409,
            {"current_version": current.version_number},
        )


def _current_versions(rows: list[GeneratedDocument]) -> dict[GeneratedDocumentType, int]:
    current: dict[GeneratedDocumentType, int] = {}
    for row in rows:
        if row.status is GeneratedDocumentStatus.VALIDATED and row.version_number is not None:
            current[row.document_type] = max(current.get(row.document_type, 0), row.version_number)
    return current


def _to_summary(
    row: GeneratedDocument, *, is_current: bool, is_outdated: bool
) -> GeneratedDocumentSummary:
    status, error_code, error_message = _effective(row)
    report = ValidationReport.model_validate(row.validation) if row.validation else None
    if row.origin is GeneratedDocumentOrigin.USER_EDITED:
        validation_status: Any = "user_authored"
    else:
        validation_status = report.status if report else "not_run"
    return GeneratedDocumentSummary(
        id=row.id,
        application_id=row.application_id,
        document_type=row.document_type,
        status=status,
        version_number=row.version_number,
        is_current=is_current,
        is_outdated=is_outdated,
        language=row.language,
        resume_id=row.resume_id,
        resume_version_number=row.resume_version_number,
        application_context_snapshot_id=row.application_context_snapshot_id,
        input_hash=row.input_hash,
        prompt_version=row.prompt_version,
        prompt_hash=row.prompt_hash,
        origin=row.origin,
        base_document_id=row.base_document_id,
        ai_provider=row.ai_provider,
        ai_model=row.ai_model,
        validation_status=validation_status,
        violation_count=len(report.violations) if report else 0,
        error_code=error_code,
        error_message=error_message,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def _referenced_fact_ids(value: Any) -> set[str]:
    """Every fact id a stored content mentions."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source_fact_id" and isinstance(item, str):
                found.add(item)
            elif key == "source_fact_ids" and isinstance(item, list):
                found.update(i for i in item if isinstance(i, str))
            else:
                found |= _referenced_fact_ids(item)
    elif isinstance(value, list):
        for item in value:
            found |= _referenced_fact_ids(item)
    return found


def _to_read(
    row: GeneratedDocument, *, is_current: bool, is_outdated: bool
) -> GeneratedDocumentRead:
    summary = _to_summary(row, is_current=is_current, is_outdated=is_outdated)
    facts = row.source_facts or {}
    referenced = _referenced_fact_ids(row.content) if row.content else set()
    return GeneratedDocumentRead(
        **summary.model_dump(),
        content=row.content,  # type: ignore[arg-type]
        validation=row.validation,  # type: ignore[arg-type]
        source_facts={fact_id: facts[fact_id] for fact_id in facts if fact_id in referenced},
    )


def _current_hash(
    db: Session, user: User, inputs: _Inputs, document_type: GeneratedDocumentType
) -> str | None:
    """The input hash a generation would have today, or None when the
    application isn't ready (then "outdated" can't be told)."""
    if not _readiness(inputs, document_type).can_generate:
        return None
    return _build_context(db, user, inputs, document_type).input_hash


def list_documents(db: Session, user: User, application_id: uuid.UUID) -> DocumentListResponse:
    """Every attempt for the user's application, newest first, with what is
    needed before the next generation of each type."""
    application = get_owned_application(db, user, application_id)
    inputs = _Inputs(db, user, application)
    rows = list(
        db.scalars(
            select(GeneratedDocument)
            .where(
                GeneratedDocument.application_id == application.id,
                GeneratedDocument.user_id == user.id,
            )
            .order_by(GeneratedDocument.created_at.desc(), GeneratedDocument.id)
            .execution_options(populate_existing=True)
        )
    )
    current = _current_versions(rows)
    hashes: dict[GeneratedDocumentType, str | None] = {}
    items: list[GeneratedDocumentSummary] = []
    for row in rows:
        is_current = current.get(row.document_type) == row.version_number and (
            row.status is GeneratedDocumentStatus.VALIDATED
        )
        is_outdated = False
        if is_current:
            if row.document_type not in hashes:
                hashes[row.document_type] = _current_hash(db, user, inputs, row.document_type)
            today = hashes[row.document_type]
            is_outdated = today is not None and today != row.input_hash
        items.append(_to_summary(row, is_current=is_current, is_outdated=is_outdated))
    return DocumentListResponse(
        items=items,
        tailored_cv=_readiness(inputs, GeneratedDocumentType.TAILORED_CV),
        cover_letter=_readiness(inputs, GeneratedDocumentType.COVER_LETTER),
        photo=photos.read_photo(db, application.id, user),
    )


def get_document(
    db: Session, user: User, application_id: uuid.UUID, document_id: uuid.UUID
) -> GeneratedDocumentRead:
    """One of the user's documents (404 for anything that isn't theirs)."""
    application = get_owned_application(db, user, application_id)
    row = db.scalar(
        select(GeneratedDocument)
        .where(
            GeneratedDocument.id == document_id,
            GeneratedDocument.application_id == application.id,
            GeneratedDocument.user_id == user.id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError("Document not found.")
    newest = db.scalar(
        select(func.max(GeneratedDocument.version_number)).where(
            GeneratedDocument.application_id == application.id,
            GeneratedDocument.document_type == row.document_type,
            GeneratedDocument.status == GeneratedDocumentStatus.VALIDATED,
        )
    )
    is_current = row.status is GeneratedDocumentStatus.VALIDATED and row.version_number == newest
    is_outdated = False
    if is_current:
        today = _current_hash(db, user, _Inputs(db, user, application), row.document_type)
        is_outdated = today is not None and today != row.input_hash
    return _to_read(row, is_current=is_current, is_outdated=is_outdated)


# --- Generating ---------------------------------------------------------------------------------


def _finish(
    db: Session,
    document_id: uuid.UUID,
    status: GeneratedDocumentStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    report: ValidationReport | None = None,
) -> None:
    """Move an in-flight attempt to FAILED or REJECTED. Commits."""
    db.rollback()
    row = db.get(GeneratedDocument, document_id)
    if row is None or row.status is not GeneratedDocumentStatus.GENERATING:
        return
    row.status = status
    row.error_code = error_code
    row.error_message = error_message
    row.validation = report.model_dump(mode="json") if report else None
    row.completed_at = datetime.now(UTC)
    db.commit()


def _expire_abandoned(db: Session, application_id: uuid.UUID, document_type: Any) -> None:
    """A generation that never finished (a crashed worker, a restart) must not
    block regenerating forever: mark it failed."""
    stuck = db.scalars(
        select(GeneratedDocument).where(
            GeneratedDocument.application_id == application_id,
            GeneratedDocument.document_type == document_type,
            GeneratedDocument.status == GeneratedDocumentStatus.GENERATING,
            GeneratedDocument.created_at < _stale_cutoff(),
        )
    ).all()
    for row in stuck:
        row.status = GeneratedDocumentStatus.FAILED
        row.error_code = "GENERATION_ABANDONED"
        row.error_message = ABANDONED_MESSAGE
        row.completed_at = datetime.now(UTC)
    if stuck:
        db.flush()


async def generate(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    document_type: GeneratedDocumentType,
    provider: AIProvider,
    request: GenerateDocumentRequest | None = None,
) -> GeneratedDocumentRead:
    """Generate a new version of the application's tailored CV or letter."""
    application = get_owned_application(db, user, application_id)
    inputs = _Inputs(db, user, application)
    require_edited_current_acknowledged(db, application_id, document_type, request)

    state = _readiness(inputs, document_type)
    if not state.can_generate:
        first = state.blockers[0]
        raise AppError(
            first.code,
            first.message,
            409,
            {"blockers": [blocker.model_dump() for blocker in state.blockers]},
        )
    context = _build_context(db, user, inputs, document_type)
    assert inputs.resume is not None and inputs.profile is not None
    assert inputs.snapshot_id is not None

    row = GeneratedDocument(
        application_id=application_id,
        user_id=user.id,
        document_type=document_type,
        status=GeneratedDocumentStatus.GENERATING,
        language=context.language,
        resume_id=inputs.resume.id,
        resume_profile_id=inputs.profile.id,
        resume_version_number=inputs.resume.version_number,
        application_context_snapshot_id=inputs.snapshot_id,
        input_hash=context.input_hash,
        prompt_version=PROMPT_VERSIONS[document_type],
        prompt_hash=prompt_hash(PROMPT_VERSIONS[document_type]),
        ai_provider=provider.name,
        ai_model=provider.model,
        source_facts=context.catalog.as_source_facts(),
    )
    _expire_abandoned(db, application_id, document_type)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        db.rollback()
        raise AppError(
            "GENERATION_IN_PROGRESS",
            "This document is already being generated. Please wait for it to finish.",
            409,
        ) from None
    # Plain values from here on: the session must not hold a transaction open
    # (or reload expired rows) while waiting for the AI.
    document_id = row.id
    db.commit()
    schema = AITailoredCV if document_type is GeneratedDocumentType.TAILORED_CV else AICoverLetter

    def fail(code: str, message: str, status_code: int) -> AppError:
        _finish(
            db,
            document_id,
            GeneratedDocumentStatus.FAILED,
            error_code=code,
            error_message=message,
        )
        return AppError(code, message, status_code, {"document_id": str(document_id)})

    try:
        output = await provider.generate_structured(
            system=system_prompt(document_type), prompt=user_prompt(context), schema=schema
        )
    except AIError as exc:
        # Codes and exception types only: never prompts, facts or provider output.
        logger.warning("Document generation failed: %s (%s)", exc.code, type(exc).__name__)
        status_code, message = _ai_failure(exc)
        raise fail(exc.code, message, status_code) from None
    except asyncio.CancelledError:
        _finish(
            db,
            document_id,
            GeneratedDocumentStatus.FAILED,
            error_code="GENERATION_CANCELLED",
            error_message=GENERATION_FAILED_MESSAGE,
        )
        raise
    except Exception as exc:
        logger.warning("Document generation failed unexpectedly (%s)", type(exc).__name__)
        raise fail("GENERATION_ERROR", GENERATION_FAILED_MESSAGE, 500) from None

    try:
        if isinstance(output, AITailoredCV):
            report = validation.validate_cv(output, context)
        else:
            assert isinstance(output, AICoverLetter)
            report = validation.validate_letter(output, context)
        if report.status == "failed":
            logger.warning(
                "Generated %s rejected: %s",
                document_type.value,
                ", ".join(sorted({v.code for v in report.violations})),
            )
            _finish(
                db,
                document_id,
                GeneratedDocumentStatus.REJECTED,
                error_code="DOCUMENT_REJECTED",
                error_message=_REJECTED_MESSAGE,
                report=report,
            )
            raise AppError(
                "DOCUMENT_REJECTED",
                _REJECTED_MESSAGE,
                502,
                {
                    "document_id": str(document_id),
                    "violations": [v.model_dump() for v in report.violations],
                },
            )
        content = (
            assembly.assemble_cv(output, context)
            if isinstance(output, AITailoredCV)
            else assembly.assemble_letter(output, context)  # type: ignore[arg-type]
        )
        _publish(
            db, application_id, document_id, document_type, content.model_dump(mode="json"), report
        )
    except AppError:
        raise
    except Exception as exc:
        logger.warning("Document finalization failed unexpectedly (%s)", type(exc).__name__)
        raise fail("GENERATION_ERROR", GENERATION_FAILED_MESSAGE, 500) from None

    return get_document(db, user, application_id, document_id)


def _publish(
    db: Session,
    application_id: uuid.UUID,
    document_id: uuid.UUID,
    document_type: GeneratedDocumentType,
    content: dict[str, Any],
    report: ValidationReport,
) -> None:
    """Make the attempt a VALIDATED document with the next version number.
    The application row is locked so concurrent finishers (a CV and a letter
    are different types; two CV attempts can't overlap, but a retry racing a
    stale one can) get distinct numbers; the unique constraint backs it up."""
    db.execute(select(Application.id).where(Application.id == application_id).with_for_update())
    newest = db.scalar(
        select(func.max(GeneratedDocument.version_number)).where(
            GeneratedDocument.application_id == application_id,
            GeneratedDocument.document_type == document_type,
            GeneratedDocument.status == GeneratedDocumentStatus.VALIDATED,
        )
    )
    row = db.get(GeneratedDocument, document_id)
    if row is None or row.status is not GeneratedDocumentStatus.GENERATING:
        db.rollback()
        raise AppError(
            "GENERATION_ABANDONED", ABANDONED_MESSAGE, 409, {"document_id": str(document_id)}
        )
    row.version_number = (newest or 0) + 1
    row.status = GeneratedDocumentStatus.VALIDATED
    row.content = content
    row.validation = report.model_dump(mode="json")
    row.error_code = None
    row.error_message = None
    row.completed_at = datetime.now(UTC)
    db.commit()


# --- Humanize and the user's edits (V0.9) -----------------------------------------------------

HUMANIZE_REJECTED_MESSAGE = (
    "The reworded letter contained changes your facts do not support (or dropped something "
    "from the original), so it was discarded. Your letter was not changed. Please try again."
)


def _validated_base(
    db: Session, application_id: uuid.UUID, user: User, document_id: uuid.UUID | None
) -> GeneratedDocument:
    """The version to work from: the given one, or the current letter. Only a
    validated document of this application and user."""
    statement = select(GeneratedDocument).where(
        GeneratedDocument.application_id == application_id,
        GeneratedDocument.user_id == user.id,
        GeneratedDocument.document_type == GeneratedDocumentType.COVER_LETTER,
        GeneratedDocument.status == GeneratedDocumentStatus.VALIDATED,
    )
    if document_id is not None:
        statement = statement.where(GeneratedDocument.id == document_id)
    else:
        statement = statement.order_by(GeneratedDocument.version_number.desc()).limit(1)
    base = db.scalar(statement.execution_options(populate_existing=True))
    if base is None:
        if document_id is not None:
            raise NotFoundError("Document not found.")
        raise AppError(
            "NO_COVER_LETTER", "Generate an Anschreiben first: there is nothing to humanize.", 409
        )
    return base


async def humanize(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    provider: AIProvider,
    request: HumanizeRequest | None = None,
) -> GeneratedDocumentRead:
    """Reword a validated Anschreiben for a more natural tone, as a NEW version.

    The pipeline is the generation pipeline's (GENERATING row -> AI -> truth
    validation -> VALIDATED version, or REJECTED / FAILED with nothing changed);
    only the AI's input (an already validated letter) and the extra rewording rules
    (`humanize.validate`) differ. The original version is never touched."""
    application = get_owned_application(db, user, application_id)
    document_type = GeneratedDocumentType.COVER_LETTER
    require_edited_current_acknowledged(db, application_id, document_type, request)
    base = _validated_base(db, application_id, user, request.base_document_id if request else None)
    content = base.content
    assert content is not None
    letter = CoverLetterContent.model_validate(content)
    facts = {
        fact_id: SourceFact.model_validate(fact)
        for fact_id, fact in (base.source_facts or {}).items()
    }
    context = humanize_logic.build_context(letter, facts)
    prompt = humanize_logic.user_prompt(humanize_logic.payload(letter, facts))
    version = HUMANIZE_PROMPT_VERSION

    row = GeneratedDocument(
        application_id=application.id,
        user_id=user.id,
        document_type=document_type,
        status=GeneratedDocumentStatus.GENERATING,
        origin=GeneratedDocumentOrigin.HUMANIZED,
        base_document_id=base.id,
        language=base.language,
        resume_id=base.resume_id,
        resume_profile_id=base.resume_profile_id,
        resume_version_number=base.resume_version_number,
        application_context_snapshot_id=base.application_context_snapshot_id,
        # The inputs of the letter are unchanged: it is outdated exactly when its base is.
        input_hash=base.input_hash,
        prompt_version=version,
        prompt_hash=prompt_hash(version),
        ai_provider=provider.name,
        ai_model=provider.model,
        source_facts=base.source_facts or {},
    )
    _expire_abandoned(db, application.id, document_type)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        db.rollback()
        raise AppError(
            "GENERATION_IN_PROGRESS",
            "This document is already being generated. Please wait for it to finish.",
            409,
        ) from None
    document_id = row.id
    system = load_prompt(version)
    db.commit()

    def fail(code: str, message: str, status_code: int) -> AppError:
        _finish(
            db,
            document_id,
            GeneratedDocumentStatus.FAILED,
            error_code=code,
            error_message=message,
        )
        return AppError(code, message, status_code, {"document_id": str(document_id)})

    try:
        output = await provider.generate_structured(
            system=system, prompt=prompt, schema=AIHumanizedLetter
        )
    except AIError as exc:
        logger.warning("Humanize failed: %s (%s)", exc.code, type(exc).__name__)
        status_code, message = _ai_failure(exc)
        raise fail(exc.code, message, status_code) from None
    except asyncio.CancelledError:
        _finish(
            db,
            document_id,
            GeneratedDocumentStatus.FAILED,
            error_code="GENERATION_CANCELLED",
            error_message=GENERATION_FAILED_MESSAGE,
        )
        raise
    except Exception as exc:
        logger.warning("Humanize failed unexpectedly (%s)", type(exc).__name__)
        raise fail("GENERATION_ERROR", GENERATION_FAILED_MESSAGE, 500) from None

    try:
        report = humanize_logic.validate(output, letter, context)
        if report.status == "failed":
            logger.warning(
                "Humanized letter rejected: %s",
                ", ".join(sorted({v.code for v in report.violations})),
            )
            _finish(
                db,
                document_id,
                GeneratedDocumentStatus.REJECTED,
                error_code="DOCUMENT_REJECTED",
                error_message=HUMANIZE_REJECTED_MESSAGE,
                report=report,
            )
            raise AppError(
                "DOCUMENT_REJECTED",
                HUMANIZE_REJECTED_MESSAGE,
                502,
                {
                    "document_id": str(document_id),
                    "violations": [v.model_dump() for v in report.violations],
                },
            )
        reworded = humanize_logic.assemble(output, letter)
        _publish(
            db, application.id, document_id, document_type, reworded.model_dump(mode="json"), report
        )
    except AppError:
        raise
    except Exception as exc:
        logger.warning("Humanize finalization failed unexpectedly (%s)", type(exc).__name__)
        raise fail("GENERATION_ERROR", GENERATION_FAILED_MESSAGE, 500) from None
    return get_document(db, user, application_id, document_id)


def edit_document(
    db: Session,
    user: User,
    application_id: uuid.UUID,
    document_id: uuid.UUID,
    edit: CvEditRequest | LetterEditRequest,
) -> GeneratedDocumentRead:
    """Save the user's edits of a version as a NEW version (origin `user_edited`).

    The edited version is not changed. Only text is editable; what is not edited
    is carried over unchanged. No AI is involved, and nothing is validated:
    the user's own words are theirs, and are shown as such."""
    application = get_owned_application(db, user, application_id)
    base = db.scalar(
        select(GeneratedDocument)
        .where(
            GeneratedDocument.id == document_id,
            GeneratedDocument.application_id == application.id,
            GeneratedDocument.user_id == user.id,
            GeneratedDocument.status == GeneratedDocumentStatus.VALIDATED,
        )
        .execution_options(populate_existing=True)
    )
    if base is None:
        raise NotFoundError("Document not found.")
    wanted = (
        GeneratedDocumentType.TAILORED_CV
        if isinstance(edit, CvEditRequest)
        else GeneratedDocumentType.COVER_LETTER
    )
    if base.document_type is not wanted:
        raise AppError(
            "DOCUMENT_TYPE_MISMATCH", "This edit does not belong to this kind of document.", 422
        )
    assert base.content is not None
    if isinstance(edit, CvEditRequest):
        content: dict[str, Any] = editing.apply_cv_edit(
            TailoredCvContent.model_validate(base.content), edit
        ).model_dump(mode="json")
    else:
        content = editing.apply_letter_edit(
            CoverLetterContent.model_validate(base.content), edit
        ).model_dump(mode="json")

    db.execute(select(Application.id).where(Application.id == application.id).with_for_update())
    newest = db.scalar(
        select(func.max(GeneratedDocument.version_number)).where(
            GeneratedDocument.application_id == application.id,
            GeneratedDocument.document_type == base.document_type,
            GeneratedDocument.status == GeneratedDocumentStatus.VALIDATED,
        )
    )
    row = GeneratedDocument(
        application_id=application.id,
        user_id=user.id,
        document_type=base.document_type,
        status=GeneratedDocumentStatus.VALIDATED,
        origin=GeneratedDocumentOrigin.USER_EDITED,
        base_document_id=base.id,
        version_number=(newest or 0) + 1,
        language=base.language,
        resume_id=base.resume_id,
        resume_profile_id=base.resume_profile_id,
        resume_version_number=base.resume_version_number,
        application_context_snapshot_id=base.application_context_snapshot_id,
        input_hash=base.input_hash,
        prompt_version="user_edit",
        prompt_hash=None,
        ai_provider="user",
        ai_model=None,
        source_facts=base.source_facts or {},
        content=content,
        validation=None,
        completed_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    return get_document(db, user, application_id, row.id)


# --- Shared with other generators ---------------------------------------------------------------
# V0.9's question answers load the same saved application data and apply the
# same readiness rules, so there is one definition of "what a generation may
# be built from".

#: The application's saved data (snapshot, resume, confirmed profile, preparation).
GenerationInputs = _Inputs
generation_readiness = _readiness
