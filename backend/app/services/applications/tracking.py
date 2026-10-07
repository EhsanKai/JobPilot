"""Application tracking (V0.12): the current status, its append-only history, and the
follow-up date -- the database side.

    change_status(user, application, request)      the user moves the status
    update_tracking(user, application, request)    the user sets a follow-up date / adds a note
    record_submission_confirmed(attempt)           V0.11 confirmed a submission  -> APPLIED
    record_user_verified_submission(attempt)       the user checked an unknown result -> APPLIED

**One writer.** Every change of `Application.status` goes through `_move` here, with the
application row locked (`SELECT ... FOR UPDATE`) and the history row written in the same
transaction -- so `Application.status` always equals the latest history row's
`new_status`, and the history's `sequence` (assigned under that lock) has no gaps and
no duplicates, however requests race. Two racing changes are both applied in the order
the lock grants; the second is validated against what the first left behind.

**Server-derived facts.** The previous status is what is read under the lock; the time
is read after the lock is taken; the actor is the authenticated user (or the system, for
a deterministic event). None of them comes from a request body.

**Deterministic.** Nothing here reads text, guesses, or calls an AI: a status moves
because the user said so, or because V0.11 confirmed a submission.

**Submission is upstream.** Tracking reads V0.11's attempts; it never changes them and
nothing here can start, retry or authorize a submission.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError, NotFoundError, ValidationAppError
from app.models.application import Application, ApplicationStatus
from app.models.application_status_history import (
    ApplicationStatusHistory,
    StatusChangeActor,
    StatusChangeSource,
    StatusHistoryEntryType,
)
from app.models.submission import SubmissionAttempt, SubmissionResult
from app.models.user import User
from app.models.user_job import UserJob
from app.schemas.application_tracking import (
    ApplicationTrackingRead,
    StatusChangeRequest,
    StatusCountsRead,
    SubmissionSummaryRead,
    TimelineEntryRead,
    TimelineResponse,
    TimelineSubmissionRead,
    TrackingUpdateRequest,
)
from app.services.applications import state_machine

S = ApplicationStatus

#: A confirmed submission moves an application that was still being prepared. Later
#: statuses already mean "applied" (the user got there first), so it changes nothing.
_SUBMISSION_APPLIES_FROM = frozenset({S.SAVED, S.PREPARING})


@dataclass(frozen=True)
class ChangeResult:
    application: Application
    changed: bool


def _now() -> datetime:
    return datetime.now(UTC)


# --- Locking and writing -----------------------------------------------------------------


def _lock_owned(db: Session, user: User, application_id: uuid.UUID) -> Application:
    """The user's own application, locked for a read-modify-write (404 otherwise)."""
    application = db.scalar(
        select(Application)
        .where(Application.id == application_id, Application.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if application is None:
        raise NotFoundError("Application not found.")
    return application


def _lock(db: Session, application_id: uuid.UUID) -> Application | None:
    """Lock an application on behalf of a deterministic system event."""
    return db.scalar(
        select(Application)
        .where(Application.id == application_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _next_sequence(db: Session, application_id: uuid.UUID) -> int:
    # Safe without a retry: the caller holds the application's row lock.
    latest = db.scalar(
        select(func.max(ApplicationStatusHistory.sequence)).where(
            ApplicationStatusHistory.application_id == application_id
        )
    )
    return (latest or 0) + 1


def _append(
    db: Session,
    application: Application,
    *,
    entry_type: StatusHistoryEntryType,
    previous: ApplicationStatus | None,
    new: ApplicationStatus,
    at: datetime,
    source: StatusChangeSource,
    actor: StatusChangeActor,
    note: str | None = None,
    attempt_id: uuid.UUID | None = None,
) -> ApplicationStatusHistory:
    entry = ApplicationStatusHistory(
        user_id=application.user_id,
        application_id=application.id,
        sequence=_next_sequence(db, application.id),
        entry_type=entry_type,
        previous_status=previous,
        new_status=new,
        changed_at=at,
        source=source,
        actor=actor,
        note=note,
        submission_attempt_id=attempt_id,
    )
    db.add(entry)
    # A new entry is a change to the application: "most recently updated" includes notes.
    application.updated_at = at
    db.flush()
    return entry


def _move(
    db: Session,
    application: Application,
    new: ApplicationStatus,
    *,
    entry_type: StatusHistoryEntryType,
    at: datetime,
    source: StatusChangeSource,
    actor: StatusChangeActor,
    note: str | None = None,
    attempt_id: uuid.UUID | None = None,
) -> None:
    """Change the current status and append its history row. The caller holds the lock
    and has validated the move."""
    previous = application.status
    application.status = new
    application.last_status_change_at = at
    if new in state_machine.APPLIED_STATES:
        if application.applied_at is None:
            application.applied_at = at
    elif new in (S.SAVED, S.PREPARING):
        # Corrected back to "not applied yet": there is no application date any more.
        # The history still says what happened.
        application.applied_at = None
    if not state_machine.accepts_follow_up(new):
        application.follow_up_at = None
    _append(
        db,
        application,
        entry_type=entry_type,
        previous=previous,
        new=new,
        at=at,
        source=source,
        actor=actor,
        note=note,
        attempt_id=attempt_id,
    )


# --- A new application -----------------------------------------------------------------------


def start_history(application: Application, user_job: UserJob, now: datetime) -> None:
    """The first two rows of a new application, added to the session (not flushed).

    SAVED is when the job came into the user's list (saved, or first discovered); PREPARING
    is now. The caller creates the application as PREPARING."""
    saved_at = user_job.saved_at or user_job.first_discovered_at or now
    saved_at = min(saved_at, now)
    for sequence, previous, new, at in (
        (1, None, S.SAVED, saved_at),
        (2, S.SAVED, S.PREPARING, now),
    ):
        application.status_history.append(
            ApplicationStatusHistory(
                user_id=application.user_id,
                sequence=sequence,
                entry_type=StatusHistoryEntryType.STATUS_CHANGE,
                previous_status=previous,
                new_status=new,
                changed_at=at,
                source=StatusChangeSource.SYSTEM,
                actor=StatusChangeActor.SYSTEM,
            )
        )
    application.last_status_change_at = now


# --- Changes by the user ------------------------------------------------------------------------


def _check_follow_up(status: ApplicationStatus) -> None:
    if not state_machine.accepts_follow_up(status):
        raise ValidationAppError(
            "A follow-up date can only be set while the application is applied, "
            "in follow-up or at interview.",
            {"field": "follow_up_at", "status": status.value},
        )


def change_status(
    db: Session, user: User, application_id: uuid.UUID, request: StatusChangeRequest
) -> ChangeResult:
    """Move the application to `request.status` (an allowed transition, or an explicit
    correction), optionally with a note and a follow-up date. Commits.

    The same status again is a no-op -- unless it carries a note, which is recorded as a
    NOTE entry. An invalid transition is a 409 and changes nothing."""
    application = _lock_owned(db, user, application_id)
    try:
        current = application.status
        moves = request.status is not current
        if request.correction and not moves:
            raise AppError(
                "ALREADY_IN_STATUS",
                f"The application is already {current.value}.",
                409,
                {"status": current.value},
            )
        allowed = state_machine.is_allowed(current, request.status)
        if moves and not request.correction and not allowed:
            raise AppError(
                "INVALID_STATUS_TRANSITION",
                f"An application can't go from {current.value} to {request.status.value}. "
                "If the status was recorded wrongly, make a correction.",
                409,
                {
                    "from": current.value,
                    "to": request.status.value,
                    "allowed": [s.value for s in state_machine.allowed_next(current)],
                },
            )
        if request.follow_up_at is not None:
            _check_follow_up(request.status)

        at = _now()
        changed = False
        if moves:
            _move(
                db,
                application,
                request.status,
                entry_type=(
                    StatusHistoryEntryType.CORRECTION
                    if request.correction
                    else StatusHistoryEntryType.STATUS_CHANGE
                ),
                at=at,
                source=StatusChangeSource.USER,
                actor=StatusChangeActor.USER,
                note=request.note,
            )
            changed = True
        elif request.note is not None:
            _append(
                db,
                application,
                entry_type=StatusHistoryEntryType.NOTE,
                previous=current,
                new=current,
                at=at,
                source=StatusChangeSource.USER,
                actor=StatusChangeActor.USER,
                note=request.note,
            )
            changed = True
        if request.follow_up_at is not None and request.follow_up_at != application.follow_up_at:
            application.follow_up_at = request.follow_up_at
            changed = True
    except AppError:
        db.rollback()  # releases the row lock
        raise
    db.commit()
    return ChangeResult(application, changed)


def update_tracking(
    db: Session, user: User, application_id: uuid.UUID, request: TrackingUpdateRequest
) -> ChangeResult:
    """Set or clear the follow-up date, and/or add a note (a NOTE entry; the status does
    not change). Setting a date writes no history row: it is a reminder for the user, not
    something that happened. Commits."""
    application = _lock_owned(db, user, application_id)
    try:
        changed = False
        if "follow_up_at" in request.model_fields_set:
            if request.follow_up_at is not None:
                _check_follow_up(application.status)
            if request.follow_up_at != application.follow_up_at:
                application.follow_up_at = request.follow_up_at
                changed = True
        if request.note is not None:
            _append(
                db,
                application,
                entry_type=StatusHistoryEntryType.NOTE,
                previous=application.status,
                new=application.status,
                at=_now(),
                source=StatusChangeSource.USER,
                actor=StatusChangeActor.USER,
                note=request.note,
            )
            changed = True
    except AppError:
        db.rollback()
        raise
    db.commit()
    return ChangeResult(application, changed)


# --- Changes caused by a V0.11 submission ---------------------------------------------------------


def _apply_submission(
    db: Session, attempt: SubmissionAttempt, actor: StatusChangeActor, at: datetime
) -> None:
    application = _lock(db, attempt.application_id)
    if application is None or application.status not in _SUBMISSION_APPLIES_FROM:
        return
    _move(
        db,
        application,
        S.APPLIED,
        entry_type=StatusHistoryEntryType.STATUS_CHANGE,
        at=at,
        source=StatusChangeSource.SUBMISSION,
        actor=actor,
        attempt_id=attempt.id,
    )


def record_submission_confirmed(db: Session, attempt_id: uuid.UUID) -> None:
    """V0.11 confirmed the submission (the page showed a confirmation): the application
    is APPLIED, as of the moment the result was recorded. An unknown or failed attempt
    never comes here. Flushes; the caller commits."""
    attempt = db.get(SubmissionAttempt, attempt_id, populate_existing=True)
    if attempt is None or attempt.result is not SubmissionResult.SUCCESS:
        return
    _apply_submission(db, attempt, StatusChangeActor.SYSTEM, attempt.finished_at or _now())


def record_user_verified_submission(db: Session, attempt: SubmissionAttempt) -> None:
    """After an unknown result the user checked and found the application submitted: it is
    APPLIED, recorded as the user's finding. Flushes; the caller commits."""
    if attempt.user_verified_outcome != "SUBMITTED":
        return
    _apply_submission(db, attempt, StatusChangeActor.USER, attempt.user_verified_at or _now())


# --- Reading ------------------------------------------------------------------------------------


def submission_summary(attempt: SubmissionAttempt | None) -> SubmissionSummaryRead | None:
    if attempt is None:
        return None
    return SubmissionSummaryRead(
        attempt_id=attempt.id,
        result=attempt.result,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
        confirmation_reference=attempt.confirmation_reference,
        user_verified_outcome=attempt.user_verified_outcome,
        needs_verification=(
            attempt.result is SubmissionResult.SUBMISSION_UNKNOWN
            and attempt.user_verified_outcome is None
        ),
    )


def latest_attempts(
    db: Session, user_id: uuid.UUID, application_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SubmissionAttempt]:
    """The most recent submission attempt per application (the user's own)."""
    if not application_ids:
        return {}
    rows = db.scalars(
        select(SubmissionAttempt)
        .where(
            SubmissionAttempt.user_id == user_id,
            SubmissionAttempt.application_id.in_(application_ids),
        )
        .order_by(SubmissionAttempt.application_id, SubmissionAttempt.started_at.desc())
        .execution_options(populate_existing=True)
    )
    latest: dict[uuid.UUID, SubmissionAttempt] = {}
    for attempt in rows:
        latest.setdefault(attempt.application_id, attempt)
    return latest


def tracking_read(
    application: Application, attempt: SubmissionAttempt | None
) -> ApplicationTrackingRead:
    return ApplicationTrackingRead(
        status=application.status,
        applied_at=application.applied_at,
        last_status_change_at=application.last_status_change_at,
        follow_up_at=application.follow_up_at,
        allowed_next_statuses=list(state_machine.allowed_next(application.status)),
        submission=submission_summary(attempt),
    )


def timeline(db: Session, user: User, application_id: uuid.UUID) -> TimelineResponse:
    """Everything that happened, newest first: the status history and the V0.11
    submission attempts, read where they live. Nothing is copied or changed."""
    application = db.scalar(
        select(Application)
        .where(Application.id == application_id, Application.user_id == user.id)
        .options(selectinload(Application.status_history))
        .execution_options(populate_existing=True)
    )
    if application is None:
        raise NotFoundError("Application not found.")
    attempts = db.scalars(
        select(SubmissionAttempt)
        .where(
            SubmissionAttempt.application_id == application.id,
            SubmissionAttempt.user_id == user.id,
        )
        .execution_options(populate_existing=True)
    ).all()

    # (time, rank, sequence): at the same instant the attempt comes before the status
    # change it caused; the history's own order is its sequence.
    ordered: list[tuple[datetime, int, int, TimelineEntryRead]] = []
    for row in application.status_history:
        ordered.append(
            (
                row.changed_at,
                1,
                row.sequence,
                TimelineEntryRead(
                    kind=row.entry_type.value,  # type: ignore[arg-type]
                    id=row.id,
                    occurred_at=row.changed_at,
                    previous_status=row.previous_status,
                    status=row.new_status,
                    source=row.source,
                    actor=row.actor,
                    note=row.note,
                    submission_attempt_id=row.submission_attempt_id,
                ),
            )
        )
    for attempt in attempts:
        occurred = attempt.finished_at or attempt.started_at
        ordered.append(
            (
                occurred,
                0,
                0,
                TimelineEntryRead(
                    kind="SUBMISSION",
                    id=attempt.id,
                    occurred_at=occurred,
                    submission=TimelineSubmissionRead(
                        attempt_id=attempt.id,
                        result=attempt.result,
                        destination_domain=attempt.destination_domain,
                        confirmation_reference=attempt.confirmation_reference,
                        confirmation_summary=attempt.confirmation_summary,
                        user_verified_outcome=attempt.user_verified_outcome,
                        user_verified_at=attempt.user_verified_at,
                        failure_code=attempt.failure_code,
                    ),
                ),
            )
        )
    ordered.sort(key=lambda item: item[:3], reverse=True)
    return TimelineResponse(items=[entry for *_, entry in ordered])


def status_counts(db: Session, user: User) -> StatusCountsRead:
    rows = db.execute(
        select(Application.status, func.count())
        .where(Application.user_id == user.id)
        .group_by(Application.status)
    ).all()
    counts = {status: 0 for status in ApplicationStatus}
    for status, count in rows:
        counts[status] = count
    return StatusCountsRead(counts=counts, total=sum(counts.values()))
