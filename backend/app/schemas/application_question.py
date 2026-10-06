"""API schemas for application questions and answers (V0.9).

Requests forbid unknown keys, so a client can never send `user_id`,
`application_id`, a normalized text, a hash, a status, a suggestion, a
validation result or provenance: those are server-owned. A user's own
answer arrives only through `ManualAnswerRequest`; an AI suggestion is never
accepted from a client.
"""

import json
import re
import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.application_question import (
    CHOICE_TYPES,
    AnswerSource,
    AnswerStatus,
    GenerationMethod,
    GenerationOutcome,
    QuestionType,
)
from app.schemas.document_content import SourceFact, ValidationReport

MAX_QUESTION_CHARS = 2000
MAX_ANSWER_LIMIT = 100_000
MAX_OPTIONS = 100
MAX_OPTION_CHARS = 300
MAX_USER_ANSWER_CHARS = 20_000
_MAX_METADATA_BYTES = 4096
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,49}$")


def _clean_options(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    cleaned = [" ".join(option.split()) for option in value]
    if any(not option for option in cleaned):
        raise ValueError("options may not be empty")
    if any(len(option) > MAX_OPTION_CHARS for option in cleaned):
        raise ValueError(f"an option may have at most {MAX_OPTION_CHARS} characters")
    folded = [option.casefold() for option in cleaned]
    if len(set(folded)) != len(folded):
        raise ValueError("options must be unique")
    if len(cleaned) > MAX_OPTIONS:
        raise ValueError(f"at most {MAX_OPTIONS} options are supported")
    return cleaned


def check_type_and_options(question_type: QuestionType, options: list[str] | None) -> None:
    """Which types take options: choices need them, a yes/no may label them,
    nothing else has any."""
    if question_type in CHOICE_TYPES:
        if not options:
            raise ValueError("a choice question needs at least one option")
    elif question_type is QuestionType.YES_NO:
        if options is not None and len(options) != 2:
            raise ValueError("a yes/no question has exactly two options (yes, then no) or none")
    elif options is not None:
        raise ValueError("only choice and yes/no questions have options")


class _QuestionFields(BaseModel):
    @field_validator("options", mode="before", check_fields=False)
    @classmethod
    def _options(cls, value: Any) -> Any:
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return _clean_options(value)
        return value

    @field_validator("source", mode="after", check_fields=False)
    @classmethod
    def _source(cls, value: str | None) -> str | None:
        if value is not None and not _SOURCE.match(value):
            raise ValueError("source must be a short lower-case label such as 'manual'")
        return value

    @field_validator("source_metadata", mode="after", check_fields=False)
    @classmethod
    def _metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, default=str)) > _MAX_METADATA_BYTES:
            raise ValueError("source_metadata is too large")
        return value


