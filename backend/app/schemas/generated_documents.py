"""API schemas for tailored documents (V0.8).

Reads only: a document is created by `POST .../documents/cv` and
`.../cover-letter`, which take no body -- everything a generation depends on
(language, salary decision, resume) is the application's own saved data, so a
client cannot smuggle a language, a fact or a salary into a request. Prompts
and provider credentials are never part of a response.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.generated_document import GeneratedDocumentStatus, GeneratedDocumentType
from app.schemas.document_content import (
    CoverLetterContent,
    SourceFact,
    TailoredCvContent,
    ValidationReport,
)

#: "not_run": still generating, or failed before validation could run.
ValidationStatusRead = Literal["passed", "failed", "not_run"]


class DocumentBlocker(BaseModel):
    """Something the user must do before a document can be generated."""

    code: str
    message: str


class DocumentReadiness(BaseModel):
    can_generate: bool
    blockers: list[DocumentBlocker]


class GeneratedDocumentSummary(BaseModel):
    """One generation attempt and its provenance. Never contains content."""

    id: uuid.UUID
    application_id: uuid.UUID
    document_type: GeneratedDocumentType
    status: GeneratedDocumentStatus
    #: Among the validated documents of this application and type; null for
    #: an attempt that was not validated.
    version_number: int | None
    #: The newest validated document of its type.
    is_current: bool
    #: Current, but generated from a resume / application data / match that has
    #: changed since: regenerate to bring it up to date.
    is_outdated: bool
    language: str
    resume_id: uuid.UUID | None
    resume_version_number: int | None
    application_context_snapshot_id: uuid.UUID
    input_hash: str
    prompt_version: str
    ai_provider: str
    ai_model: str | None
    validation_status: ValidationStatusRead
    violation_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class GeneratedDocumentRead(GeneratedDocumentSummary):
    content: TailoredCvContent | CoverLetterContent | None
    validation: ValidationReport | None
    #: The facts the content cites, by id.
    source_facts: dict[str, SourceFact]


class DocumentListResponse(BaseModel):
    """Every attempt, newest first, and what the user needs to do before the
    next generation of each type."""

    items: list[GeneratedDocumentSummary]
    tailored_cv: DocumentReadiness
    cover_letter: DocumentReadiness
