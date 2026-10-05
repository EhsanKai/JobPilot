"""Orchestrates the Resume Intelligence pipeline.

    start_processing()  (inside the HTTP request, fast)
        ownership check -> concurrency guard -> file hash -> reuse check
        -> create a PROCESSING ResumeProfile row
    run_processing()    (FastAPI background task)
        read original file -> extract text -> parse (AI) -> validate
        -> persist as NEEDS_REVIEW

Each stage lives in its own module (extraction, parser, validation); this
module only sequences them and records status transitions:

    resume.status: uploaded -> extracting -> extracted -> parsing -> parsed
                   -> needs_review -> (user) confirmed
                   or failed / ocr_required

Privacy: nothing in this module logs resume text, prompts, parser output,
or exception messages that could embed them -- only ids, codes, counts and
exception *types*.
"""

import hashlib
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.provider import AIError
from app.core.config import settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.resume import IN_PROGRESS_RESUME_STATUSES, Resume, ResumeStatus
from app.models.resume_profile import (
    SUCCESSFUL_PROFILE_STATUSES,
    ResumeProfile,
    ResumeProfileStatus,
)
from app.models.user import User
from app.schemas.resume_profile import ProfileWarning, ResumeProfileData
from app.services.resume_intelligence.extraction import (
    ExtractionError,
    OcrRequiredError,
    get_extractor,
)
from app.services.resume_intelligence.parser import ResumeParser
from app.services.resume_intelligence.validation import NoResumeContentError, build_profile_data
from app.services.storage.base import FileStorage

logger = get_logger(__name__)

GENERIC_FAILURE_MESSAGE = "Something went wrong while analyzing your resume. Please try again."


class ResumeTooLongError(Exception):
    code = "RESUME_TOO_LONG"
    user_message = (
        "This resume contains more text than JobPilot can analyze at once. "
        "Please upload a shorter version."
    )


class FileMissingError(Exception):
    code = "FILE_MISSING"
    user_message = "The original resume file could not be found. Please upload it again."


@dataclass(frozen=True)
class ProcessingStart:
    resume: Resume
    profile: ResumeProfile
    #: True when an existing result was reused and no processing is needed.
    reused: bool


def _now() -> datetime:
    return datetime.now(UTC)


def compute_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def get_owned_resume(db: Session, user: User, resume_id: uuid.UUID) -> Resume:
    """Fetch a resume only if it belongs to `user` (404 otherwise, never 403,
    so other users' resume ids can't be probed)."""
    resume = db.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id)
    ).scalar_one_or_none()
    if resume is None:
        raise NotFoundError("Resume not found.")
    return resume


def get_current_profile(db: Session, resume: Resume) -> ResumeProfile | None:
    """The resume's current result: newest run that produced a usable profile."""
    return db.execute(
        select(ResumeProfile)
        .where(
            ResumeProfile.resume_id == resume.id,
            ResumeProfile.status.in_(SUCCESSFUL_PROFILE_STATUSES),
        )
        .order_by(ResumeProfile.created_at.desc())
    ).scalars().first()


def get_latest_attempt(db: Session, resume: Resume) -> ResumeProfile | None:
    return db.execute(
        select(ResumeProfile)
        .where(ResumeProfile.resume_id == resume.id)
        .order_by(ResumeProfile.created_at.desc())
    ).scalars().first()


def is_processing(resume: Resume) -> bool:
    if resume.status not in IN_PROGRESS_RESUME_STATUSES:
        return False
    started = resume.processing_started_at
    if started is None:
        return True
    stale_after = timedelta(minutes=settings.RESUME_PROCESSING_STALE_MINUTES)
    return _now() - started < stale_after