class QuestionCreate(_QuestionFields):
    """A question entered by the user (V0.9) or, from V0.10, discovered in a form."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question_text: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    question_type: QuestionType = QuestionType.FREE_TEXT
    required: bool = False
    max_length: int | None = Field(default=None, ge=1, le=MAX_ANSWER_LIMIT)
    options: list[str] | None = None
    external_key: str | None = Field(default=None, min_length=1, max_length=255)
    field_name: str | None = Field(default=None, min_length=1, max_length=255)
    source: str | None = Field(default="manual", max_length=50)
    source_metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _types(self) -> Self:
        check_type_and_options(self.question_type, self.options)
        return self


class QuestionUpdate(_QuestionFields):
    """Omitted fields stay as they are; `null` clears an optional one."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question_text: str | None = Field(default=None, min_length=1, max_length=MAX_QUESTION_CHARS)
    question_type: QuestionType | None = None
    required: bool | None = None
    max_length: int | None = Field(default=None, ge=1, le=MAX_ANSWER_LIMIT)
    options: list[str] | None = None
    external_key: str | None = Field(default=None, min_length=1, max_length=255)
    field_name: str | None = Field(default=None, min_length=1, max_length=255)
    source: str | None = Field(default=None, max_length=50)
    source_metadata: dict[str, Any] | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100_000)

    @model_validator(mode="after")
    def _not_null_columns(self) -> Self:
        for name in ("question_text", "question_type", "required", "sort_order"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared")
        return self


class ManualAnswerRequest(BaseModel):
    """The user's own answer. Text for most questions; `selected_options` for a
    choice question (every option must be one the question lists)."""

    model_config = ConfigDict(extra="forbid")

    answer: str | None = Field(default=None, max_length=MAX_USER_ANSWER_CHARS)
    selected_options: list[str] | None = Field(default=None, max_length=MAX_OPTIONS)
    #: The user edited the current suggestion rather than writing from scratch.
    based_on_suggestion: bool = False

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        has_text = self.answer is not None and bool(self.answer.strip())
        has_options = bool(self.selected_options)
        if has_text == has_options:
            raise ValueError("give either an answer or selected_options")
        return self


class AcceptRequest(BaseModel):
    """Accepting copies the server-held suggestion: the client never sends the
    answer. `replace_existing` confirms replacing a final answer that differs."""

    model_config = ConfigDict(extra="forbid")

    replace_existing: bool = False


class GenerateRequest(BaseModel):
    """`acknowledge_existing_answer` confirms the user knows they already have
    an answer: a new suggestion never changes it, but is only produced when asked
    for knowingly."""

    model_config = ConfigDict(extra="forbid")

    acknowledge_existing_answer: bool = False


# --- Reads -----------------------------------------------------------------------------------


class AnswerProvenance(BaseModel):
    """How the current suggestion was produced. Never contains a prompt."""

    generation_id: uuid.UUID
    method: GenerationMethod
    prompt_version: str | None
    prompt_hash: str | None
    ai_provider: str | None
    ai_model: str | None
    resume_id: uuid.UUID | None
    resume_version_number: int | None
    resume_profile_id: uuid.UUID | None
    application_context_snapshot_id: uuid.UUID | None
    input_hash: str
    question_fingerprint: str
    confidence: str | None
    max_length: int | None
    generated_at: datetime
    validation_status: Literal["passed", "failed", "not_run", "not_applicable"]


class AnswerRead(BaseModel):
    id: uuid.UUID
    status: AnswerStatus
    #: Where the *final* answer came from; null while there is none.
    answer_source: AnswerSource | None
    # --- The suggestion (AI or deterministic): never edited by the user ----------------------
    ai_suggestion: str | None
    ai_selected_options: list[str] | None
    ai_source_fact_ids: list[str]
    ai_provenance: AnswerProvenance | None
    ai_generation_id: uuid.UUID | None
    #: "DETERMINISTIC" (built from the application's own data) or "AI".
    suggestion_method: GenerationMethod | None
    suggestion_dismissed: bool
    #: The question changed since the suggestion was made.
    suggestion_outdated: bool
    #: The facts the suggestion cites, by id.
    source_facts: dict[str, SourceFact]
    needs_input_reason: str | None
    needs_input_message: str | None
    # --- The user's answer: never edited by the AI --------------------------------------------
    final_answer: str | None
    final_selected_options: list[str] | None
    basis_generation_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class QuestionRead(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    question_text: str
    normalized_question_text: str
    question_hash: str
    question_type: QuestionType
    required: bool
    max_length: int | None
    options: list[str] | None
    external_key: str | None
    field_name: str | None
    source: str | None
    source_metadata: dict[str, Any] | None
    sort_order: int
    answer: AnswerRead
    created_at: datetime
    updated_at: datetime


class QuestionBlocker(BaseModel):
    code: str
    message: str


class QuestionAiReadiness(BaseModel):
    """Whether an AI suggestion (as opposed to a deterministic one) can be
    generated now. Answering manually is never blocked."""

    can_generate: bool
    blockers: list[QuestionBlocker]


class QuestionListResponse(BaseModel):
    items: list[QuestionRead]
    ai: QuestionAiReadiness


class GenerationRead(BaseModel):
    """One attempt (also a rejected or failed one), for the audit trail."""

    id: uuid.UUID
    question_id: uuid.UUID
    method: GenerationMethod
    outcome: GenerationOutcome
    needs_input_reason: str | None
    suggestion: str | None
    selected_options: list[str] | None
    source_fact_ids: list[str]
    source_facts: dict[str, SourceFact]
    confidence: str | None
    validation: ValidationReport | None
    error_code: str | None
    error_message: str | None
    question_fingerprint: str
    input_hash: str
    max_length: int | None
    prompt_version: str | None
    prompt_hash: str | None
    ai_provider: str | None
    ai_model: str | None
    resume_id: uuid.UUID | None
    resume_version_number: int | None
    application_context_snapshot_id: uuid.UUID | None
    created_at: datetime


class GenerationListResponse(BaseModel):
    items: list[GenerationRead]
