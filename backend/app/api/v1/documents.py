"""Tailored CV & Anschreiben endpoints (V0.8).

Documents belong to an application and therefore to the user who owns its
`UserJob`: another user's application or document -- or one that doesn't
exist -- is a 404. Generating takes no body: language, resume, salary
decision and everything else a document depends on is the application's own
saved data. Every POST creates a *new* version; nothing is ever overwritten.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.ai.provider import AIProvider
from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.generated_document import GeneratedDocumentType
from app.models.user import User
from app.schemas.generated_documents import DocumentListResponse, GeneratedDocumentRead
from app.services.documents import service

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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> GeneratedDocumentRead:
    """Generate a new version of the tailored CV. Only a document that passes
    truth validation against your confirmed resume is saved."""
    return await service.generate(
        db, current_user, application_id, GeneratedDocumentType.TAILORED_CV, provider
    )


@router.post(
    "/cover-letter", response_model=GeneratedDocumentRead, status_code=status.HTTP_201_CREATED
)
async def generate_cover_letter(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> GeneratedDocumentRead:
    """Generate a new version of the Anschreiben, in the application language."""
    return await service.generate(
        db, current_user, application_id, GeneratedDocumentType.COVER_LETTER, provider
    )
