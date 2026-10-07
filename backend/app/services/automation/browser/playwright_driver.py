"""The Playwright (Chromium) implementation of the browser interfaces (V0.10).

Playwright runs on one dedicated thread with its own event loop; every call here
blocks the *calling* thread until that loop has answered. That keeps the web
server's event loop free, works no matter which loop serves a request, and lets
the automation logic stay plain synchronous code.

What the page interface (`PlaywrightPage`) never does: click a button, submit a form,
solve or work around a challenge, change the browser's fingerprint, rotate proxies or
touch cookies. It starts an ordinary Chromium with an empty profile, opens the page it is
given, reads it and writes into text controls, selects, radios, checkboxes and file
inputs -- and only into those: every write first checks what kind of element the
locator resolved to.

V0.11 adds one separate capability, `PlaywrightFinalSubmission`: it presses the one final
button a single-use `SubmitPermit` names, on the site the permit names, once -- and
nothing else (see `browser/final_submit.py`).
"""

import asyncio
import atexit
import threading
from collections.abc import Awaitable
from typing import Any, TypeVar

from app.core.logging import get_logger
from app.services.automation.browser import scripts
from app.services.automation.browser.base import (
    BrowserDriver,
    BrowserError,
    BrowserPage,
    BrowserSession,
    FieldLocator,
    FieldValue,
    PageSignals,
    PageSnapshot,
    RawControl,
    RawOption,
)
from app.services.automation.browser.final_submit import (
    FinalSubmissionChannel,
    OutcomeSignals,
    PermitError,
    SubmissionNotTriggered,
    SubmitControl,
    SubmitPermit,
    SupportsFinalSubmission,
)

logger = get_logger(__name__)

T = TypeVar("T")

#: Operations on the page that should answer quickly.
_ACTION_TIMEOUT_MS = 10_000
#: Controls that submit, reset or press: the assistant never operates them.
_NEVER_TOUCH = frozenset({"submit", "button", "image", "reset"})
_TEXT_TYPES = frozenset(
    {"text", "email", "tel", "url", "number", "date", "month", "time", "datetime-local", "week"}
)


