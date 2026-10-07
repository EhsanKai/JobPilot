"""The user's photo for an application's CV (V0.9).

A photo is a user-owned asset, kept apart from every generated document:

* only the user adds, replaces or removes it (`PUT` / `DELETE` of their own
  application) -- no AI pipeline reads, writes, describes or replaces it, and
  none of the document schemas has a field for one;
* regenerating, humanizing or editing a document leaves it as it is;
* the stored file is what was uploaded, unmodified (no re-encoding, no cropping).

Accepted: JPEG and PNG, up to `MAX_PHOTO_SIZE_MB`. The type is decided by the
file's own signature, not by the name or the declared content type, which
the client controls.
"""

import io
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationAppError
from app.models.application_photo import ApplicationPhoto
from app.models.user import User
from app.schemas.generated_documents import PhotoRead
from app.services.applications.service import get_owned_application
from app.services.storage.base import FileStorage
from app.services.storage.uploads import safe_file_name

_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
)


def _sniff(content: bytes) -> tuple[str, str] | None:
    for signature, content_type, extension in _SIGNATURES:
        if content.startswith(signature):
            return content_type, extension
    return None


def _row(db: Session, application_id: uuid.UUID, user: User) -> ApplicationPhoto | None:
    return db.scalar(
        select(ApplicationPhoto).where(
            ApplicationPhoto.application_id == application_id,
            ApplicationPhoto.user_id == user.id,
        )
    )


def _read(row: ApplicationPhoto) -> PhotoRead:
    return PhotoRead(
        content_type=row.content_type,
        file_name=row.file_name,
        size_bytes=row.size_bytes,
        updated_at=row.updated_at,
    )


def read_photo(db: Session, application_id: uuid.UUID, user: User) -> PhotoRead | None:
    row = _row(db, application_id, user)
    return _read(row) if row is not None else None


def save_photo(
    db: Session,
    user: User,
    storage: FileStorage,
    application_id: uuid.UUID,
    *,
    file_name: str,
    content: bytes,
) -> PhotoRead:
    """Add or replace the user's photo. A file that is not a JPEG or PNG, is empty
    or is too large is refused and nothing changes."""
    application = get_owned_application(db, user, application_id)
    kind = _sniff(content)
    if not content or kind is None:
        raise ValidationAppError("The photo must be a JPEG or PNG image.")
    limit = settings.MAX_PHOTO_SIZE_MB * 1024 * 1024
    if len(content) > limit:
        raise ValidationAppError(
            f"The photo exceeds the maximum size of {settings.MAX_PHOTO_SIZE_MB}MB.",
            {"max_bytes": limit, "size_bytes": len(content)},
        )
    content_type, extension = kind
    reference = f"{user.id}/photos/{uuid.uuid4()}.{extension}"
    stored = storage.save(key=reference, content=io.BytesIO(content))
    row = _row(db, application.id, user)
    previous = row.storage_reference if row is not None else None
    if row is None:
        row = ApplicationPhoto(application_id=application.id, user_id=user.id)
        db.add(row)
    row.storage_reference = stored.reference
    row.content_type = content_type
    row.file_name = safe_file_name(file_name, f"photo.{extension}")
    row.size_bytes = stored.size
    db.commit()
    db.refresh(row)
    if previous is not None:
        storage.delete(reference=previous)
    return _read(row)


def photo_bytes(
    db: Session, user: User, storage: FileStorage, application_id: uuid.UUID
) -> tuple[bytes, str]:
    application = get_owned_application(db, user, application_id)
    row = _row(db, application.id, user)
    if row is None:
        raise NotFoundError("This application has no photo.")
    try:
        return storage.read(reference=row.storage_reference), row.content_type
    except FileNotFoundError:
        raise NotFoundError("This application has no photo.") from None


def delete_photo(db: Session, user: User, storage: FileStorage, application_id: uuid.UUID) -> None:
    """Remove the photo. Removing one that does not exist is not an error."""
    application = get_owned_application(db, user, application_id)
    row = _row(db, application.id, user)
    if row is None:
        return
    reference = row.storage_reference
    db.delete(row)
    db.commit()
    storage.delete(reference=reference)