def start_processing(
    db: Session,
    user: User,
    storage: FileStorage,
    parser: ResumeParser,
    resume_id: uuid.UUID,
    *,
    force: bool = False,
) -> ProcessingStart:
    resume = db.execute(
        select(Resume)
        .where(Resume.id == resume_id, Resume.user_id == user.id)
        .with_for_update()
    ).scalar_one_or_none()
    if resume is None:
        raise NotFoundError("Resume not found.")
    if is_processing(resume):
        raise ConflictError("This resume is already being analyzed.")

    if resume.file_hash is None:
        # Uploaded before V0.2: hash the (unchanged) original once.
        try:
            resume.file_hash = compute_file_hash(storage.read(reference=resume.storage_reference))
        except FileNotFoundError:
            raise AppError(
                FileMissingError.code, FileMissingError.user_message, status_code=409
            ) from None

    if not force:
        reused = _reuse_existing_result(db, user, resume, parser)
        if reused is not None:
            db.commit()
            return ProcessingStart(resume=resume, profile=reused, reused=True)

    now = _now()
    profile = ResumeProfile(
        resume_id=resume.id,
        user_id=user.id,
        status=ResumeProfileStatus.PROCESSING,
        parser_version=parser.version,
        source_file_hash=resume.file_hash,
        ai_provider=parser.provider_name,
        ai_model=parser.model,
        warnings=[],
        started_at=now,
    )
    db.add(profile)
    resume.status = ResumeStatus.EXTRACTING
    resume.processing_started_at = now
    db.commit()
    db.refresh(profile)
    logger.info(
        "Resume processing queued resume_id=%s profile_id=%s parser=%s force=%s",
        resume.id,
        profile.id,
        parser.version,
        force,
    )
    return ProcessingStart(resume=resume, profile=profile, reused=False)


def _reuse_existing_result(
    db: Session, user: User, resume: Resume, parser: ResumeParser
) -> ResumeProfile | None:
    """Avoid paying for the same analysis twice.

    1. This resume already has a successful result for this exact file and
       parser version -> return it unchanged.
    2. Another of the user's resumes (e.g. the same file uploaded again as a
       new version) has one -> copy its parser output into a fresh,
       unreviewed profile for this resume. Review decisions are not copied:
       each resume version is reviewed and confirmed on its own.
    """
    current = get_current_profile(db, resume)
    if (
        current is not None
        and current.parser_version == parser.version
        and current.source_file_hash == resume.file_hash
    ):
        return current

    donor = db.execute(
        select(ResumeProfile)
        .where(
            ResumeProfile.user_id == user.id,
            ResumeProfile.source_file_hash == resume.file_hash,
            ResumeProfile.parser_version == parser.version,
            ResumeProfile.status.in_(
                [*SUCCESSFUL_PROFILE_STATUSES, ResumeProfileStatus.SUPERSEDED]
            ),
            ResumeProfile.raw_data.is_not(None),
        )
        .order_by(ResumeProfile.created_at.desc())
    ).scalars().first()
    if donor is None or donor.raw_data is None:
        return None

    _supersede_previous(db, resume, keep=None)
    now = _now()
    copy = ResumeProfile(
        resume_id=resume.id,
        user_id=user.id,
        status=ResumeProfileStatus.NEEDS_REVIEW,
        parser_version=donor.parser_version,
        schema_version=donor.schema_version,
        source_file_hash=donor.source_file_hash,
        ai_provider=donor.ai_provider,
        ai_model=donor.ai_model,
        reused_from_profile_id=donor.id,
        extraction_method=donor.extraction_method,
        page_count=donor.page_count,
        extracted_text=donor.extracted_text,
        raw_data=donor.raw_data,
        reviewed_data=ResumeProfileData.model_validate(donor.raw_data).model_dump(mode="json"),
        warnings=list(donor.warnings),
        started_at=now,
        processed_at=now,
    )
    db.add(copy)
    resume.status = ResumeStatus.NEEDS_REVIEW
    resume.processing_started_at = None
    db.flush()
    logger.info(
        "Resume processing reused resume_id=%s profile_id=%s donor_profile_id=%s",
        resume.id,
        copy.id,
        donor.id,
    )
    return copy


def _supersede_previous(db: Session, resume: Resume, keep: ResumeProfile | None) -> None:
    previous = db.execute(
        select(ResumeProfile).where(
            ResumeProfile.resume_id == resume.id,
            ResumeProfile.status.in_(SUCCESSFUL_PROFILE_STATUSES),
        )
    ).scalars()
    for old in previous:
        if keep is None or old.id != keep.id:
            old.status = ResumeProfileStatus.SUPERSEDED


