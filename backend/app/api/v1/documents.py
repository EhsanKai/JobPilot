"""Tailored CV & Anschreiben endpoints (V0.8), their humanize / edit actions and the
CV photo (V0.9).

Documents belong to an application and therefore to the user who owns its
`UserJob`: another user's application or document -- or one that doesn't
exist -- is a 404. Generating takes no body: language, resume, salary
decision and everything else a document depends on is the application's own
saved data. Every POST creates a *new* version; nothing is ever overwritten.
"""

import uuid

from fastapi import APIRouter, Depends, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.ai.provider import AIProvider
from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.generated_document import GeneratedDocumentType
from app.models.user import User
from app.schemas.generated_documents import (
    DocumentEditRequest,
    DocumentListResponse,
    GeneratedDocumentRead,
    GenerateDocumentRequest,
    HumanizeRequest,
    PhotoRead,
)
from app.services.documents import photos, service
from app.services.storage import FileStorage, get_file_storage

router = APIRouter(prefix="/applications/{application_id}/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
def list_documents(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    """Every generation attempt for your application, newest first, and what
    is needed before the next one."""
    return service.list_documents(db, current_user, application_id)


@router.get("/{document_id}", response_model=GeneratedDocumentRead)
def get_document(
    application_id: uuid.UUID,
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GeneratedDocumentRead:
    return service.get_document(db, current_user, application_id, document_id)


@router.post("/cv", response_model=GeneratedDocumentRead, status_code=status.HTTP_201_CREATED)
async def generate_cv(
    application_id: uuid.UUID,
    request: GenerateDocumentRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> GeneratedDocumentRead:
    """Generate a new version of the tailored CV. Only a document that passes
    truth validation against your confirmed resume is saved."""
    return await service.generate(
        db, current_user, application_id, GeneratedDocumentType.TAILORED_CV, provider, request
    )


@router.post(
    "/cover-letter", response_model=GeneratedDocumentRead, status_code=status.HTTP_201_CREATED
)
async def generate_cover_letter(
    application_id: uuid.UUID,
    request: GenerateDocumentRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> GeneratedDocumentRead:
    """Generate a new version of the Anschreiben, in the application language."""
    return await service.generate(
        db, current_user, application_id, GeneratedDocumentType.COVER_LETTER, provider, request
    )


@router.post(
    "/cover-letter/humanize",
    response_model=GeneratedDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def humanize_cover_letter(
    application_id: uuid.UUID,
    request: HumanizeRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> GeneratedDocumentRead:
    """Reword the current Anschreiben (or `base_document_id`) for a natural tone,
    as a NEW version. It goes through the same truth validation as a generated letter;
    if it fails, nothing is saved and the letter is untouched."""
    return await service.humanize(db, current_user, application_id, provider, request)


@router.post(
    "/{document_id}/edit",
    response_model=GeneratedDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
def edit_document(
    application_id: uuid.UUID,
    document_id: uuid.UUID,
    request: DocumentEditRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GeneratedDocumentRead:
    """Save your edits of a version as a new version (`user_edited`). The edited
    version is never changed, and no AI is involved."""
    return service.edit_document(db, current_user, application_id, document_id, request)


# --- The user's own photo for the CV (V0.9) -----------------------------------------------------

photo_router = APIRouter(prefix="/applications/{application_id}/cv-photo", tags=["documents"])


@photo_router.put("", response_model=PhotoRead)
async def put_photo(
    application_id: uuid.UUID,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> PhotoRead:
    """Add or replace your photo (JPEG or PNG). Only you ever change it: no AI does."""
    content = await file.read()
    return photos.save_photo(
        db,
        current_user,
        storage,
        application_id,
        file_name=file.filename or "photo",
        content=content,
    )


@photo_router.get("")
def get_photo(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> Response:
    """The photo file, exactly as uploaded."""
    content, content_type = photos.photo_bytes(db, current_user, storage, application_id)
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@photo_router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_photo(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> None:
    """Remove your photo."""
    photos.delete_photo(db, current_user, storage, application_id)
