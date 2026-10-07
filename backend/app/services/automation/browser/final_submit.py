"""The final-submission capability (V0.11): narrow, separate, and permit-gated.

The V0.10 browser interface (`BrowserPage`) is unchanged: it can read and write form
controls and has **no** way to press anything. Submitting is not a looser version of
filling. It is this separate interface, which a browser session offers in addition,
and which does exactly three things:

    find_final_controls()            read: the buttons that look like a final action
    submit_final_application(permit) press ONE final button -- the one the permit names
    read_outcome()                   read: what the page shows afterwards

There is no `click(selector)`, no key press, no script evaluation. The only acting
method takes a `SubmitPermit`, which only the submission guard
(`services.automation.submission`) issues -- after it has consumed a persisted
authorization and verified, in the same breath, that the live page is still exactly what
the user reviewed. A permit is single-use: redeeming it twice raises, so the same press
cannot happen twice through it, by construction.

Everything crossing this boundary is plain, serializable data.
"""

import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.services.automation.browser.base import BrowserError


@dataclass(frozen=True)
class SubmitControl:
    """A button that looks like the final action of the application, as the page shows
    it. A description, never a handle or a selector."""

    label: str
    #: "button" or "input".
    tag: str
    #: The button's type ("submit" or "button"); an input is always "submit".
    type: str
    dom_id: str | None = None
    name: str | None = None
    form_index: int | None = None
    #: Position among the buttons with the same label, in the page.
    ordinal: int = 0
    disabled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "tag": self.tag,
            "type": self.type,
            "dom_id": self.dom_id,
            "name": self.name,
            "form_index": self.form_index,
            "ordinal": self.ordinal,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubmitControl":
        return cls(
            label=str(data.get("label", "")),
            tag=str(data.get("tag", "")),
            type=str(data.get("type", "")),
            dom_id=data.get("dom_id"),
            name=data.get("name"),
            form_index=data.get("form_index"),
            ordinal=int(data.get("ordinal", 0)),
        )


@dataclass(frozen=True)
class OutcomeSignals:
    """What the page shows, read after the press. Held in memory only: the submission
    guard extracts a short, recognized confirmation from it and drops the rest."""

    url: str
    title: str
    #: The visible text of the page, bounded. Never stored or logged.
    text: str
    has_password: bool = False
    challenge_markers: list[str] = field(default_factory=list)
    #: A `data-application-reference` (or similar) marker the page carries.
    reference_marker: str | None = None
    #: The page carries an explicit success marker (`data-application-submitted`).
    success_marker: bool = False


class SubmissionNotTriggered(BrowserError):
    """The final button was certainly *not* pressed (the check before the press failed).
    Any other error from `submit_final_application` means "maybe pressed"."""


class PermitError(Exception):
    """A permit that is forged or already used. Nothing was pressed."""


_ISSUER = object()


class SubmitPermit:
    """A one-shot capability to press one final button on one site.

    Issued only through `issue_permit`, which the submission guard calls after it has
    consumed a persisted authorization for this very attempt. Redeemed once by the
    browser implementation; a second redemption raises `PermitError`."""

    __slots__ = ("attempt_id", "authorization_id", "control", "expected_host", "_lock", "_used")

    def __init__(
        self,
        *,
        attempt_id: uuid.UUID,
        authorization_id: uuid.UUID,
        expected_host: str,
        control: SubmitControl,
        issuer: object,
    ) -> None:
        if issuer is not _ISSUER:
            raise PermitError("Only the submission guard issues submit permits.")
        self.attempt_id = attempt_id
        self.authorization_id = authorization_id
        self.expected_host = expected_host
        self.control = control
        self._lock = threading.Lock()
        self._used = False

    @property
    def used(self) -> bool:
        return self._used

    def redeem(self) -> None:
        """Mark the permit used. Raises if it already was -- the press it allows happens
        at most once."""
        with self._lock:
            if self._used:
                raise PermitError("This submit permit has already been used.")
            self._used = True


def issue_permit(
    *,
    attempt_id: uuid.UUID,
    authorization_id: uuid.UUID,
    expected_host: str,
    control: SubmitControl,
) -> SubmitPermit:
    """Called only by `services.automation.submission` (a test pins that)."""
    return SubmitPermit(
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        expected_host=expected_host,
        control=control,
        issuer=_ISSUER,
    )


class FinalSubmissionChannel(ABC):
    """The final-submission capability of one browser session."""

    @abstractmethod
    def find_final_controls(self) -> list[SubmitControl]:
        """Every visible button (and submit input) of the page. Reads only; which of
        them is a final action is decided by `safety.final_controls`."""

    @abstractmethod
    def page_marker(self) -> str:
        """Which page instance the browser shows: it changes with every navigation,
        reload or new window. Equal markers mean the very same page instance."""

    @abstractmethod
    def submit_final_application(self, permit: SubmitPermit) -> None:
        """Press the final button the permit names, once.

        Raises `PermitError` (nothing pressed) for a forged or used permit and
        `SubmissionNotTriggered` when the check before the press fails (wrong site, the
        button is gone, disabled, covered, not a button). Any other error means the press
        may have happened."""

    @abstractmethod
    def read_outcome(self) -> OutcomeSignals:
        """What the page shows now. Reads only."""


class SupportsFinalSubmission(ABC):
    """A browser session that can offer the final-submission capability."""

    @abstractmethod
    def final_submission(self) -> FinalSubmissionChannel: ...
