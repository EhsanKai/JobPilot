"""V0.10 browser sessions through the real API and a real (headless) Chromium: the
lifecycle, ownership, and the safety rules that hand the browser back to the user --
a CAPTCHA, a login, an unexpected website, a closed browser. Nothing here ever submits.
"""

import logging
import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    AutomationEvent,
    AutomationField,
    AutomationSession,
    JobSourcePosting,
)
from app.services.automation import controller
from app.services.automation.browser.base import BrowserDriver, BrowserError
from app.services.automation.registry import registry
from tests.application_helpers import make_job, started, user_by_email
from tests.automation_helpers import (
    FakeAutomationAI,
    Human,
    base,
    get_fields,
    get_session,
    make_application,
    post,
    session_url,
    start,
    started_session,
)
from tests.fake_site import FakeApplicationSite
from tests.question_helpers import error, ok


@pytest.fixture
def fake_ai() -> FakeAutomationAI:
    return FakeAutomationAI()


@pytest.fixture
def auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {register_and_login()['access_token']}"}


@pytest.fixture
def other_auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = register_and_login(email="mallory@example.com")["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def site(automation_env: FakeApplicationSite) -> FakeApplicationSite:
    return automation_env


@pytest.fixture
def app_(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> dict[str, Any]:
    return make_application(client, auth, db_session, site)


@pytest.fixture
def session(client: TestClient, auth: dict[str, str], app_: dict[str, Any]) -> dict[str, Any]:
    return started_session(client, auth, app_["id"])


# --- Starting ------------------------------------------------------------------------------------


def test_nothing_opens_until_the_user_starts_the_assistant(
    client: TestClient,
    auth: dict[str, str],
    app_: dict[str, Any],
    site: FakeApplicationSite,
    db_session: Session,
) -> None:
    # An application exists, the page is known -- and no browser has been started.
    assert registry.has_capacity()
    assert site.page_loads == {}
    listing = ok(client.get(base(app_["id"]), headers=auth))
    assert listing["items"] == [] and listing["can_start"] is True
    assert db_session.scalar(select(AutomationSession)) is None


def test_start_opens_the_application_page_and_discovers_its_fields(
    session: dict[str, Any], site: FakeApplicationSite
) -> None:
    assert session["status"] == "RUNNING"
    assert session["status_label"] in ("Running", "Needs your input", "Needs your review")
    assert session["browser_mode"] == "headless"
    assert session["browser_alive"] is True
    assert session["target_url"] == site.url("/apply")
    assert session["current_url"] == site.url("/apply")
    assert session["page_title"] == "Apply - Example GmbH"
    assert session["counts"]["found"] == 24
    assert session["started_at"] is not None and session["ended_at"] is None
    assert session["controls"] == {
        "can_pause": True,
        "can_resume": False,
        "can_take_control": True,
        "can_stop": True,
        "can_discover": True,
        "can_fill": True,
        "can_confirm_domain": False,
    }
    assert site.page_loads == {"/apply": 1}
    assert site.submit_clicks == 0


def test_the_application_url_comes_from_the_snapshot_and_a_client_cannot_supply_one(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], site: FakeApplicationSite
) -> None:
    response = client.post(
        f"{base(app_['id'])}/start", headers=auth, json={"url": "https://evil.example.com/"}
    )
    assert response.status_code == 422
    assert site.page_loads == {}
    # Moving the posting afterwards does not change an application's snapshot either.
    assert started_session(client, auth, app_["id"])["target_url"] == site.url("/apply")


def test_a_second_session_cannot_be_started_while_one_is_live(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    body = error(start(client, auth, app_["id"]), 409, "SESSION_ALREADY_ACTIVE")
    assert body["details"]["session_id"] == session["id"]
    listing = ok(client.get(base(app_["id"]), headers=auth))
    assert listing["can_start"] is False
    assert listing["cannot_start_reason"] == "SESSION_ALREADY_ACTIVE"


def test_the_database_allows_one_live_session_per_application(
    session: dict[str, Any], db_session: Session
) -> None:
    from sqlalchemy.exc import IntegrityError

    row = db_session.get(AutomationSession, uuid.UUID(session["id"]))
    assert row is not None
    db_session.add(
        AutomationSession(
            application_id=row.application_id,
            user_id=row.user_id,
            status=row.status,
            target_url=row.target_url,
            allowed_hosts=[],
            acknowledged_markers=[],
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_an_application_without_an_application_url_cannot_start(
    client: TestClient, auth: dict[str, str], db_session: Session, automation_env: Any
) -> None:
    user = user_by_email(db_session)
    # A source that does not host the form and states no apply URL.
    job = make_job(db_session, user, sources=("other",))
    application = started(client, auth, job, None)
    listing = ok(client.get(base(application["id"]), headers=auth))
    assert listing["can_start"] is False
    assert listing["cannot_start_reason"] == "NO_APPLICATION_URL"
    error(start(client, auth, application["id"]), 409, "NO_APPLICATION_URL")


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://example.com/apply", "UNSUPPORTED_URL_SCHEME"),
        ("javascript:alert(1)", "UNSUPPORTED_URL_SCHEME"),
        ("https://user:secret@example.com/apply", "UNSAFE_APPLICATION_URL"),
        ("http://127.0.0.1:8000/apply", "PRIVATE_APPLICATION_URL"),
        ("http://localhost/apply", "PRIVATE_APPLICATION_URL"),
        ("http://169.254.169.254/latest/meta-data", "PRIVATE_APPLICATION_URL"),
        ("http://10.0.0.5/apply", "PRIVATE_APPLICATION_URL"),
        ("http://[::1]/apply", "PRIVATE_APPLICATION_URL"),
    ],
)
def test_unsafe_application_urls_are_refused(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    code: str,
) -> None:
    monkeypatch.setattr(settings, "AUTOMATION_ALLOW_PRIVATE_HOSTS", False)
    application = make_application(client, auth, db_session, site, apply_url=url)
    error(start(client, auth, application["id"]), 409, code)
    assert db_session.scalar(select(AutomationSession)) is None


def test_stored_urls_never_carry_a_query_or_fragment(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    application = make_application(
        client, auth, db_session, site, apply_url=site.url("/apply?token=SECRET123#frag")
    )
    session = started_session(client, auth, application["id"])
    assert "SECRET123" not in str(session)
    row = db_session.get(AutomationSession, uuid.UUID(session["id"]))
    assert row is not None
    assert row.target_url == site.url("/apply") and row.current_url == site.url("/apply")


# --- The user's controls -------------------------------------------------------------------------


def _status(client: TestClient, auth: dict[str, str], app_: dict[str, Any], sid: str) -> str:
    return str(get_session(client, auth, app_["id"], sid)["status"])


def test_pause_take_control_resume_and_stop(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    sid, aid = session["id"], app_["id"]

    paused = ok(post(client, auth, aid, sid, "pause"))
    assert paused["status"] == "PAUSED" and paused["status_label"] == "Paused"
    assert paused["controls"]["can_resume"] is True and paused["controls"]["can_fill"] is False

    resumed = ok(post(client, auth, aid, sid, "resume"))
    assert resumed["status"] == "RUNNING"

    control = ok(post(client, auth, aid, sid, "take-control"))
    assert control["status"] == "USER_CONTROL" and control["status_label"] == "User control"
    assert control["controls"]["can_resume"] is True
    assert control["controls"]["can_fill"] is False and control["controls"]["can_discover"] is False

    assert ok(post(client, auth, aid, sid, "resume"))["status"] == "RUNNING"
    stopped = ok(post(client, auth, aid, sid, "stop"))
    assert stopped["status"] == "STOPPED" and stopped["ended_at"] is not None
    assert stopped["controls"]["can_stop"] is False

    events = [
        e["event_type"]
        for e in ok(client.get(f"{session_url(aid, sid)}/events", headers=auth))["items"]
    ]
    for expected in (
        "SESSION_STARTED",
        "AUTOMATION_PAUSED",
        "AUTOMATION_RESUMED",
        "USER_TAKEOVER",
        "AUTOMATION_STOPPED",
        "DISCOVERY_STARTED",
        "FIELD_DISCOVERED",
        "FIELD_MAPPED",
    ):
        assert expected in events, expected


def test_invalid_transitions_are_refused(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    sid, aid = session["id"], app_["id"]
    error(post(client, auth, aid, sid, "resume"), 409, "INVALID_SESSION_STATE")  # not paused
    ok(post(client, auth, aid, sid, "pause"))
    error(post(client, auth, aid, sid, "pause"), 409, "INVALID_SESSION_STATE")
    error(post(client, auth, aid, sid, "fill"), 409, "INVALID_SESSION_STATE")  # paused
    error(post(client, auth, aid, sid, "discover"), 409, "INVALID_SESSION_STATE")
    ok(post(client, auth, aid, sid, "stop"))
    for action in ("pause", "resume", "take-control", "fill", "discover", "stop"):
        error(post(client, auth, aid, sid, action), 409, "INVALID_SESSION_STATE")


def test_there_is_no_submitted_status_and_no_submit_endpoint(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    from app.models import AutomationStatus

    assert "SUBMITTED" not in {s.value for s in AutomationStatus}
    for action in ("submit", "apply", "click", "send"):
        response = post(client, auth, app_["id"], session["id"], action)
        assert response.status_code in (404, 405)
    paths = {route.path for route in client.app.routes}  # type: ignore[attr-defined]
    assert not any("submit" in path and "automation" in path for path in paths)


def test_stop_leaves_the_browser_open_for_the_user_unless_asked_to_close_it(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    sid = uuid.UUID(session["id"])
    stopped = ok(post(client, auth, app_["id"], session["id"], "stop"))
    live = registry.peek(sid)
    assert live is not None and live.detached and live.browser.is_alive()
    assert stopped["controls"] == {
        "can_pause": False,
        "can_resume": False,
        "can_take_control": False,
        "can_stop": False,
        "can_discover": False,
        "can_fill": False,
        "can_confirm_domain": False,
    }
    # The user keeps using the window; the assistant does not touch it.
    Human(session["id"]).type_into("#first_name", "Still mine")
    error(post(client, auth, app_["id"], session["id"], "fill"), 409, "INVALID_SESSION_STATE")
    assert Human(session["id"]).value("#first_name") == "Still mine"

    # A new session replaces it; "close_browser" closes the window.
    new = started_session(client, auth, app_["id"])
    ok(post(client, auth, app_["id"], new["id"], "stop", {"close_browser": True}))
    assert registry.peek(uuid.UUID(new["id"])) is None


def test_a_session_is_reconciled_when_its_browser_is_gone(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    # The window was closed (or the server restarted: the registry is empty).
    registry.remove(uuid.UUID(session["id"]))
    ended = get_session(client, auth, app_["id"], session["id"])
    assert ended["status"] == "STOPPED"
    assert ended["browser_alive"] is False
    assert ended["last_error"]["code"] == "BROWSER_GONE"
    error(post(client, auth, app_["id"], session["id"], "fill"), 409, "INVALID_SESSION_STATE")
    # Starting again works.
    assert started_session(client, auth, app_["id"])["status"] == "RUNNING"


def test_a_vanished_browser_during_an_action_ends_the_session(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    registry.remove(uuid.UUID(session["id"]))
    body = error(post(client, auth, app_["id"], session["id"], "fill"), 409, "BROWSER_GONE")
    assert "Start the assistant again" in body["message"]
    assert get_session(client, auth, app_["id"], session["id"])["status"] == "STOPPED"


# --- Ownership -----------------------------------------------------------------------------------


def test_another_user_cannot_see_or_control_a_session(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    app_: dict[str, Any],
    session: dict[str, Any],
) -> None:
    aid, sid = app_["id"], session["id"]
    field_id = get_fields(client, auth, aid, sid)[0]["id"]
    for method, url, body in [
        ("get", base(aid), None),
        ("post", f"{base(aid)}/start", None),
        ("get", session_url(aid, sid), None),
        ("get", f"{session_url(aid, sid)}/fields", None),
        ("get", f"{session_url(aid, sid)}/events", None),
        ("get", f"{session_url(aid, sid)}/screenshot", None),
        ("post", f"{session_url(aid, sid)}/pause", None),
        ("post", f"{session_url(aid, sid)}/resume", None),
        ("post", f"{session_url(aid, sid)}/take-control", None),
        ("post", f"{session_url(aid, sid)}/stop", None),
        ("post", f"{session_url(aid, sid)}/discover", None),
        ("post", f"{session_url(aid, sid)}/fill", None),
        ("post", f"{session_url(aid, sid)}/suggest", None),
        ("post", f"{session_url(aid, sid)}/confirm-domain", {"host": "127.0.0.1"}),
        ("post", f"{session_url(aid, sid)}/fields/{field_id}/mapping", {"action": "unmap"}),
        ("get", f"{session_url(aid, sid)}/fields/{field_id}/documents", None),
    ]:
        response = (
            getattr(client, method)(url, headers=other_auth, json=body)
            if body
            else getattr(client, method)(url, headers=other_auth)
        )
        assert response.status_code == 404, (method, url, response.text)
    # Nothing changed for the owner.
    assert get_session(client, auth, aid, sid)["status"] == "RUNNING"


def test_a_session_is_only_reachable_through_its_own_application(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    app_: dict[str, Any],
    session: dict[str, Any],
) -> None:
    other_job = make_job(db_session, user_by_email(db_session))
    posting = db_session.scalar(
        select(JobSourcePosting).where(JobSourcePosting.job_id == other_job.id)
    )
    assert posting is not None
    posting.apply_url = site.url("/apply")
    db_session.commit()
    second = started(client, auth, other_job, None)
    error(client.get(session_url(second["id"], session["id"]), headers=auth), 404, "NOT_FOUND")
    error(post(client, auth, second["id"], session["id"], "pause"), 404, "NOT_FOUND")


def test_unauthenticated_requests_are_rejected(
    client: TestClient, app_: dict[str, Any], session: dict[str, Any]
) -> None:
    assert client.get(base(app_["id"])).status_code == 401
    assert client.post(f"{base(app_['id'])}/start").status_code == 401
    assert client.get(session_url(app_["id"], session["id"])).status_code == 401


def test_the_database_refuses_a_field_or_event_of_someone_elses_session(
    session: dict[str, Any], db_session: Session
) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.models import User

    row = db_session.get(AutomationSession, uuid.UUID(session["id"]))
    assert row is not None
    stranger = User(email="x@example.com", password_hash="x", first_name="X", last_name="Y")
    db_session.add(stranger)
    db_session.commit()
    db_session.add(AutomationEvent(session_id=row.id, user_id=stranger.id, event_type="X", data={}))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --- What the response may contain ---------------------------------------------------------------


def test_responses_expose_no_browser_internals(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    aid, sid = app_["id"], session["id"]
    texts = [
        client.get(session_url(aid, sid), headers=auth).text,
        client.get(f"{session_url(aid, sid)}/fields", headers=auth).text,
        client.get(f"{session_url(aid, sid)}/events", headers=auth).text,
        client.get(base(aid), headers=auth).text,
    ]
    blob = " ".join(texts).lower()
    for forbidden in (
        "cookie",
        "ws://",
        "wss://",
        "devtools",
        "remote-debugging",
        "executable",
        "/opt/",
        "/home/",
        "/tmp/",
        "locator",
        "storage_reference",
        "password",
    ):
        assert forbidden not in blob, forbidden
    fields = ok(client.get(f"{session_url(aid, sid)}/fields", headers=auth))["items"]
    assert all("locator" not in f and "meta" not in f for f in fields)


def test_the_screenshot_is_a_png_for_the_owner_and_never_stored(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    response = client.get(f"{session_url(app_['id'], session['id'])}/screenshot", headers=auth)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content.startswith(b"\x89PNG")
    ok(post(client, auth, app_["id"], session["id"], "stop", {"close_browser": True}))
    error(
        client.get(f"{session_url(app_['id'], session['id'])}/screenshot", headers=auth),
        409,
        "INVALID_SESSION_STATE",
    )


def test_events_and_logs_never_contain_form_values(
    client: TestClient,
    auth: dict[str, str],
    app_: dict[str, Any],
    session: dict[str, Any],
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from tests.automation_helpers import answer_question, field_named

    caplog.set_level(logging.DEBUG)
    aid, sid = app_["id"], session["id"]
    fields = get_fields(client, auth, aid, sid)
    email = field_named(fields, "Email address")
    answer_question(client, auth, aid, email["question"]["id"], "private.address@example.com")
    ok(post(client, auth, aid, sid, "fill"))
    assert Human(sid).value("#email") == "private.address@example.com"

    events = client.get(f"{session_url(aid, sid)}/events", headers=auth).text
    assert "private.address" not in events
    assert any(e.event_type == "FIELD_FILLED" for e in db_session.scalars(select(AutomationEvent)))
    assert "private.address" not in caplog.text
    # The value is not in the field's stored state either: only a salted hash.
    for row in db_session.scalars(select(AutomationField)):
        assert "private.address" not in str(row.__dict__)
        if row.last_agent_value_hash:
            assert len(row.last_agent_value_hash) == 64


# --- Stopping and handing back -------------------------------------------------------------------


def _start_on(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    application = make_application(client, auth, db_session, site, path=path)
    return application, started_session(client, auth, application["id"])


def test_a_captcha_stops_the_assistant_and_returns_control_to_the_user(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    app_, blocked = _start_on(client, auth, db_session, site, "/challenge")
    aid, sid = app_["id"], blocked["id"]
    assert blocked["status"] == "BLOCKED" and blocked["status_label"] == "Blocked"
    assert blocked["attention"]["code"] == "SECURITY_CHALLENGE"
    assert "does not solve" in blocked["attention"]["message"]
    assert (
        blocked["controls"]["can_take_control"] is True and blocked["controls"]["can_fill"] is False
    )
    # Nothing was discovered, filled or tried.
    assert get_fields(client, auth, aid, sid) == []
    error(post(client, auth, aid, sid, "fill"), 409, "INVALID_SESSION_STATE")
    error(post(client, auth, aid, sid, "discover"), 409, "INVALID_SESSION_STATE")
    error(
        post(client, auth, aid, sid, "resume"), 409, "INVALID_SESSION_STATE"
    )  # take control first

    # The user takes over, deals with the check themselves and hands the browser back.
    control = ok(post(client, auth, aid, sid, "take-control"))
    assert control["status"] == "USER_CONTROL"
    assert control["attention"]["code"] == "SECURITY_CHALLENGE"
    resumed = ok(post(client, auth, aid, sid, "resume"))
    assert resumed["status"] == "RUNNING" and resumed["attention"] is None
    assert [f["label"] for f in get_fields(client, auth, aid, sid)] == ["Email address"]
    # The solved widget is still on the page; the assistant does not stop for it again.
    assert ok(post(client, auth, aid, sid, "discover"))["status"] == "RUNNING"
    events = [
        e["event_type"]
        for e in ok(client.get(f"{session_url(aid, sid)}/events", headers=auth))["items"]
    ]
    assert "SECURITY_CHALLENGE" in events and "USER_TAKEOVER" in events
    assert site.submit_clicks == 0


def test_a_page_that_gates_the_whole_site_is_never_waved_through(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    app_, blocked = _start_on(client, auth, db_session, site, "/interstitial")
    aid, sid = app_["id"], blocked["id"]
    assert blocked["status"] == "BLOCKED"
    ok(post(client, auth, aid, sid, "take-control"))
    # Still the interstitial: resuming does not get past it.
    again = ok(post(client, auth, aid, sid, "resume"))
    assert again["status"] == "BLOCKED"
    assert again["attention"]["code"] == "SECURITY_CHALLENGE"


def test_a_login_page_hands_over_without_collecting_credentials(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app_, blocked = _start_on(client, auth, db_session, site, "/login")
    aid, sid = app_["id"], blocked["id"]
    assert blocked["status"] == "BLOCKED"
    assert blocked["attention"]["code"] == "LOGIN_REQUIRED"
    assert blocked["attention"]["message"] == (
        "Login required. Please take control of the browser and sign in manually."
    )
    # The password field was not recorded -- nor anything else of the page.
    assert get_fields(client, auth, aid, sid) == []
    assert db_session.scalar(select(AutomationField)) is None
    # The user signs in themselves (here: the page is simply left) and returns control.
    ok(post(client, auth, aid, sid, "take-control"))
    Human(sid).goto(site.url("/apply"))
    resumed = ok(post(client, auth, aid, sid, "resume"))
    assert resumed["status"] == "RUNNING" and resumed["counts"]["found"] == 24
    assert "password" not in caplog.text.lower()


def test_a_forbidden_page_is_reported_not_worked_around(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    _, blocked = _start_on(client, auth, db_session, site, "/forbidden")
    assert blocked["status"] == "BLOCKED"
    assert blocked["attention"]["code"] == "ACCESS_RESTRICTED"
    assert blocked["controls"]["can_take_control"] is True


def test_a_page_without_a_form_says_so_and_never_presses_apply(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    app_, empty = _start_on(client, auth, db_session, site, "/empty")
    assert empty["status"] == "RUNNING"
    assert empty["attention"]["code"] == "NO_FORM_FOUND"
    assert "never presses Apply" in empty["attention"]["message"]
    assert empty["counts"]["found"] == 0
    # The "Apply now" link on the page was not followed.
    assert "/apply" not in site.page_loads
    assert site.submit_clicks == 0


def test_a_dynamic_page_is_found_when_the_form_appears(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    import time

    app_, first = _start_on(client, auth, db_session, site, "/spa")
    aid, sid = app_["id"], first["id"]
    time.sleep(0.8)
    again = ok(post(client, auth, aid, sid, "discover"))
    assert again["counts"]["found"] == 1 and again["attention"] is None
    assert [f["label"] for f in get_fields(client, auth, aid, sid)] == ["Email address"]


def test_unrelated_navigation_pauses_the_assistant_until_the_user_confirms(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any], session: dict[str, Any]
) -> None:
    aid, sid = app_["id"], session["id"]
    # The browser ends up on another website (here: the same server under another name).
    Human(sid).goto(site_away := session["target_url"].replace("127.0.0.1", "localhost"))
    assert site_away.startswith("http://localhost")
    paused = get_session(client, auth, aid, sid)
    assert paused["status"] == "PAUSED"
    assert paused["attention"]["code"] == "UNEXPECTED_DOMAIN"
    assert (
        paused["controls"]["can_confirm_domain"] is True
        and paused["controls"]["can_resume"] is False
    )
    error(post(client, auth, aid, sid, "fill"), 409, "INVALID_SESSION_STATE")
    error(post(client, auth, aid, sid, "resume"), 409, "CONFIRM_DOMAIN_FIRST")
    error(
        post(client, auth, aid, sid, "confirm-domain", {"host": "evil.example.com"}),
        409,
        "DOMAIN_CHANGED_AGAIN",
    )
    confirmed = ok(post(client, auth, aid, sid, "confirm-domain", {"host": "localhost"}))
    assert confirmed["status"] == "RUNNING" and confirmed["attention"] is None
    assert get_session(client, auth, aid, sid)["status"] == "RUNNING"


def test_a_redirect_to_another_website_at_start_pauses_before_anything_is_read(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    app_, paused = _start_on(client, auth, db_session, site, "/away")
    assert paused["status"] == "PAUSED"
    assert paused["attention"]["code"] == "UNEXPECTED_DOMAIN"
    assert paused["counts"]["found"] == 0
    assert get_fields(client, auth, app_["id"], paused["id"]) == []


# --- Failures ------------------------------------------------------------------------------------


def test_an_unreachable_page_fails_the_session_and_frees_the_application(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    application = make_application(
        client, auth, db_session, site, apply_url="http://127.0.0.1:1/apply"
    )
    failed = started_session(client, auth, application["id"])
    assert failed["status"] == "FAILED" and failed["status_label"] == "Failed"
    assert failed["last_error"]["code"] in ("NAVIGATION_FAILED", "BROWSER_ERROR")
    assert failed["ended_at"] is not None
    assert registry.peek(uuid.UUID(failed["id"])) is None  # its browser was closed
    listing = ok(client.get(base(application["id"]), headers=auth))
    assert listing["can_start"] is True


def test_a_missing_browser_is_a_clear_failure(
    client: TestClient, auth: dict[str, str], app_: dict[str, Any]
) -> None:
    class NoBrowser(BrowserDriver):
        def launch(self, *, allow_private_hosts: bool) -> Any:
            raise BrowserError("BROWSER_UNAVAILABLE", "Chromium could not be started.")

    registry.set_driver_factory(NoBrowser)
    failed = started_session(client, auth, app_["id"])
    assert failed["status"] == "FAILED"
    assert failed["last_error"] == {
        "code": "BROWSER_UNAVAILABLE",
        "message": "Chromium could not be started.",
    }


def test_the_number_of_live_browsers_is_bounded(
    client: TestClient,
    auth: dict[str, str],
    app_: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AUTOMATION_MAX_BROWSERS", 0)
    error(start(client, auth, app_["id"]), 503, "TOO_MANY_BROWSERS")


# --- Interrupting a running fill -----------------------------------------------------------------


def test_taking_control_during_a_fill_stops_further_writes(
    client: TestClient,
    auth: dict[str, str],
    app_: dict[str, Any],
    session: dict[str, Any],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user presses "Take control" while the assistant is mid-fill: the field being
    written finishes, nothing after it is written."""
    from app.services.automation import filler
    from tests.conftest import TestSessionLocal

    owner = user_by_email(db_session)
    app_id, sid = uuid.UUID(app_["id"]), uuid.UUID(session["id"])
    real_write = filler.write
    first_write = threading.Event()
    writes: list[str] = []

    def slow_write(page: Any, field: Any, intended: Any) -> None:
        writes.append(field.label)
        real_write(page, field, intended)
        if len(writes) == 1:
            first_write.set()
            threading.Event().wait(0.6)  # the first field is still being written

    monkeypatch.setattr(filler, "write", slow_write)
    worker_db = TestSessionLocal()
    outcome: dict[str, Any] = {}

    def run_fill() -> None:
        try:
            worker_user = user_by_email(worker_db)
            controller.fill(worker_db, worker_user, app_id, sid, None, None)
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc
        finally:
            worker_db.close()

    thread = threading.Thread(target=run_fill)
    thread.start()
    assert first_write.wait(20)
    control_db = TestSessionLocal()
    try:
        controller.take_control(control_db, user_by_email(control_db), app_id, sid)
    finally:
        control_db.close()
    thread.join(30)
    assert "error" not in outcome, outcome
    assert len(writes) == 1, writes  # no field after the first was written
    db_session.expire_all()
    row = db_session.get(AutomationSession, sid)
    assert row is not None and row.status.value == "USER_CONTROL"
    assert owner is not None
