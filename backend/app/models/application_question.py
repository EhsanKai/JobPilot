"""Application questions, the user's answers, and the AI attempts (V0.9).

    Application
     +-- ApplicationQuestion (many)         what the employer asked; entered manually in V0.9,
          |                                  discovered in a browser from V0.10 on
          +-- ApplicationQuestionAnswer      exactly one per question: the *current* state
          +-- ApplicationQuestionGeneration  every attempt to suggest an answer (write-once)

**Human Control at Every Stage.** AI output and user-authored content are kept
apart on purpose:

- `ai_*` columns of the answer hold the *current suggestion* (an AI answer, or
  a deterministic one built from the application's own saved data) with the
  facts it rests on and its provenance. Nothing a user does overwrites them.
- `final_*` columns hold *the user's answer* -- what they accepted or wrote.
  Nothing the AI does overwrites them: a new suggestion only ever replaces the
  previous *suggestion*, never the final answer.
- Every attempt is a write-once `ApplicationQuestionGeneration` row, so what
  the AI suggested earlier -- including a suggestion the user later edited --
  stays auditable (`basis_generation_id` points at the attempt the final
  answer came from).

Ownership: every row carries `user_id`, and composite foreign keys
(`(application_id, user_id)`, `(question_id, user_id)`) make it impossible to
store a question, answer or attempt for someone else's application.

The question model is source-neutral: `source`, `external_key`, `field_name`
and `source_metadata` are filled by whoever created the question ("manual" in
V0.9; a browser-discovered form field in V0.10) and nothing in the model
names a particular website or ATS.
"""

import enum
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, ImmutableMixin, TimestampMixin, require_sha256_hex
from app.schemas.document_content import SourceFact, ValidationReport

if TYPE_CHECKING:
    from app.models.application import Application


class QuestionType(str, enum.Enum):
    """The kind of answer a question expects. Values equal names (as in the V0.9 spec)."""

    FREE_TEXT = "FREE_TEXT"
    YES_NO = "YES_NO"
    SINGLE_CHOICE = "SINGLE_CHOICE"
    MULTI_CHOICE = "MULTI_CHOICE"
    NUMERIC = "NUMERIC"
    DATE = "DATE"
    SALARY = "SALARY"
    #: The type is not known (e.g. a discovered field without a clear input type).
    UNKNOWN = "UNKNOWN"


CHOICE_TYPES = frozenset({QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE})


class AnswerSource(str, enum.Enum):
    """Where the *final* answer (or, until one exists, the suggestion) came from."""

    #: Built from the application's own structured data, without AI.
    DETERMINISTIC = "DETERMINISTIC"
    #: A validated AI suggestion the user accepted unchanged.
    AI = "AI"
    #: Written by the user.
    USER = "USER"
    #: A suggestion the user edited.
    HYBRID = "HYBRID"


class AnswerStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    #: The latest attempt found the trusted facts insufficient: the user must answer.
    NEEDS_INPUT = "NEEDS_INPUT"
    #: A validated suggestion exists and the user has not decided yet.
    SUGGESTED = "SUGGESTED"
    #: The user accepted the suggestion unchanged.
    ACCEPTED = "ACCEPTED"
    #: The user wrote or edited the answer.
    USER_EDITED = "USER_EDITED"
    #: The user rejected the suggestion and has no answer yet.
    REJECTED = "REJECTED"


FINAL_STATUSES = frozenset({AnswerStatus.ACCEPTED, AnswerStatus.USER_EDITED})


class GenerationMethod(str, enum.Enum):
    DETERMINISTIC = "DETERMINISTIC"
    AI = "AI"


class GenerationOutcome(str, enum.Enum):
    #: A validated suggestion was produced.
    SUGGESTED = "SUGGESTED"
    #: The trusted facts do not support an answer. Not an error.
    NEEDS_INPUT = "NEEDS_INPUT"
    #: The AI's output failed validation and was discarded.
    REJECTED = "REJECTED"
    #: The AI call failed.
    FAILED = "FAILED"


