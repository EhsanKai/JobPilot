"""Request/response schemas for application tracking (V0.12).

Requests are strict: a client cannot send `previous_status`, `user_id`, `changed_at`,
`actor` or `source` -- the server derives them. Notes are the user's own words and are
kept exactly as written (only a note with nothing but whitespace counts as no note).
"""

import uuid
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.application import ApplicationStatus
from app.models.application_status_history import StatusChangeActor, StatusChangeSource
from app.models.submission import SubmissionResult

MAX_TRACKING_NOTE_LENGTH = 2_000


def _clean_note(value: str | None) -> str | None:
    """A note is stored as written. Whitespace alone is no note."""
    if value is None or value.strip() == "":
        return None
    return value


def _aware(value: datetime | None) -> datetime | None:
    """A date-time without an offset is read as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class StatusChangeRequest(BaseModel):
    """Body of `PATCH /applications/{id}/status`."""

    model_config = ConfigDict(extra="forbid")

    status: ApplicationStatus
    note: str | None = Field(default=None, max_length=MAX_TRACKING_NOTE_LENGTH)
    #: Explicitly use the manual correction path: any other status, but a note is required.
    correction: bool = False
    #: Optionally set a follow-up date together with the status.
    follow_up_at: datetime | None = None

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _clean_note(value)

    @field_validator("follow_up_at")
    @classmethod
    def _follow_up(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def _correction_needs_a_note(self) -> "StatusChangeRequest":
        if self.correction and self.note is None:
            raise ValueError("A correction needs a note saying what you are correcting.")
        return self


class TrackingUpdateRequest(BaseModel):
    """Body of `PATCH /applications/{id}/tracking`: the follow-up date and/or a note.
    An omitted field is left alone; `follow_up_at: null` clears the date."""

    model_config = ConfigDict(extra="forbid")

    follow_up_at: datetime | None = None
    note: str | None = Field(default=None, max_length=MAX_TRACKING_NOTE_LENGTH)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _clean_note(value)

    @field_validator("follow_up_at")
    @classmethod
    def _follow_up(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def _something_to_do(self) -> "TrackingUpdateRequest":
        if not ({"follow_up_at"} & self.model_fields_set) and self.note is None:
            raise ValueError("Send a follow_up_at, a note, or both.")
        return self


class SubmissionSummaryRead(BaseModel):
    """The latest V0.11 submission attempt, as tracking shows it. Read from the
    attempt: tracking never copies or changes it."""

    attempt_id: uuid.UUID
    result: SubmissionResult | None
    started_at: datetime
    finished_at: datetime | None
    confirmation_reference: str | None
    #: After an unknown result: what the user found when they checked.
    user_verified_outcome: str | None
    #: The result is uncertain and the user has not checked yet.
    needs_verification: bool


class ApplicationTrackingRead(BaseModel):
    status: ApplicationStatus
    applied_at: datetime | None
    last_status_change_at: datetime
    follow_up_at: datetime | None
    #: What an ordinary status change may go to from here (the server decides).
    allowed_next_statuses: list[ApplicationStatus]
    #: The latest submission attempt, when there is one.
    submission: SubmissionSummaryRead | None


class StatusChangeResponse(BaseModel):
    #: False when the request changed nothing (the same status again, without a note).
    changed: bool
    tracking: ApplicationTrackingRead


TimelineKind = Literal["STATUS_CHANGE", "CORRECTION", "NOTE", "SUBMISSION"]


class TimelineSubmissionRead(BaseModel):
    attempt_id: uuid.UUID
    result: SubmissionResult | None
    destination_domain: str
    confirmation_reference: str | None
    confirmation_summary: str | None
    user_verified_outcome: str | None
    user_verified_at: datetime | None
    #: Present only when no confirmation was found; never a form value.
    failure_code: str | None


class TimelineEntryRead(BaseModel):
    """One event of the application's timeline: a status change, a correction, a note,
    or a V0.11 submission attempt."""

    kind: TimelineKind
    #: The history row's id, or the attempt's id for a SUBMISSION entry.
    id: uuid.UUID
    occurred_at: datetime
    previous_status: ApplicationStatus | None = None
    status: ApplicationStatus | None = None
    source: StatusChangeSource | None = None
    actor: StatusChangeActor | None = None
    #: The user's note, exactly as written.
    note: str | None = None
    #: For a status change caused by a submission.
    submission_attempt_id: uuid.UUID | None = None
    submission: TimelineSubmissionRead | None = None


class TimelineResponse(BaseModel):
    #: Newest first.
    items: list[TimelineEntryRead]


class StatusCountsRead(BaseModel):
    """How many of the user's applications are in each status (every status listed)."""

    counts: dict[ApplicationStatus, int]
    total: int


class ApplicationListFilters(BaseModel):
    """The list endpoint's filters, validated once."""

    statuses: list[ApplicationStatus] = Field(default_factory=list)
    company: str | None = None
    source: str | None = None
    applied_from: date | None = None
    applied_to: date | None = None
    sort: Literal["updated", "applied"] = "updated"
