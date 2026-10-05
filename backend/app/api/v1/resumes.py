import io
import uuid

from fastapi import APIRouter, Depends, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.resume import ResumeRead
from app.services import resume_service
from app.services.storage import FileStorage, get_file_storage

router = APIRouter(prefix="/resumes", tags=["resumes"])


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
    content = await file.read()
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