class ApplicationQuestion(Base, TimestampMixin):
    """One question the employer asks as part of an application."""

    __tablename__ = "application_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "user_id"],
            ["applications.id", "applications.user_id"],
            ondelete="CASCADE",
            name="fk_application_questions_application",
        ),
        # The target of the answer's and the attempts' composite ownership keys.
        UniqueConstraint("id", "user_id", name="uq_application_questions_id_user_id"),
        # A discovered form field is imported once per application.
        Index(
            "uq_application_questions_external_key",
            "application_id",
            "external_key",
            unique=True,
            postgresql_where=text("external_key IS NOT NULL"),
        ),
        CheckConstraint(
            "max_length IS NULL OR max_length >= 1", name="ck_application_questions_max_length"
        ),
        CheckConstraint("sort_order >= 0", name="ck_application_questions_sort_order"),
        CheckConstraint(
            "options IS NULL OR jsonb_typeof(options) = 'array'",
            name="ck_application_questions_options_array",
        ),
        CheckConstraint(
            "question_type NOT IN ('SINGLE_CHOICE', 'MULTI_CHOICE') OR options IS NOT NULL",
            name="ck_application_questions_choice_has_options",
        ),
        Index(
            "ix_application_questions_application_sort",
            "application_id",
            "sort_order",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Denormalized owner (see the composite foreign key above).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The question exactly as the employer wrote it. Never rewritten.
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Server-computed comparison form (case, whitespace, trailing "?" / "*").
    normalized_question_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: SHA-256 of `normalized_question_text`: an identity for the same question
    #: asked again, regardless of where it was discovered.
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    question_type: Mapped[QuestionType] = mapped_column(
        Enum(QuestionType, name="application_question_type"),
        default=QuestionType.FREE_TEXT,
        nullable=False,
    )
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Character limit of the answer; never exceeded and never truncated to.
    max_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The choices of a choice (or labelled yes/no) question, as shown.
    options: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # --- Source-neutral metadata (V0.10 fills these for browser-discovered fields) ---------------
    #: A stable key of the field in whatever produced the question.
    external_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The name of the input as the form calls it.
    field_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Who created the question: "manual", or a neutral label of a discoverer.
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: Opaque extras of the source (input type, option identifiers, ...), small.
    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    application: Mapped["Application"] = relationship(back_populates="questions")
    answer: Mapped["ApplicationQuestionAnswer | None"] = relationship(
        back_populates="question",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    generations: Mapped[list["ApplicationQuestionGeneration"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApplicationQuestionGeneration.created_at",
    )

    @validates("question_hash")
    def _validate_hash(self, key: str, value: str) -> str:
        return require_sha256_hex(key, value)


_HAS_FINAL = "(final_answer IS NOT NULL OR final_selected_options IS NOT NULL)"
_HAS_SUGGESTION = "(ai_suggestion IS NOT NULL OR ai_selected_options IS NOT NULL)"


class ApplicationQuestionAnswer(Base, TimestampMixin):
    """The current answer state of one question (exactly one per question).

    The suggestion (`ai_*`) and the user's final answer (`final_*`) are
    separate on purpose, and `status` says what the user has decided:

        NOT_STARTED  nothing yet
        NEEDS_INPUT  the latest attempt found no supported answer
        SUGGESTED    a validated suggestion, undecided
        ACCEPTED     final answer == the suggestion, accepted unchanged
        USER_EDITED  the final answer was written or edited by the user
        REJECTED     the suggestion was rejected; no final answer

    A final answer is never changed by an attempt. A choice question stores
    its answer as a list of options (`*_selected_options`), every other type
    as text.
    """

    __tablename__ = "application_question_answers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "user_id"],
            ["application_questions.id", "application_questions.user_id"],
            ondelete="CASCADE",
            name="fk_application_question_answers_question",
        ),
        UniqueConstraint("question_id", name="uq_application_question_answers_question_id"),
        # Only a user-decided answer is a final answer, and it says where it came from.
        CheckConstraint(
            f"(status IN ('ACCEPTED', 'USER_EDITED')) = {_HAS_FINAL}",
            name="ck_application_question_answers_final_iff_decided",
        ),
        CheckConstraint(
            f"(answer_source IS NOT NULL) = {_HAS_FINAL}",
            name="ck_application_question_answers_source_iff_final",
        ),
        CheckConstraint(
            "status <> 'ACCEPTED' OR answer_source IN ('DETERMINISTIC', 'AI')",
            name="ck_application_question_answers_accepted_source",
        ),
        CheckConstraint(
            "status <> 'USER_EDITED' OR answer_source IN ('USER', 'HYBRID')",
            name="ck_application_question_answers_edited_source",
        ),
        CheckConstraint(
            f"status NOT IN ('SUGGESTED', 'REJECTED') OR {_HAS_SUGGESTION}",
            name="ck_application_question_answers_suggestion_exists",
        ),
        CheckConstraint(
            f"status <> 'NEEDS_INPUT' OR (NOT {_HAS_SUGGESTION} AND NOT {_HAS_FINAL})",
            name="ck_application_question_answers_needs_input_empty",
        ),
        Index("ix_application_question_answers_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[AnswerStatus] = mapped_column(
        Enum(AnswerStatus, name="application_answer_status"),
        default=AnswerStatus.NOT_STARTED,
        nullable=False,
    )
    #: Where the final answer came from; NULL while there is none.
    answer_source: Mapped[AnswerSource | None] = mapped_column(
        Enum(AnswerSource, name="application_answer_source"), nullable=True
    )

    # --- The current suggestion (AI or deterministic): never written by the user ---------------
    ai_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_selected_options: Mapped[list[str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    #: Ids of the trusted facts the suggestion rests on.
    ai_source_fact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: How the suggestion was produced (copied from its attempt).
    ai_provenance: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    #: The attempt the suggestion came from.
    ai_generation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_question_generations.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The user turned the current suggestion down.
    suggestion_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Set while the latest attempt found no supported answer (see `NEEDS_INPUT`).
    needs_input_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- The user's answer: never written by the AI ----------------------------------------------
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_selected_options: Mapped[list[str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    #: The attempt whose suggestion the final answer was accepted or edited
    #: from; NULL for an answer the user wrote from scratch.
    basis_generation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_question_generations.id", ondelete="SET NULL"),
        nullable=True,
    )

    question: Mapped["ApplicationQuestion"] = relationship(back_populates="answer")

    @property
    def has_final(self) -> bool:
        return self.final_answer is not None or self.final_selected_options is not None

    @property
    def has_suggestion(self) -> bool:
        return self.ai_suggestion is not None or self.ai_selected_options is not None


class ApplicationQuestionGeneration(Base, CreatedAtMixin, ImmutableMixin):
    """One attempt to suggest an answer (V0.9). Write-once.

    Like a V0.8 generated document, an attempt keeps what it was produced
    from: the resume version and confirmed profile, the application context
    snapshot, a hash of the exact input, the prompt version and hash, the AI
    provider and model, the facts it was allowed to use (with their text, so
    each cited id stays resolvable after the resume changes) and the
    validation report. An attempt that failed or was rejected keeps no
    suggestion -- only the reason -- and never changes the answer.

    `created_at` is the generation timestamp (the attempt is written once, when
    it has finished).
    """

    __tablename__ = "application_question_generations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "user_id"],
            ["application_questions.id", "application_questions.user_id"],
            ondelete="CASCADE",
            name="fk_application_question_generations_question",
        ),
        CheckConstraint(
            "outcome <> 'SUGGESTED' OR (suggestion IS NOT NULL OR selected_options IS NOT NULL)",
            name="ck_application_question_generations_suggested_has_value",
        ),
        CheckConstraint(
            "outcome = 'SUGGESTED' OR (suggestion IS NULL AND selected_options IS NULL)",
            name="ck_application_question_generations_only_suggested_has_value",
        ),
        CheckConstraint(
            "method <> 'AI' OR (prompt_version IS NOT NULL AND prompt_hash IS NOT NULL "
            "AND ai_provider IS NOT NULL)",
            name="ck_application_question_generations_ai_provenance",
        ),
        Index("ix_application_question_generations_question_created", "question_id", "created_at"),
        Index("ix_application_question_generations_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[GenerationMethod] = mapped_column(
        Enum(GenerationMethod, name="application_generation_method"), nullable=False
    )
    outcome: Mapped[GenerationOutcome] = mapped_column(
        Enum(GenerationOutcome, name="application_generation_outcome"), nullable=False
    )
    #: Why no answer was suggested (outcome NEEDS_INPUT), as a stable code.
    needs_input_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- The result (only when outcome is SUGGESTED) ------------------------------------------
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_options: Mapped[list[str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    source_fact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: id -> {text, ref} of every fact the attempt could cite.
    source_facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: `ValidationReport`: why an AI answer passed or was rejected. NULL for
    #: deterministic attempts and for attempts that failed before validation.
    validation: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- Provenance -------------------------------------------------------------------------
    #: SHA-256 of the question as asked (text, type, options, limit): tells a
    #: suggestion for a since-changed question apart.
    question_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    #: SHA-256 of the exact generation input.
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    max_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )
    resume_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resume_profiles.id", ondelete="SET NULL"), nullable=True
    )
    resume_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    application_context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_context_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )

    question: Mapped["ApplicationQuestion"] = relationship(back_populates="generations")

    @validates("input_hash", "question_fingerprint")
    def _validate_hash(self, key: str, value: str) -> str:
        return require_sha256_hex(key, value)

    @validates("validation")
    def _validate_validation(self, key: str, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return ValidationReport.model_validate(value).model_dump(mode="json")

    @validates("source_facts")
    def _validate_source_facts(self, key: str, value: Any) -> dict[str, Any]:
        return {
            fact_id: SourceFact.model_validate(fact).model_dump(mode="json")
            for fact_id, fact in (value or {}).items()
        }