async def run_processing(
    profile_id: uuid.UUID,
    *,
    session_factory: sessionmaker[Session],
    storage: FileStorage,
    parser: ResumeParser,
) -> None:
    """Background task: run the pipeline for one queued ResumeProfile."""
    started = time.monotonic()
    db = session_factory()
    try:
        profile = db.get(ResumeProfile, profile_id)
        if profile is None:  # resume deleted before the task started
            return
        resume = profile.resume

        # -- Stage 1: extraction ------------------------------------------
        try:
            content = storage.read(reference=resume.storage_reference)
        except FileNotFoundError:
            raise FileMissingError("original file missing") from None
        extractor = get_extractor(resume.mime_type)
        extracted = await extractor.extract(content)
        if extracted.char_count > settings.RESUME_MAX_TEXT_CHARS:
            raise ResumeTooLongError(f"{extracted.char_count} chars")

        profile.extraction_method = extracted.method
        profile.page_count = extracted.page_count
        profile.extracted_text = extracted.text
        resume.status = ResumeStatus.EXTRACTED
        db.commit()

        # -- Stage 2: parsing ---------------------------------------------
        resume.status = ResumeStatus.PARSING
        db.commit()
        parsed = await parser.parse(extracted.text)
        resume.status = ResumeStatus.PARSED
        db.commit()

        # -- Stage 3: validation ------------------------------------------
        result = build_profile_data(parsed, extracted.text)

        # -- Stage 4: persistence -----------------------------------------
        document = result.data.model_dump(mode="json")
        profile.raw_data = document
        profile.reviewed_data = document
        profile.warnings = [w.model_dump(mode="json") for w in result.warnings]
        profile.status = ResumeProfileStatus.NEEDS_REVIEW
        profile.processed_at = _now()
        _supersede_previous(db, resume, keep=profile)
        resume.status = ResumeStatus.NEEDS_REVIEW
        resume.processing_started_at = None
        db.commit()
        logger.info(
            "Resume processing succeeded resume_id=%s profile_id=%s items=%d warnings=%d "
            "chars=%d duration_ms=%d",
            resume.id,
            profile.id,
            sum(1 for _ in result.data.iter_items()),
            len(result.warnings),
            extracted.char_count,
            (time.monotonic() - started) * 1000,
        )
    except OcrRequiredError as exc:
        _record_failure(db, profile_id, exc.code, exc.user_message, str(exc), ocr=True)
    except (
        ExtractionError,
        AIError,
        NoResumeContentError,
        ResumeTooLongError,
        FileMissingError,
    ) as exc:
        _record_failure(db, profile_id, exc.code, exc.user_message, str(exc))
    except Exception as exc:  # last resort: never leave a run stuck in progress
        frame = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
        location = f"{frame.filename}:{frame.lineno}" if frame else "unknown"
        # Deliberately no logger.exception(): exception messages (e.g. DB or
        # validation errors) can embed resume content.
        logger.error(
            "Resume processing crashed profile_id=%s exc_type=%s at=%s",
            profile_id,
            type(exc).__name__,
            location,
        )
        _record_failure(db, profile_id, "PROCESSING_ERROR", GENERIC_FAILURE_MESSAGE, "crash")
    finally:
        db.close()


def _record_failure(
    db: Session,
    profile_id: uuid.UUID,
    code: str,
    user_message: str,
    reason: str,
    *,
    ocr: bool = False,
) -> None:
    db.rollback()
    profile = db.get(ResumeProfile, profile_id)
    if profile is None:
        return
    resume = profile.resume
    profile.status = ResumeProfileStatus.OCR_REQUIRED if ocr else ResumeProfileStatus.FAILED
    profile.error_code = code
    profile.error_message = user_message[:500]
    profile.processed_at = _now()

    # A failed *re*-processing never discards a previous good result: the
    # resume keeps that result's status and the failure is reported via the
    # latest attempt instead.
    previous = get_current_profile(db, resume)
    if previous is not None:
        resume.status = (
            ResumeStatus.CONFIRMED
            if previous.status == ResumeProfileStatus.CONFIRMED
            else ResumeStatus.NEEDS_REVIEW
        )
    else:
        resume.status = ResumeStatus.OCR_REQUIRED if ocr else ResumeStatus.FAILED
    resume.processing_started_at = None
    db.commit()
    # `reason` is a short technical string produced by our own error types
    # (never document content or provider response bodies).
    logger.warning(
        "Resume processing failed resume_id=%s profile_id=%s code=%s reason=%s",
        resume.id,
        profile_id,
        code,
        reason,
    )


def profile_warnings(profile: ResumeProfile) -> list[ProfileWarning]:
    return [ProfileWarning.model_validate(w) for w in profile.warnings or []]
