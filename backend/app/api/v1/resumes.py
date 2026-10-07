import io
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user, get_resume_parser
from app.core.config import settings
from app.core.errors import NotFoundError
from app.db.session import get_db, get_session_factory
from app.models.user import User
from app.schemas.resume import ResumeIntelligenceRead, ResumeProcessResponse, ResumeRead
from app.schemas.resume_profile import (
    ProcessingAttemptRead,
    ResumeProcessRequest,
    ResumeProfileRead,
    ResumeSourceRead,
    ReviewItemRequest,
)
from app.services import resume_service
from app.services.resume_intelligence import processing_service, review_service
from app.services.resume_intelligence.parser import ResumeParser
from app.services.storage import FileStorage, get_file_storage
from app.services.storage.uploads import read_bounded

router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.get("", response_model=list[ResumeRead])
def list_resumes(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ResumeRead]:
    return [
        ResumeRead.model_validate(r) for r in resume_service.list_resumes_for_user(db, current_user)
    ]


@router.get("/current", response_model=ResumeRead | None)
def get_current_resume(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ResumeRead | None:
    resume = resume_service.get_resume_for_user(db, current_user)
    return ResumeRead.model_validate(resume) if resume else None


@router.post("", response_model=ResumeRead, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> ResumeRead:
    # Read one byte past the limit at most: enough to tell "too large" (V0.13).
    content = await read_bounded(file, settings.MAX_RESUME_SIZE_MB * 1024 * 1024)
    resume = resume_service.upload_resume(
        db,
        current_user,
        storage,
        file_name=file.filename or "resume",
        mime_type=file.content_type or "application/octet-stream",
        file_size=len(content),
        content=io.BytesIO(content),
    )
    return ResumeRead.model_validate(resume)


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> None:
    resume_service.delete_resume(db, current_user, storage, resume_id)


# --- Resume Intelligence (V0.2) ---------------------------------------------


@router.post(
    "/{resume_id}/process",
    response_model=ResumeProcessResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": ResumeProcessResponse}},
)
def process_resume(
    resume_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    request: ResumeProcessRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
    parser: ResumeParser = Depends(get_resume_parser),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> ResumeProcessResponse | JSONResponse:
    """Start analyzing a resume. Returns immediately (202); poll
    `GET /resumes/{id}/profile` for progress. Returns 200 without starting
    new work when this exact file was already analyzed by the current parser
    version, unless `force` is set ("Reprocess Resume")."""
    force = request.force if request is not None else False
    start = processing_service.start_processing(
        db, current_user, storage, parser, resume_id, force=force
    )
    if start.reused:
        body = ResumeProcessResponse(
            resume=ResumeRead.model_validate(start.resume),
            profile_id=start.profile.id,
            reused=True,
            message="This resume was already analyzed. Showing the existing result.",
        )
        return JSONResponse(status_code=status.HTTP_200_OK, content=body.model_dump(mode="json"))

    background_tasks.add_task(
        processing_service.run_processing,
        start.profile.id,
        session_factory=session_factory,
        storage=storage,
        parser=parser,
    )
    return ResumeProcessResponse(
        resume=ResumeRead.model_validate(start.resume),
        profile_id=start.profile.id,
        reused=False,
        message="Resume analysis started.",
    )


@router.get("/{resume_id}/profile", response_model=ResumeIntelligenceRead)
def get_resume_profile(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeIntelligenceRead:
    resume = processing_service.get_owned_resume(db, current_user, resume_id)
    latest = processing_service.get_latest_attempt(db, resume)
    profile = processing_service.get_current_profile(db, resume)
    return ResumeIntelligenceRead(
        resume=ResumeRead.model_validate(resume),
        latest_attempt=(
            ProcessingAttemptRead(
                profile_id=latest.id,
                status=latest.status.value,
                error_code=latest.error_code,
                error_message=latest.error_message,
                parser_version=latest.parser_version,
                started_at=latest.started_at,
                processed_at=latest.processed_at,
            )
            if latest
            else None
        ),
        is_processing=processing_service.is_processing(resume),
        profile=review_service.build_profile_read(profile, current_user) if profile else None,
    )


@router.get("/{resume_id}/profile/source", response_model=ResumeSourceRead)
def get_resume_profile_source(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeSourceRead:
    """The extracted text the current profile was built from ("View source")."""
    resume = processing_service.get_owned_resume(db, current_user, resume_id)
    profile = processing_service.get_current_profile(db, resume)
    if profile is None or profile.extracted_text is None:
        raise NotFoundError("This resume hasn't been analyzed yet.")
    return ResumeSourceRead(
        profile_id=profile.id,
        extraction_method=profile.extraction_method,
        page_count=profile.page_count,
        text=profile.extracted_text,
    )


@router.patch("/{resume_id}/profile/items/{item_id}", response_model=ResumeProfileRead)
def review_profile_item(
    resume_id: uuid.UUID,
    item_id: str,
    request: ReviewItemRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeProfileRead:
    profile = review_service.review_item(db, current_user, resume_id, item_id, request)
    return review_service.build_profile_read(profile, current_user)


@router.post("/{resume_id}/profile/accept-pending", response_model=ResumeProfileRead)
def accept_pending_items(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeProfileRead:
    profile = review_service.accept_pending(db, current_user, resume_id)
    return review_service.build_profile_read(profile, current_user)


@router.post("/{resume_id}/profile/confirm", response_model=ResumeProfileRead)
def confirm_resume_profile(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeProfileRead:
    profile = review_service.confirm_profile(db, current_user, resume_id)
    return review_service.build_profile_read(profile, current_user)


@router.post("/{resume_id}/profile/reopen", response_model=ResumeProfileRead)
def reopen_resume_profile(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeProfileRead:
    profile = review_service.reopen_profile(db, current_user, resume_id)
    return review_service.build_profile_read(profile, current_user)
