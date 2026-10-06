"""The browser, as the rest of JobPilot sees it (V0.10).

Nothing outside `browser/` knows Playwright. The domain talks to these small
interfaces, so the automation logic (discovery, mapping, filling, the user's
control) is testable without a browser and a different engine could replace
Chromium without touching it.

**There is no `click` here.** The page can be *read* (a snapshot of its controls
and a few safety signals), and a control can be *written*: text typed into a text
control, an option chosen, a box checked, a file attached. Nothing in the
interface can press a button, so no code built on it can press "Submit" -- by
construction, not by a check somewhere. (Checking a radio button or a checkbox
is a click on that input in the browser; the implementation refuses anything that
is not a text control, a select, a radio, a checkbox or a file input.)

Everything crossing this boundary is plain, serializable data.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class BrowserError(Exception):
    """A browser operation failed. `code` is a stable, user-safe reason; the
    message never contains page content or a form value."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True)
class RawOption:
    value: str
    label: str


@dataclass(frozen=True)
class RawControl:
    """One control (or group of radio buttons / checkboxes) as the page shows it.

    `kind` is the *control*: text, email, tel, url, number, date, month, time,
    datetime-local, textarea, select, radio (a group), checkbox, checkbox_group,
    file, custom (an ARIA widget the assistant does not drive).
    """

    kind: str
    label: str
    name: str | None = None
    dom_id: str | None = None
    #: Whether `dom_id` looks stable (generated ids such as ":r1:" are not).
    id_stable: bool = True
    form: str | None = None
    form_index: int | None = None
    #: Position among the controls with the same kind and label, in the page.
    ordinal: int = 0
    required: bool = False
    max_length: int | None = None
    options: list[RawOption] = field(default_factory=list)
    disabled: bool = False
    readonly: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PageSignals:
    """What the page says about itself that matters for safety. Booleans, codes
    and button labels only -- never page text or values."""

    #: A visible password field: signing in is the user's to do.
    has_password: bool = False
    #: Visible security-challenge widgets: "recaptcha", "hcaptcha", "turnstile",
    #: "arkose", "datadome", "perimeterx", "interstitial".
    challenge_markers: list[str] = field(default_factory=list)
    #: Labels of visible buttons and links that look like they could submit.
    button_labels: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PageSnapshot:
    url: str
    title: str
    controls: list[RawControl]
    signals: PageSignals
    #: More controls than the configured maximum were on the page.
    truncated: bool = False


@dataclass(frozen=True)
class FieldLocator:
    """How to find one control again: by what the page itself says, never by
    position alone. A serializable description, not a handle."""

    kind: str
    dom_id: str | None = None
    name: str | None = None
    label: str = ""
    ordinal: int = 0
    form_index: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "dom_id": self.dom_id,
            "name": self.name,
            "label": self.label,
            "ordinal": self.ordinal,
            "form_index": self.form_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldLocator":
        return cls(
            kind=str(data.get("kind", "")),
            dom_id=data.get("dom_id"),
            name=data.get("name"),
            label=str(data.get("label", "")),
            ordinal=int(data.get("ordinal", 0)),
            form_index=data.get("form_index"),
        )


@dataclass(frozen=True)
class FieldValue:
    """What a control holds right now. Held in memory only -- never stored."""

    found: bool
    #: Text of a text control; the selected option's value of a select / radio.
    text: str | None = None
    #: Values of the selected options (select, checkbox group) or checked boxes.
    selected: list[str] = field(default_factory=list)
    #: A single checkbox / radio.
    checked: bool | None = None
    #: Visible label of the selected option, where there is one.
    selected_labels: list[str] = field(default_factory=list)
    #: Attached files: (name, size).
    files: list[tuple[str, int]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        if not self.found:
            return True
        return not ((self.text or "").strip() or self.selected or self.checked or self.files)


class BrowserPage(ABC):
    """The page the assistant works on. Synchronous: it blocks until the browser has
    answered (the caller is a request thread, never the browser's own loop)."""

    @property
    @abstractmethod
    def url(self) -> str: ...

    @abstractmethod
    def title(self) -> str: ...

    @abstractmethod
    def snapshot(self, *, max_controls: int) -> PageSnapshot:
        """Read every visible control and the safety signals. Reads only."""

    @abstractmethod
    def read_value(self, locator: FieldLocator) -> FieldValue:
        """What the control holds now. `found=False` when it is gone."""

    @abstractmethod
    def write_text(self, locator: FieldLocator, value: str) -> None:
        """Type into a text control, firing the events a modern form listens to."""

    @abstractmethod
    def write_choice(self, locator: FieldLocator, option_value: str) -> None:
        """Choose the option of a select (by its value) or the radio of a group."""

    @abstractmethod
    def write_checked(
        self, locator: FieldLocator, checked: bool, option_value: str | None = None
    ) -> None:
        """Check / uncheck a checkbox (of a group: the one with `option_value`)."""

    @abstractmethod
    def write_file(self, locator: FieldLocator, name: str, mime_type: str, data: bytes) -> None:
        """Attach a file from memory. No filesystem path is involved."""

    @abstractmethod
    def screenshot(self) -> bytes:
        """A PNG of the page, for the user. Never stored."""

    @abstractmethod
    def is_alive(self) -> bool: ...


class BrowserSession(ABC):
    """One controlled browser (its own profile; nothing is kept after it closes)."""

    @property
    @abstractmethod
    def page(self) -> BrowserPage: ...

    @property
    @abstractmethod
    def mode(self) -> str:
        """ "visible" or "headless"."""

    @abstractmethod
    def open(self, url: str, *, timeout_seconds: float) -> None:
        """Navigate to `url` (the application page). Raises `BrowserError`."""

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def is_alive(self) -> bool: ...


class BrowserDriver(ABC):
    """Starts browsers."""

    @abstractmethod
    def launch(self, *, allow_private_hosts: bool) -> BrowserSession:
        """Raises `BrowserError("BROWSER_UNAVAILABLE")` when no browser can start."""
