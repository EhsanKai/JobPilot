import uuid
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationAppError
from app.models.resume import Resume, ResumeStatus
from app.models.user import User
from app.services.storage.base import FileStorage

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ALLOWED_EXTENSIONS = {".pdf", ".docx"}


def _validate_upload(file_name: str, mime_type: str, file_size: int) -> None:
    extension = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if extension not in ALLOWED_EXTENSIONS or mime_type not in ALLOWED_MIME_TYPES:
        raise ValidationAppError(
            "Only PDF and DOCX resumes are supported.",
            {"file_name": file_name, "mime_type": mime_type},
        )

    max_bytes = settings.MAX_RESUME_SIZE_MB * 1024 * 1024
    if file_size > max_bytes:
        raise ValidationAppError(
            f"Resume exceeds the maximum size of {settings.MAX_RESUME_SIZE_MB}MB.",
            {"file_size": file_size, "max_bytes": max_bytes},
        )


def get_resume_for_user(db: Session, user: User) -> Resume | None:
    return db.execute(
        select(Resume).where(Resume.user_id == user.id).order_by(Resume.created_at.desc())
    ).scalars().first()


def upload_resume(
    db: Session,
    user: User,
    storage: FileStorage,
    *,
    file_name: str,
    mime_type: str,
    file_size: int,
    content: BinaryIO,
) -> Resume:
    _validate_upload(file_name, mime_type, file_size)

    # Replace any existing resume: V0 supports a single active resume per user.
    existing = get_resume_for_user(db, user)
    if existing is not None:
        storage.delete(reference=existing.storage_reference)
        db.delete(existing)
        db.flush()

    extension = "." + file_name.rsplit(".", 1)[-1].lower()
    storage_key = f"{user.id}/{uuid.uuid4()}{extension}"
    stored_file = storage.save(key=storage_key, content=content)

    resume = Resume(
        user_id=user.id,
        file_name=file_name,
        storage_reference=stored_file.reference,
        mime_type=mime_type,
        file_size=stored_file.size,
        status=ResumeStatus.UPLOADED,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def delete_resume(db: Session, user: User, storage: FileStorage, resume_id: uuid.UUID) -> None:
    resume = db.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id)
    ).scalar_one_or_none()
    if resume is None:
        raise NotFoundError("Resume not found.")

    storage.delete(reference=resume.storage_reference)
    db.delete(resume)
    db.commit()
