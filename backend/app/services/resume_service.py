import hashlib
import io
import uuid
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.models.resume import Resume, ResumeStatus
from app.models.user import User
from app.services.storage import uploads
from app.services.storage.base import FileStorage

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ALLOWED_EXTENSIONS = {".pdf", ".docx"}


_EXTENSION_FOR = {uploads.PDF: ".pdf", uploads.DOCX: ".docx"}


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


def _verified_type(file_name: str, raw: bytes) -> str:
    """The file's real type, from its bytes (V0.13). The browser's MIME type and the
    extension are claims; a file whose content is not the PDF / DOCX its name says is
    refused (an executable or HTML page renamed to .pdf, a ZIP renamed to .docx)."""
    actual = uploads.sniff_document(raw)
    extension = "." + file_name.rsplit(".", 1)[-1].lower()
    if actual is None or _EXTENSION_FOR[actual] != extension:
        raise ValidationAppError(
            "This file is not a valid PDF or DOCX document.",
            {"file_name": file_name},
        )
    return actual


def get_resume_for_user(db: Session, user: User) -> Resume | None:
    """The user's current (most recently uploaded) resume version."""
    return db.execute(
        select(Resume)
        .where(Resume.user_id == user.id)
        .order_by(Resume.version_number.desc(), Resume.created_at.desc())
    ).scalars().first()


def list_resumes_for_user(db: Session, user: User) -> list[Resume]:
    """All of the user's resume versions, newest first."""
    return list(
        db.execute(
            select(Resume)
            .where(Resume.user_id == user.id)
            .order_by(Resume.version_number.desc(), Resume.created_at.desc())
        )
        .scalars()
        .all()
    )


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
    file_name = uploads.safe_file_name(file_name, "resume")
    _validate_upload(file_name, mime_type, file_size)

    # Since V0.2 a new upload becomes a new *version*: previous versions (and
    # anything extracted from them) are kept until the user deletes them, so
    # the original file is never lost or overwritten.
    raw = content.read()
    mime_type = _verified_type(file_name, raw)
    file_hash = hashlib.sha256(raw).hexdigest()
    next_version = (
        db.execute(
            select(func.max(Resume.version_number)).where(Resume.user_id == user.id)
        ).scalar_one_or_none()
        or 0
    ) + 1

    extension = "." + file_name.rsplit(".", 1)[-1].lower()
    storage_key = f"{user.id}/{uuid.uuid4()}{extension}"
    stored_file = storage.save(key=storage_key, content=io.BytesIO(raw))

    resume = Resume(
        user_id=user.id,
        version_number=next_version,
        file_name=file_name,
        storage_reference=stored_file.reference,
        mime_type=mime_type,
        file_size=stored_file.size,
        file_hash=file_hash,
        status=ResumeStatus.UPLOADED,
    )
    db.add(resume)
    try:
        db.commit()
    except IntegrityError:
        # Two uploads raced for the same version number.
        db.rollback()
        storage.delete(reference=stored_file.reference)
        raise ConflictError("Another upload is in progress. Please try again.") from None
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
