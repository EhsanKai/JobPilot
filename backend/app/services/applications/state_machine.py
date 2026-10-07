"""The application tracking lifecycle (V0.12): which status may follow which.

    SAVED        -> PREPARING, WITHDRAWN
    PREPARING    -> APPLIED, WITHDRAWN
    APPLIED      -> FOLLOW_UP, INTERVIEW, OFFER, REJECTED, WITHDRAWN, NO_RESPONSE
    FOLLOW_UP    -> INTERVIEW, OFFER, REJECTED, WITHDRAWN, NO_RESPONSE
    INTERVIEW    -> OFFER, REJECTED, WITHDRAWN, NO_RESPONSE
    OFFER        -> WITHDRAWN, REJECTED
    REJECTED, WITHDRAWN, NO_RESPONSE -> (nothing: terminal)

A pure table: no database, no clock, no judgement. The same current status and the
same requested status always give the same answer.

Real life is messier than any table -- a mis-click, a "no response" that turns into an
interview invitation weeks later. That is not allowed *silently*: the user can make a
**correction**, which may go to any other status but must say why (a note) and is
recorded as a CORRECTION, distinct from an ordinary change.
"""

from collections.abc import Mapping
from typing import Final

from app.models.application import ApplicationStatus as S

TRANSITIONS: Final[Mapping[S, tuple[S, ...]]] = {
    S.SAVED: (S.PREPARING, S.WITHDRAWN),
    S.PREPARING: (S.APPLIED, S.WITHDRAWN),
    S.APPLIED: (S.FOLLOW_UP, S.INTERVIEW, S.OFFER, S.REJECTED, S.WITHDRAWN, S.NO_RESPONSE),
    S.FOLLOW_UP: (S.INTERVIEW, S.OFFER, S.REJECTED, S.WITHDRAWN, S.NO_RESPONSE),
    S.INTERVIEW: (S.OFFER, S.REJECTED, S.WITHDRAWN, S.NO_RESPONSE),
    S.OFFER: (S.WITHDRAWN, S.REJECTED),
    S.REJECTED: (),
    S.WITHDRAWN: (),
    S.NO_RESPONSE: (),
}

#: Statuses with no further ordinary transition.
TERMINAL: Final[frozenset[S]] = frozenset(
    status for status, targets in TRANSITIONS.items() if not targets
)

#: Statuses that mean the application was sent: they carry an `applied_at`.
APPLIED_STATES: Final[frozenset[S]] = frozenset({S.APPLIED, S.FOLLOW_UP, S.INTERVIEW, S.OFFER})

#: Statuses in which a follow-up date makes sense: the user is waiting on the employer.
#: Moving to any other status drops a date set earlier.
FOLLOW_UP_STATES: Final[frozenset[S]] = frozenset({S.APPLIED, S.FOLLOW_UP, S.INTERVIEW})


def allowed_next(current: S) -> tuple[S, ...]:
    """The statuses an ordinary status change may move to from `current`."""
    return TRANSITIONS[current]


def is_allowed(current: S, new: S) -> bool:
    return new in TRANSITIONS[current]


def can_correct(current: S, new: S) -> bool:
    """A correction may go to any *other* status."""
    return current is not new


def accepts_follow_up(status: S) -> bool:
    return status in FOLLOW_UP_STATES
