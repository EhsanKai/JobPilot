"""API schemas for tailored documents (V0.8).

Reads only: a document is created by `POST .../documents/cv` and
`.../cover-letter`, which take no body -- everything a generation depends on
(language, salary decision, resume) is the application's own saved data, so a
client cannot smuggle a language, a fact or a salary into a request. Prompts
and provider credentials are never part of a response.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.generated_document import (
    GeneratedDocumentOrigin,
    GeneratedDocumentStatus,
    GeneratedDocumentType,
)
from app.schemas.document_ai import LetterRole
from app.schemas.document_content import (
    CoverLetterContent,
    SourceFact,
    TailoredCvContent,
    ValidationReport,
)

#: "not_run": still generating, or failed before validation could run.
#: "user_authored" (V0.9): a version the user saved from the editor -- their own
#: words, never checked and never presented as checked.
ValidationStatusRead = Literal["passed", "failed", "not_run", "user_authored"]


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
    prompt_hash: str | None
    #: ai | humanized | user_edited (V0.9): whose words this version holds.
    origin: GeneratedDocumentOrigin
    #: The version a humanized / edited one was made from.
    base_document_id: uuid.UUID | None
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
    #: The user's own photo for the CV (V0.9), if they added one.
    photo: "PhotoRead | None" = None


# --- V0.9: photo, regeneration safety, Humanize and the editor --------------------------------


class PhotoRead(BaseModel):
    """The user's own photo for the CV. A user-owned asset: never written by the AI."""

    content_type: str
    file_name: str
    size_bytes: int
    updated_at: datetime


class GenerateDocumentRequest(BaseModel):
    """Optional body of a generation. Nothing a document depends on can be sent
    (language, facts, salary): any other field is ignored, exactly as in V0.8, where
    generation took no body at all. The one thing it carries is the confirmation
    that the user knows the current version holds their own edits, which a new
    generation will not delete but will supersede as the current version."""

    model_config = ConfigDict(extra="ignore")

    acknowledge_edited_current: bool = False


class HumanizeRequest(GenerateDocumentRequest):
    model_config = ConfigDict(extra="forbid")

    #: The letter version to reword; the current one when omitted.
    base_document_id: uuid.UUID | None = None


_Line = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]
_Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
MAX_EDIT_PARAGRAPHS = 20
MAX_EDIT_BULLETS = 30


class CvHeaderEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: _Short | None = None
    email: _Short | None = None
    phone: _Short | None = None
    location: _Short | None = None
    links: list[_Short] = Field(default_factory=list, max_length=10)


class CvExperienceEdit(BaseModel):
    """The bullets of one role. Employer, title and dates are the resume's: they
    are changed in the resume review, not here."""

    model_config = ConfigDict(extra="forbid")

    source_fact_id: str = Field(max_length=50)
    bullets: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    ] = Field(max_length=MAX_EDIT_BULLETS)


class CvEditRequest(BaseModel):
    """The user's edited CV text (V0.9)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tailored_cv"]
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    header: CvHeaderEdit
    experience: list[CvExperienceEdit] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _roles_once(self) -> "CvEditRequest":
        ids = [entry.source_fact_id for entry in self.experience]
        if len(set(ids)) != len(ids):
            raise ValueError("a role appears more than once")
        return self


class LetterParagraphEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: LetterRole
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class LetterEditRequest(BaseModel):
    """The user's edited Anschreiben text (V0.9). The sender and the recipient are
    the application's; everything written is the user's to change."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["cover_letter"]
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    salutation: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]
    paragraphs: list[LetterParagraphEdit] = Field(min_length=1, max_length=MAX_EDIT_PARAGRAPHS)
    salary_statement: _Line | None = None
    closing_formula: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]
    signature: _Short | None = None


DocumentEditRequest = Annotated[CvEditRequest | LetterEditRequest, Field(discriminator="kind")]
