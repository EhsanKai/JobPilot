"""The live browsers of this server process (V0.10).

A session row in the database says what the user may do; the browser itself lives
in the memory of the process that started it. This registry maps session ids to
those browsers. It is deliberately a process-local object: a restarted server has no
browsers, and a session whose browser is gone is reconciled to STOPPED when it is next
looked at (see the controller). Running several server processes needs the requests of
one session to reach the same process (sticky routing) -- documented limitation.

Browsers are bounded (a maximum number alive at once) and an idle one is closed.
"""

import atexit
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging import get_logger
from app.services.automation.browser.base import BrowserDriver, BrowserSession

logger = get_logger(__name__)


@dataclass
class LiveSession:
    session_id: uuid.UUID
    browser: BrowserSession
    #: Serializes the operations that read or write the page (discover, fill, resume).
    #: Pausing, taking control and stopping do not wait for it: they only change the
    #: session's status, which every write checks first.
    lock: threading.RLock = field(default_factory=threading.RLock)
    last_used: float = field(default_factory=time.monotonic)
    #: The assistant no longer touches this browser (stopped); it stays open for the user.
    detached: bool = False

    def touch(self) -> None:
        self.last_used = time.monotonic()


DriverFactory = Callable[[], BrowserDriver]


def _default_driver() -> BrowserDriver:
    from app.services.automation.browser.playwright_driver import PlaywrightDriver

    return PlaywrightDriver(
        headless=settings.AUTOMATION_BROWSER_HEADLESS,
        executable_path=settings.AUTOMATION_CHROMIUM_PATH,
    )


class SessionRegistry:
    def __init__(self) -> None:
        self._live: dict[uuid.UUID, LiveSession] = {}
        self._lock = threading.Lock()
        self._driver_factory: DriverFactory = _default_driver

    # --- configuration (tests and the E2E server swap the driver) -------------------------------
    def set_driver_factory(self, factory: DriverFactory | None) -> None:
        self._driver_factory = factory or _default_driver

    def new_driver(self) -> BrowserDriver:
        return self._driver_factory()

    # --- sessions --------------------------------------------------------------------------------
    def get(self, session_id: uuid.UUID) -> LiveSession | None:
        with self._lock:
            live = self._live.get(session_id)
        if live is not None:
            live.touch()
        return live

    def peek(self, session_id: uuid.UUID) -> LiveSession | None:
        """Like `get`, without counting as use (a status poll does not keep a browser alive)."""
        with self._lock:
            return self._live.get(session_id)

    def add(self, live: LiveSession) -> None:
        with self._lock:
            self._live[live.session_id] = live

    def has_capacity(self) -> bool:
        self.close_idle()
        with self._lock:
            alive = sum(1 for live in self._live.values() if live.browser.is_alive())
        return alive < settings.AUTOMATION_MAX_BROWSERS

    def remove(self, session_id: uuid.UUID, *, close: bool = True) -> None:
        with self._lock:
            live = self._live.pop(session_id, None)
        if live is not None and close:
            try:
                live.browser.close()
            except Exception:  # noqa: BLE001
                logger.warning("Closing a browser failed")

    def close_idle(self) -> None:
        cutoff = time.monotonic() - settings.AUTOMATION_IDLE_CLOSE_MINUTES * 60
        with self._lock:
            idle = [
                sid
                for sid, live in self._live.items()
                if live.last_used < cutoff or not live.browser.is_alive()
            ]
        for session_id in idle:
            self.remove(session_id)

    def shutdown(self) -> None:
        with self._lock:
            ids = list(self._live)
        for session_id in ids:
            self.remove(session_id)


registry = SessionRegistry()
atexit.register(registry.shutdown)  # no browser outlives the server process
