import enum
import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.application_context_snapshot import ApplicationContextSnapshot
    from app.models.application_question import ApplicationQuestion
    from app.models.generated_document import GeneratedDocument
    from app.models.job import Job
    from app.models.job_source_posting import JobSourcePosting
    from app.models.resume import Resume
    from app.models.user import User
    from app.models.user_job import UserJob


class ApplicationStatus(str, enum.Enum):
    """Where an application is in its lifecycle.

    V0.7 only creates `PREPARING`: the user is getting the application
    ready in the Application Workspace. The other values are V0 placeholders
    for the complete lifecycle (submission, interviews, offers...), which
    belongs to V0.12 -- nothing in V0.7 sets or transitions to them, and they
    are kept only because Postgres cannot drop enum values and rows may exist.

    `PREPARING` is declared last on purpose: it was appended to the existing
    "application_status" Postgres type by the V0.7 migration, and Postgres
    enums cannot be reordered.
    """

    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    PREPARING = "preparing"


class NoticePeriodUnit(str, enum.Enum):
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"


class WorkAuthorization(str, enum.Enum):
    """What the candidate states about their right to work for *this* job.

    Optional: not every application asks. `None` means "not stated"; UNKNOWN
    is the explicit answer "I don't know yet / it isn't clear". This is the
    application-specific statement only -- the candidate snapshot's
    `work_authorization` (declared authorized countries) is unrelated and
    untouched.
    """

    AUTHORIZED = "authorized"
    REQUIRES_SPONSORSHIP = "requires_sponsorship"
    UNKNOWN = "unknown"


class Application(Base, TimestampMixin):
    """A user's private workspace for applying to one job (V0.7).

        User
         +-- UserJob                          (what the user did with the job)
               +-- MatchResult                (V0.6, separate domain)
               +-- Application                (this table: exactly one per UserJob)
                     +-- ApplicationContextSnapshot   what the job was, when it was started
                     +-- preparation data             salary, availability, language, notes...
                     +-- resume reference             which resume version it builds on
                     +-- GeneratedDocument (many)     tailored CV / Anschreiben, V0.8

    Starting an application is an explicit user action; a match, a saved job
    or an opened job never creates one. `user_job_id` is unique, so a job has
    at most one application per user -- enforced by the database, so
    double-clicks and concurrent requests cannot create duplicates.

    Ownership: the application belongs to whoever owns its `UserJob`. The
    composite foreign key (user_job_id, user_id) makes the denormalized
    `user_id` impossible to set to anyone else. `job_id` mirrors the
    UserJob's job (kept from V0 so `Job.applications` keeps working); the
    UserJob is authoritative.

    Preparation data is specific to this application and never written to
    the user's profile or preferences (and vice versa). `notes` are private:
    nothing sends them to an employer. The resume reference stays what the
    user chose: uploading a newer resume does not change it.

    The V0 columns `salary_expectation` / `salary_currency` are reused as the
    application's salary expectation (annual gross, like Job Preferences),
    and V0's `start_date` is now `available_from`: the date the candidate can
    start for this application.
    """

    __tablename__ = "applications"
    __table_args__ = (
        # An application can only reference a UserJob of its own user.
        ForeignKeyConstraint(
            ["user_job_id", "user_id"],
            ["user_jobs.id", "user_jobs.user_id"],
            ondelete="CASCADE",
            name="fk_applications_user_job",
        ),
        UniqueConstraint("user_job_id", name="uq_applications_user_job_id"),
        # The target of generated_documents' composite ownership key (V0.8).
        UniqueConstraint("id", "user_id", name="uq_applications_id_user_id"),
        CheckConstraint(
            "salary_expectation IS NULL OR salary_expectation >= 0",
            name="ck_applications_salary_expectation",
        ),
        CheckConstraint(
            "(notice_period_value IS NULL) = (notice_period_unit IS NULL)",
            name="ck_applications_notice_period_pair",
        ),
        CheckConstraint(
            "notice_period_value IS NULL OR notice_period_value >= 0",
            name="ck_applications_notice_period_value",
        ),
        CheckConstraint(
            "application_language IS NULL OR application_language ~ '^[a-z]{2}$'",
            name="ck_applications_application_language",
        ),
        # "My applications, most recently touched first."
        Index("ix_applications_user_id_updated_at", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Denormalized owner (see the composite foreign key above).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status"),
        default=ApplicationStatus.PREPARING,
        nullable=False,
    )

    # --- Where to apply -----------------------------------------------------------
    # The source posting chosen as the application target (the job's preferred
    # posting unless the user picked another). The URLs and identity at that
    # time are in the context snapshot, which outlives the posting row.
    source_posting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_source_postings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # --- Resume reference ----------------------------------------------------------
    # The resume version this application builds on. A reference, not a copy:
    # the file and its confirmed profile stay in the resume tables. NULL when
    # the user has no resume (or deleted the one they had).
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # --- Preparation data (specific to this application) ---------------------------
    # Annual gross, in `salary_currency`: the same convention as Job Preferences.
    salary_expectation: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # NULL = not decided. The user explicitly chooses whether a future letter
    # states the salary; nothing chooses for them.
    mention_salary_in_letter: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    available_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    notice_period_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notice_period_unit: Mapped[NoticePeriodUnit | None] = mapped_column(
        Enum(NoticePeriodUnit, name="notice_period_unit"), nullable=True
    )
    work_authorization: Mapped[WorkAuthorization | None] = mapped_column(
        Enum(WorkAuthorization, name="work_authorization"), nullable=True
    )
    # ISO 639-1 key ("de", "en"); which keys are offered is application
    # configuration, so a new language needs no migration. NULL until the user
    # chooses: it is never guessed from the job.
    application_language: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # Private. Never sent to an employer.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="applications")
    # `user_id` is written by both `user` and the composite foreign key to the
    # UserJob (which guarantees they agree), hence `overlaps`.
    user_job: Mapped["UserJob"] = relationship(
        back_populates="application", overlaps="user,applications"
    )
    job: Mapped["Job | None"] = relationship(back_populates="applications")
    source_posting: Mapped["JobSourcePosting | None"] = relationship()
    resume: Mapped["Resume | None"] = relationship()
    context: Mapped["ApplicationContextSnapshot | None"] = relationship(
        back_populates="application",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # Tailored documents generated for this application (V0.8), every attempt.
    documents: Mapped[list["GeneratedDocument"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="GeneratedDocument.created_at",
    )
    # Questions the employer asks (V0.9), in the order they were entered.
    questions: Mapped[list["ApplicationQuestion"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApplicationQuestion.sort_order, ApplicationQuestion.created_at",
    )
