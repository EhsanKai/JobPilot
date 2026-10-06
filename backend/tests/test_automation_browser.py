"""The Playwright implementation of the browser interfaces, used directly (V0.10):
what it reads, what it refuses to touch, and what it does when the page changes under it.
A real headless Chromium against the local fake employer website; no database content is
involved.
"""

import pytest

from app.services.automation.browser.base import BrowserError, FieldLocator
from app.services.automation.browser.playwright_driver import PlaywrightDriver
from tests.fake_site import FakeApplicationSite


@pytest.fixture
def browser(automation_env: FakeApplicationSite):  # type: ignore[no-untyped-def]
    session = PlaywrightDriver(headless=True).launch(allow_private_hosts=True)
    session.open(automation_env.url("/apply"), timeout_seconds=20)
    yield session
    session.close()


def test_the_snapshot_lists_controls_and_ignores_buttons_and_honeypots(browser) -> None:  # type: ignore[no-untyped-def]
    snapshot = browser.page.snapshot(max_controls=300)
    names = {c.name for c in snapshot.controls}
    assert "email" in names and "authorized" in names
    assert "hp_website" not in names  # off-screen honeypot
    assert not any(c.kind in ("submit", "button") for c in snapshot.controls)
    assert "Submit application" in snapshot.signals.button_labels  # seen, never operated
    assert snapshot.signals.has_password is False
    assert snapshot.signals.challenge_markers == []


def test_the_snapshot_is_capped_and_says_so(browser) -> None:  # type: ignore[no-untyped-def]
    snapshot = browser.page.snapshot(max_controls=5)
    assert len(snapshot.controls) == 5 and snapshot.truncated is True


def test_a_button_can_never_be_written_to(  # type: ignore[no-untyped-def]
    browser, automation_env: FakeApplicationSite
) -> None:
    page = browser.page
    for locator in (
        FieldLocator(kind="text", dom_id="submit-btn", label="Submit application"),
        FieldLocator(kind="submit", dom_id="submit-btn", label="Submit application"),
        FieldLocator(kind="text", name="save", label="Save draft"),
    ):
        with pytest.raises(BrowserError) as refused:
            page.write_text(locator, "x")
        assert refused.value.code in ("FIELD_NOT_FOUND", "NOT_A_FIELD")
    for call in (
        lambda: page.write_checked(
            FieldLocator(kind="checkbox", dom_id="submit-btn", label="Submit application"), True
        ),
        lambda: page.write_choice(
            FieldLocator(kind="select", dom_id="submit-btn", label="Submit application"), "x"
        ),
        lambda: page.write_file(
            FieldLocator(kind="file", dom_id="submit-btn", label="Submit application"),
            "a.pdf",
            "application/pdf",
            b"%PDF",
        ),
    ):
        with pytest.raises(BrowserError):
            call()
    assert automation_env.submit_clicks == 0


def test_a_write_to_the_wrong_kind_of_control_is_refused(browser) -> None:  # type: ignore[no-untyped-def]
    page = browser.page
    with pytest.raises(BrowserError) as refused:
        page.write_text(FieldLocator(kind="select", dom_id="country", label="Country"), "x")
    assert refused.value.code == "NOT_A_TEXT_CONTROL"
    with pytest.raises(BrowserError) as refused:
        page.write_checked(FieldLocator(kind="text", dom_id="email", label="Email address"), True)
    assert refused.value.code in ("NOT_A_CHOICE_CONTROL", "FIELD_NOT_FOUND")


def test_a_control_that_left_the_page_is_reported_not_guessed(browser) -> None:  # type: ignore[no-untyped-def]
    page = browser.page
    snapshot = page.snapshot(max_controls=300)
    email = next(c for c in snapshot.controls if c.name == "email")
    locator = FieldLocator(kind=email.kind, dom_id=email.dom_id, name=email.name, label=email.label)
    assert page.read_value(locator).found is True
    from app.services.automation.browser.playwright_driver import _worker

    async def remove() -> None:
        await browser.live_page().evaluate("document.getElementById('email').remove()")

    _worker().call(remove())
    assert page.read_value(locator).found is False
    with pytest.raises(BrowserError) as gone:
        page.write_text(locator, "x")
    assert gone.value.code == "FIELD_NOT_FOUND"


def test_a_reload_does_not_break_the_locators(browser) -> None:  # type: ignore[no-untyped-def]
    from app.services.automation.browser.playwright_driver import _worker

    page = browser.page
    locator = FieldLocator(kind="email", dom_id="email", name="email", label="Email address")
    page.write_text(locator, "a@b.c")
    assert page.read_value(locator).text == "a@b.c"

    async def reload() -> None:
        await browser.live_page().reload()

    _worker().call(reload())
    assert page.read_value(locator).text == ""  # the page reset itself
    page.write_text(locator, "again@b.c")
    assert page.read_value(locator).text == "again@b.c"


def test_the_browser_does_not_open_private_addresses_unless_allowed(
    automation_env: FakeApplicationSite,
) -> None:
    session = PlaywrightDriver(headless=True).launch(allow_private_hosts=False)
    try:
        with pytest.raises(BrowserError) as refused:
            session.open(automation_env.url("/apply"), timeout_seconds=10)
        assert refused.value.code == "NAVIGATION_FAILED"
        assert automation_env.page_loads.get("/apply", 0) == 0  # the request never left the browser
    finally:
        session.close()


def test_a_closed_browser_is_reported(automation_env: FakeApplicationSite) -> None:
    session = PlaywrightDriver(headless=True).launch(allow_private_hosts=True)
    session.open(automation_env.url("/apply"), timeout_seconds=20)
    assert session.is_alive() and session.page.is_alive()
    session.close()
    assert not session.is_alive()
    with pytest.raises(BrowserError) as closed:
        session.page.snapshot(max_controls=10)
    assert closed.value.code == "BROWSER_CLOSED"
    session.close()  # closing twice is harmless


def test_the_screenshot_is_a_png(browser) -> None:  # type: ignore[no-untyped-def]
    assert browser.page.screenshot().startswith(b"\x89PNG")