class _LoopThread:
    """A background thread running an asyncio loop for all browser work."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="jobpilot-browser", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        self._loop.run_forever()

    def call(self, coroutine: Awaitable[T], timeout: float = 90.0) -> T:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)  # type: ignore[arg-type]
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise BrowserError("TIMEOUT", "The browser did not answer in time.") from None

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


_loop_thread: _LoopThread | None = None
_loop_lock = threading.Lock()
_playwright: Any = None


def _worker() -> _LoopThread:
    global _loop_thread
    with _loop_lock:
        if _loop_thread is None:
            _loop_thread = _LoopThread()
        return _loop_thread


def shutdown_playwright() -> None:
    """Stop the Playwright driver process (called at interpreter exit)."""
    global _loop_thread, _playwright
    with _loop_lock:
        thread, pw = _loop_thread, _playwright
        _loop_thread, _playwright = None, None
    if thread is None:
        return
    if pw is not None:
        try:
            thread.call(pw.stop(), timeout=10)
        except Exception:  # pragma: no cover - best effort at exit
            pass
    thread.stop()


atexit.register(shutdown_playwright)


def _translate(exc: BaseException) -> BrowserError:
    """Playwright's exceptions as stable, user-safe codes (the message of the
    original may contain page content and is dropped)."""
    if isinstance(exc, BrowserError):
        return exc
    name = type(exc).__name__
    text = str(exc)
    if name == "TimeoutError":
        return BrowserError("TIMEOUT", "The page did not respond in time.")
    lowered = text.lower()
    if "has been closed" in lowered or "target closed" in lowered or "browser closed" in lowered:
        return BrowserError("BROWSER_CLOSED", "The browser was closed.")
    if "crash" in lowered:
        return BrowserError("PAGE_CRASHED", "The page crashed.")
    if "net::" in lowered or "ns_error" in lowered:
        return BrowserError("NAVIGATION_FAILED", "The page could not be loaded.")
    if "execution context was destroyed" in lowered or "navigat" in lowered:
        return BrowserError("PAGE_CHANGED", "The page changed while the assistant was reading it.")
    return BrowserError("BROWSER_ERROR", "The browser reported an error.")


def _is_private_url(url: str) -> bool:
    from urllib.parse import urlsplit

    from app.services.automation.safety import host_is_private

    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return True
    return host_is_private(host)


class PlaywrightPage(BrowserPage):
    def __init__(self, session: "PlaywrightSession") -> None:
        self._session = session

    # --- reading ---------------------------------------------------------------------------
    @property
    def url(self) -> str:
        return self._session.current_url

    def title(self) -> str:
        async def run() -> str:
            return str(await self._session.live_page().title())

        return self._session.run(run())

    def snapshot(self, *, max_controls: int) -> PageSnapshot:
        async def run() -> dict[str, Any]:
            page = self._session.live_page()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:  # noqa: BLE001 - reading a still-loading page is fine
                pass
            data = await page.evaluate(scripts.SNAPSHOT_JS, {"max": max_controls})
            return dict(data)

        data = self._session.run(run())
        controls = [
            RawControl(
                kind=str(c["kind"]),
                label=str(c.get("label") or ""),
                name=c.get("name"),
                dom_id=c.get("dom_id"),
                id_stable=bool(c.get("id_stable", True)),
                form=c.get("form"),
                form_index=c.get("form_index"),
                ordinal=int(c.get("ordinal", 0)),
                required=bool(c.get("required")),
                max_length=c.get("max_length"),
                options=[RawOption(str(o["value"]), str(o["label"])) for o in c.get("options", [])],
                disabled=bool(c.get("disabled")),
                readonly=bool(c.get("readonly")),
                meta={k: v for k, v in (c.get("meta") or {}).items() if v not in (None, "")},
            )
            for c in data["controls"]
        ]
        signals = data["signals"]
        return PageSnapshot(
            url=str(data["url"]),
            title=str(data["title"]),
            controls=controls,
            signals=PageSignals(
                has_password=bool(signals["has_password"]),
                challenge_markers=[str(m) for m in signals["challenge_markers"]],
                button_labels=[str(b) for b in signals["button_labels"]],
            ),
            truncated=bool(data.get("truncated")),
        )

    def read_value(self, locator: FieldLocator) -> FieldValue:
        async def run() -> dict[str, Any]:
            page = self._session.live_page()
            return dict(await page.evaluate(scripts.READ_JS, {"locator": locator.as_dict()}))

        data = self._session.run(run())
        if not data.get("found"):
            return FieldValue(found=False)
        return FieldValue(
            found=True,
            text=data.get("text"),
            selected=[str(v) for v in data.get("selected", [])],
            selected_labels=[str(v) for v in data.get("selected_labels", [])],
            checked=data.get("checked"),
            files=[(str(n), int(s)) for n, s in data.get("files", [])],
        )

    # --- writing: the only five things the assistant can do to a page ----------------------------
    async def _handle(
        self, locator: FieldLocator, option_value: str | None = None
    ) -> tuple[Any, dict[str, Any]]:
        page = self._session.live_page()
        js_handle = await page.evaluate_handle(
            scripts.RESOLVE_JS, {"locator": locator.as_dict(), "option": option_value}
        )
        element = js_handle.as_element()
        if element is None:
            await js_handle.dispose()
            raise BrowserError("FIELD_NOT_FOUND", "The field is no longer on the page.")
        info = dict(await element.evaluate(scripts.ELEMENT_KIND_JS))
        if info["tag"] == "button" or info["type"] in _NEVER_TOUCH:
            # Defense in depth: discovery never returns a button, and no write below accepts
            # one, but nothing that submits or presses is ever operated on.
            await element.dispose()
            raise BrowserError("NOT_A_FIELD", "That is not a form field.")
        return element, info

    def write_text(self, locator: FieldLocator, value: str) -> None:
        async def run() -> None:
            element, info = await self._handle(locator)
            try:
                tag, kind = info["tag"], info["type"]
                if not (tag == "textarea" or (tag == "input" and kind in _TEXT_TYPES)):
                    raise BrowserError("NOT_A_TEXT_CONTROL", "This is not a text field.")
                if info["readonly"] or info["disabled"]:
                    raise BrowserError("NOT_EDITABLE", "The field cannot be edited.")
                await element.fill(value, timeout=_ACTION_TIMEOUT_MS)
            finally:
                await element.dispose()

        self._session.run(run())

    def write_choice(self, locator: FieldLocator, option_value: str) -> None:
        async def run() -> None:
            if locator.kind == "radio":
                element, info = await self._handle(locator, option_value)
                try:
                    if info["tag"] != "input" or info["type"] != "radio":
                        raise BrowserError("NOT_A_CHOICE_CONTROL", "This is not a radio button.")
                    if info["disabled"]:
                        raise BrowserError("NOT_EDITABLE", "The option cannot be chosen.")
                    await element.check(timeout=_ACTION_TIMEOUT_MS)
                finally:
                    await element.dispose()
                return
            element, info = await self._handle(locator)
            try:
                if info["tag"] != "select":
                    raise BrowserError("NOT_A_CHOICE_CONTROL", "This is not a drop-down.")
                if info["disabled"]:
                    raise BrowserError("NOT_EDITABLE", "The field cannot be edited.")
                await element.select_option(value=option_value, timeout=_ACTION_TIMEOUT_MS)
            finally:
                await element.dispose()

        self._session.run(run())

    def write_checked(
        self, locator: FieldLocator, checked: bool, option_value: str | None = None
    ) -> None:
        async def run() -> None:
            element, info = await self._handle(locator, option_value)
            try:
                if info["tag"] != "input" or info["type"] != "checkbox":
                    raise BrowserError("NOT_A_CHOICE_CONTROL", "This is not a checkbox.")
                if info["disabled"]:
                    raise BrowserError("NOT_EDITABLE", "The field cannot be edited.")
                await element.set_checked(checked, timeout=_ACTION_TIMEOUT_MS)
            finally:
                await element.dispose()

        self._session.run(run())

    def write_file(self, locator: FieldLocator, name: str, mime_type: str, data: bytes) -> None:
        async def run() -> None:
            element, info = await self._handle(locator)
            try:
                if info["tag"] != "input" or info["type"] != "file":
                    raise BrowserError("NOT_A_FILE_CONTROL", "This is not a file field.")
                if info["disabled"]:
                    raise BrowserError("NOT_EDITABLE", "The field cannot be edited.")
                await element.set_input_files(
                    files=[{"name": name, "mimeType": mime_type, "buffer": data}],
                    timeout=_ACTION_TIMEOUT_MS,
                )
            finally:
                await element.dispose()

        self._session.run(run())

    # --- the user's view --------------------------------------------------------------------------
    def screenshot(self) -> bytes:
        async def run() -> bytes:
            return bytes(await self._session.live_page().screenshot(type="png", timeout=15_000))

        return self._session.run(run())

    def is_alive(self) -> bool:
        return self._session.is_alive()


class PlaywrightFinalSubmission(FinalSubmissionChannel):
    """The final-submission capability of one Playwright session (V0.11).

    `submit_final_application` is the only code in JobPilot that presses a button. It
    takes a permit, never a selector: the button is found again from the permit's
    description, must be the only element that matches it, must be a real button or
    submit input on the expected host, enabled, and able to receive the press (a trial
    press that changes nothing). Then it is pressed once. There is no retry here and none
    above it."""

    def __init__(self, session: "PlaywrightSession") -> None:
        self._session = session

    def find_final_controls(self) -> list[SubmitControl]:
        async def run() -> list[dict[str, Any]]:
            page = self._session.live_page()
            return list(await page.evaluate(scripts.FINAL_CONTROLS_JS))

        return [
            SubmitControl(
                label=str(c.get("label") or ""),
                tag=str(c.get("tag") or ""),
                type=str(c.get("type") or ""),
                dom_id=c.get("dom_id"),
                name=c.get("name"),
                form_index=c.get("form_index"),
                ordinal=int(c.get("ordinal", 0)),
                disabled=bool(c.get("disabled")),
            )
            for c in self._session.run(run())
        ]

    def page_marker(self) -> str:
        return self._session.page_marker()

    def submit_final_application(self, permit: SubmitPermit) -> None:
        if not isinstance(permit, SubmitPermit):
            raise PermitError("A submit permit is required.")
        permit.redeem()  # single use: a second call with the same permit raises here

        async def run() -> None:
            page = self._session.live_page()
            from urllib.parse import urlsplit

            host = (urlsplit(page.url).hostname or "").lower()
            if host != permit.expected_host:
                raise SubmissionNotTriggered("WRONG_DESTINATION", "The browser is on another site.")
            handle = await page.evaluate_handle(
                scripts.RESOLVE_FINAL_JS, {"control": permit.control.as_dict()}
            )
            element = handle.as_element()
            if element is None:
                await handle.dispose()
                raise SubmissionNotTriggered(
                    "SUBMIT_CONTROL_NOT_FOUND", "The final button is no longer on the page."
                )
            try:
                info = dict(await element.evaluate(scripts.ELEMENT_KIND_JS))
                pressable = (info["tag"] == "button" and permit.control.tag == "button") or (
                    info["tag"] == "input" and info["type"] == "submit"
                )
                if not pressable:
                    raise SubmissionNotTriggered(
                        "NOT_A_SUBMIT_CONTROL", "That is not the final button."
                    )
                if info["disabled"]:
                    raise SubmissionNotTriggered(
                        "SUBMIT_CONTROL_DISABLED", "The final button is disabled."
                    )
                try:
                    # Checks that the button is visible, stable, enabled and not covered --
                    # without pressing it.
                    await element.click(trial=True, timeout=_ACTION_TIMEOUT_MS)
                except Exception:  # noqa: BLE001 - nothing was pressed
                    raise SubmissionNotTriggered(
                        "SUBMIT_CONTROL_NOT_READY", "The final button cannot be pressed."
                    ) from None
                # The one press. Whatever happens from here on, it may have been made.
                await element.click(timeout=_ACTION_TIMEOUT_MS)
            finally:
                try:
                    await element.dispose()
                except Exception:  # noqa: BLE001 - the page may have navigated away
                    pass

        self._session.run(run())

    def read_outcome(self) -> OutcomeSignals:
        async def run() -> dict[str, Any]:
            page = self._session.live_page()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:  # noqa: BLE001 - a page still loading is read as it is
                pass
            return dict(await page.evaluate(scripts.OUTCOME_JS))

        data = self._session.run(run())
        return OutcomeSignals(
            url=str(data.get("url") or ""),
            title=str(data.get("title") or ""),
            text=str(data.get("text") or ""),
            has_password=bool(data.get("has_password")),
            challenge_markers=[str(m) for m in data.get("challenge_markers") or []],
            reference_marker=data.get("reference_marker"),
            success_marker=bool(data.get("success_marker")),
        )


class PlaywrightSession(BrowserSession, SupportsFinalSubmission):
    def __init__(
        self, *, browser: Any, context: Any, page: Any, headless: bool, allow_private: bool
    ) -> None:
        self._browser = browser
        self._context = context
        self._page = page
        self._headless = headless
        self._allow_private = allow_private
        self._crashed = False
        self._closed = False
        self._api = PlaywrightPage(self)
        self._final = PlaywrightFinalSubmission(self)
        #: Page instances and navigations, for `page_marker` (V0.11).
        self._page_numbers: dict[int, int] = {}
        self._navigations = 0
        self.watch_page(page)

    # --- which page instance is this? (V0.11) --------------------------------------------------
    def watch_page(self, page: Any) -> None:
        """Count the page and every navigation of its main frame (a reload included)."""
        self._page_numbers.setdefault(id(page), len(self._page_numbers) + 1)

        def navigated(frame: Any) -> None:
            if frame == page.main_frame:
                self._navigations += 1

        page.on("framenavigated", navigated)

    def page_marker(self) -> str:
        """Changes whenever the page the assistant works on is another one: a navigation,
        a reload, a popup that took over. Equal markers mean the same page instance."""
        try:
            page = self.live_page()
        except BrowserError:
            return "closed"
        number = self._page_numbers.get(id(page), 0)
        return f"{number}:{self._navigations}"

    def final_submission(self) -> FinalSubmissionChannel:
        return self._final

    # Everything below runs on (or is marshalled to) the browser thread.
    def run(self, coroutine: Awaitable[T]) -> T:
        if self._closed:
            raise BrowserError("BROWSER_CLOSED", "The browser was closed.")
        try:
            return _worker().call(coroutine)
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001 - translated, never leaked
            logger.warning("Browser operation failed (%s)", type(exc).__name__)
            raise _translate(exc) from None

    def live_page(self) -> Any:
        """The page the assistant works on: the newest open one (a popup the site
        opened, say), else the first alive. Called on the browser thread."""
        pages = [p for p in self._context.pages if not p.is_closed()]
        if not pages:
            raise BrowserError("BROWSER_CLOSED", "The browser window was closed.")
        if self._page in pages:
            return self._page
        self._page = pages[-1]
        return self._page

    @property
    def current_url(self) -> str:
        try:
            return str(self.live_page().url)
        except BrowserError:
            return ""

    @property
    def page(self) -> BrowserPage:
        return self._api

    @property
    def mode(self) -> str:
        return "headless" if self._headless else "visible"

    def open(self, url: str, *, timeout_seconds: float) -> None:
        async def run() -> None:
            page = self.live_page()
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000)
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:  # noqa: BLE001 - long-polling pages never go idle
                pass
            if response is not None and response.status in (401, 403, 429):
                raise BrowserError(
                    "ACCESS_RESTRICTED",
                    f"The site did not allow access to the page (HTTP {response.status}).",
                )
            if response is not None and response.status >= 400:
                raise BrowserError(
                    "PAGE_UNAVAILABLE", f"The page answered with an error (HTTP {response.status})."
                )

        self.run(run())

    def is_alive(self) -> bool:
        if self._closed or self._crashed:
            return False
        try:
            if not self._browser.is_connected():
                return False
            return any(not p.is_closed() for p in self._context.pages)
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        async def run() -> None:
            for closer in (self._context.close, self._browser.close):
                try:
                    await closer()
                except Exception:  # noqa: BLE001 - already gone is fine
                    pass

        try:
            _worker().call(run(), timeout=20)
        except Exception:  # noqa: BLE001
            logger.warning("Closing a browser failed")


class PlaywrightDriver(BrowserDriver):
    def __init__(self, *, headless: bool, executable_path: str | None = None) -> None:
        self._headless = headless
        self._executable_path = executable_path

    def launch(self, *, allow_private_hosts: bool) -> BrowserSession:
        async def run() -> PlaywrightSession:
            global _playwright
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                raise BrowserError(
                    "BROWSER_UNAVAILABLE", "Browser automation is not installed on this server."
                ) from None
            if _playwright is None:
                _playwright = await async_playwright().start()
            kwargs: dict[str, Any] = {"headless": self._headless}
            if self._executable_path:
                kwargs["executable_path"] = self._executable_path
            try:
                browser = await _playwright.chromium.launch(**kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Chromium could not be started (%s)", type(exc).__name__)
                raise BrowserError(
                    "BROWSER_UNAVAILABLE",
                    "Chromium could not be started on this server"
                    + ("" if self._headless else " (is a display available?)")
                    + ".",
                ) from None
            context_args: dict[str, Any] = {"accept_downloads": False}
            if self._headless:
                context_args["viewport"] = {"width": 1280, "height": 900}
            else:
                context_args["no_viewport"] = True
            context = await browser.new_context(**context_args)
            if not allow_private_hosts:

                async def guard(route: Any) -> None:
                    request = route.request
                    if request.is_navigation_request() and _is_private_url(request.url):
                        await route.abort("blockedbyclient")
                    else:
                        await route.continue_()

                await context.route("**/*", guard)
            page = await context.new_page()
            session = PlaywrightSession(
                browser=browser,
                context=context,
                page=page,
                headless=self._headless,
                allow_private=allow_private_hosts,
            )

            def on_crash() -> None:
                session._crashed = True  # noqa: SLF001

            page.on("crash", lambda *_: on_crash())
            context.on("page", session.watch_page)
            return session

        try:
            return _worker().call(run(), timeout=60)
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from None
