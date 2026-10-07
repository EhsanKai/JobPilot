"""V0.12 tracking on top of V0.11 submissions (a real headless Chromium on the fake
employer's form): what a submission result does -- and does not -- do to the tracking status,
and that the submission audit is never touched by tracking.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Application, SubmissionAttempt
from app.services.automation import documents
from tests.application_helpers import API
from tests.automation_helpers import FakeAutomationAI, base
from tests.fake_site import FakeApplicationSite
from tests.question_helpers import ok
from tests.submission_helpers import SubmitCtx, make_submit_ctx
from tests.tracking_helpers import (
    assert_history_is_consistent,
    change_of,
    history_rows,
    set_status,
    timeline,
)


@pytest.fixture
def fake_ai() -> FakeAutomationAI:
    return FakeAutomationAI()


@pytest.fixture(autouse=True)
def _storage(monkeypatch: pytest.MonkeyPatch, file_storage: Any) -> None:
    monkeypatch.setattr(documents, "get_file_storage", lambda: file_storage)


@pytest.fixture(autouse=True)
def _quick_results(monkeypatch: pytest.MonkeyPatch) -> None:
    # An ambiguous page is given up on after 2 s (production: 20 s).
    monkeypatch.setattr(settings, "SUBMISSION_RESULT_TIMEOUT_SECONDS", 2.0)


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
def ctx(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> SubmitCtx:
    return make_submit_ctx(client, auth, db_session, site)


def attempt_columns(db: Session, attempt_id: str) -> dict[str, Any]:
    row = db.scalar(
        select(SubmissionAttempt)
        .where(SubmissionAttempt.id == uuid.UUID(attempt_id))
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return {column.key: getattr(row, column.key) for column in SubmissionAttempt.__table__.columns}


def tracking_of(ctx: SubmitCtx) -> dict[str, Any]:
    response = ctx.client.get(f"{API}/applications/{ctx.aid}", headers=ctx.auth)
    assert response.status_code == 200, response.text
    return response.json()["tracking"]  # type: ignore[no-any-return]


def application_row(ctx: SubmitCtx) -> Application:
    row = ctx.db.get(Application, uuid.UUID(ctx.aid), populate_existing=True)
    assert row is not None
    return row


# --- A confirmed submission ---------------------------------------------------------------------


def test_a_confirmed_submission_makes_the_application_applied(ctx: SubmitCtx) -> None:
    assert tracking_of(ctx)["status"] == "PREPARING"
    attempt = ok(ctx.submit(ctx.ready_authorization()["id"]))
    assert attempt["result"] == "SUCCESS"

    tracking = tracking_of(ctx)
    assert tracking["status"] == "APPLIED"
    assert tracking["applied_at"] == attempt["finished_at"]
    assert tracking["submission"]["result"] == "SUCCESS"
    assert tracking["submission"]["confirmation_reference"] == "EXM-2026-0001"
    assert tracking["submission"]["needs_verification"] is False

    rows = assert_history_is_consistent(ctx.db, ctx.aid)
    last = rows[-1]
    assert change_of(last) == ("PREPARING", "APPLIED")
    assert last.source.value == "SUBMISSION" and last.actor.value == "SYSTEM"
    assert str(last.submission_attempt_id) == attempt["id"]
    assert last.note is None
    assert last.changed_at == application_row(ctx).applied_at


def test_the_timeline_shows_the_submission_and_the_status_it_caused(ctx: SubmitCtx) -> None:
    attempt = ok(ctx.submit(ctx.ready_authorization()["id"]))
    items = timeline(ctx.client, ctx.auth, ctx.aid)

    assert [(i["kind"], i["status"]) for i in items[:2]] == [
        ("STATUS_CHANGE", "APPLIED"),
        ("SUBMISSION", None),
    ]
    submission = items[1]["submission"]
    assert submission["attempt_id"] == attempt["id"] and submission["result"] == "SUCCESS"
    assert submission["confirmation_reference"] == "EXM-2026-0001"
    assert submission["destination_domain"]
    assert items[0]["submission_attempt_id"] == attempt["id"]
    assert items[0]["source"] == "SUBMISSION"


def test_my_applications_shows_the_submission_result(ctx: SubmitCtx) -> None:
    ok(ctx.submit(ctx.ready_authorization()["id"]))
    body = ok(ctx.client.get(f"{API}/applications", headers=ctx.auth))
    item = body["items"][0]
    assert item["status"] == "APPLIED"
    assert item["submission"]["result"] == "SUCCESS" and item["submission"]["finished_at"]
    assert item["applied_at"] == item["submission"]["finished_at"]


def test_changing_the_status_afterwards_leaves_the_submission_audit_untouched(
    ctx: SubmitCtx,
) -> None:
    attempt = ok(ctx.submit(ctx.ready_authorization()["id"]))
    before = attempt_columns(ctx.db, attempt["id"])

    for status in ("FOLLOW_UP", "INTERVIEW", "OFFER"):
        assert set_status(ctx.client, ctx.auth, ctx.aid, status).status_code == 200
    corrected = set_status(
        ctx.client, ctx.auth, ctx.aid, "PREPARING", correction=True, note="Mistake."
    )
    assert corrected.status_code == 200

    assert attempt_columns(ctx.db, attempt["id"]) == before
    assert ctx.site.submit_clicks == 1  # tracking never presses anything
    assert ctx.attempts()["submitted"] is True  # V0.11's own view of the fact is unchanged
    # ... and the timeline still tells the submission's story.
    kinds = [i["kind"] for i in timeline(ctx.client, ctx.auth, ctx.aid)]
    assert kinds.count("SUBMISSION") == 1


def test_a_submission_that_lands_on_an_already_applied_application_changes_nothing(
    ctx: SubmitCtx,
) -> None:
    authorization = ctx.ready_authorization()
    # The user already recorded that they applied (outside JobPilot) ...
    assert set_status(ctx.client, ctx.auth, ctx.aid, "APPLIED").status_code == 200
    applied_at = tracking_of(ctx)["applied_at"]
    before = len(history_rows(ctx.db, ctx.aid))
    # ... and then the confirmed submission arrives.
    ok(ctx.submit(authorization["id"]))
    assert tracking_of(ctx)["status"] == "APPLIED"
    assert tracking_of(ctx)["applied_at"] == applied_at
    assert len(history_rows(ctx.db, ctx.aid)) == before  # no duplicate APPLIED


def test_a_confirmed_submission_does_not_override_a_withdrawn_application(
    ctx: SubmitCtx,
) -> None:
    authorization = ctx.ready_authorization()
    assert set_status(ctx.client, ctx.auth, ctx.aid, "WITHDRAWN").status_code == 200
    attempt = ok(ctx.submit(authorization["id"]))
    assert attempt["result"] == "SUCCESS"
    # The user's explicit decision stands; the submission is still on the timeline.
    assert tracking_of(ctx)["status"] == "WITHDRAWN"
    assert_history_is_consistent(ctx.db, ctx.aid)
    assert any(i["kind"] == "SUBMISSION" for i in timeline(ctx.client, ctx.auth, ctx.aid))


# --- An unknown result: the user decides ----------------------------------------------------------


def unknown_attempt(
    client: TestClient, auth: dict[str, str], db: Session, site: Any
) -> tuple[SubmitCtx, dict[str, Any]]:
    unknown = make_submit_ctx(client, auth, db, site, path="/submit-ambiguous")
    attempt = ok(unknown.submit(unknown.ready_authorization()["id"]))
    assert attempt["result"] == "SUBMISSION_UNKNOWN"
    return unknown, attempt


def test_an_unknown_submission_is_not_applied_and_asks_for_verification(
    client: TestClient, auth: dict[str, str], db_session: Session, site: Any
) -> None:
    unknown, attempt = unknown_attempt(client, auth, db_session, site)

    tracking = tracking_of(unknown)
    assert tracking["status"] == "PREPARING" and tracking["applied_at"] is None
    assert tracking["submission"]["result"] == "SUBMISSION_UNKNOWN"
    assert tracking["submission"]["needs_verification"] is True
    rows = assert_history_is_consistent(db_session, unknown.aid)
    assert [r.new_status.value for r in rows] == ["SAVED", "PREPARING"]  # nothing new
    entry = timeline(client, auth, unknown.aid)[0]
    assert entry["kind"] == "SUBMISSION" and entry["submission"]["result"] == "SUBMISSION_UNKNOWN"
    assert entry["submission"]["user_verified_outcome"] is None


def test_the_user_can_decide_the_status_of_an_unknown_submission_without_rewriting_it(
    client: TestClient, auth: dict[str, str], db_session: Session, site: Any
) -> None:
    unknown, attempt = unknown_attempt(client, auth, db_session, site)
    before = attempt_columns(db_session, attempt["id"])

    # They checked their email themselves and mark it applied through tracking.
    assert set_status(client, auth, unknown.aid, "APPLIED", note="Got a mail.").status_code == 200

    rows = assert_history_is_consistent(db_session, unknown.aid)
    assert rows[-1].source.value == "USER" and rows[-1].note == "Got a mail."
    assert attempt_columns(db_session, attempt["id"]) == before  # still UNKNOWN, unverified
    assert tracking_of(unknown)["submission"]["needs_verification"] is True


def test_verifying_an_unknown_submission_as_submitted_records_applied_from_the_submission(
    client: TestClient, auth: dict[str, str], db_session: Session, site: Any
) -> None:
    unknown, attempt = unknown_attempt(client, auth, db_session, site)
    verify = f"{base(unknown.aid)}/submissions/{attempt['id']}/verification"
    ok(client.post(verify, headers=auth, json={"outcome": "SUBMITTED"}))

    assert tracking_of(unknown)["status"] == "APPLIED"
    rows = assert_history_is_consistent(db_session, unknown.aid)
    last = rows[-1]
    assert last.source.value == "SUBMISSION" and last.actor.value == "USER"
    assert str(last.submission_attempt_id) == attempt["id"]
    entry = next(i for i in timeline(client, auth, unknown.aid) if i["kind"] == "SUBMISSION")
    assert entry["submission"]["result"] == "SUBMISSION_UNKNOWN"  # the fact is not rewritten
    assert entry["submission"]["user_verified_outcome"] == "SUBMITTED"
    assert tracking_of(unknown)["submission"]["needs_verification"] is False


def test_verifying_as_not_submitted_leaves_the_application_in_preparation(
    client: TestClient, auth: dict[str, str], db_session: Session, site: Any
) -> None:
    unknown, attempt = unknown_attempt(client, auth, db_session, site)
    verify = f"{base(unknown.aid)}/submissions/{attempt['id']}/verification"
    ok(client.post(verify, headers=auth, json={"outcome": "NOT_SUBMITTED"}))

    assert tracking_of(unknown)["status"] == "PREPARING"
    assert [r.new_status.value for r in assert_history_is_consistent(db_session, unknown.aid)] == [
        "SAVED",
        "PREPARING",
    ]


def test_verifying_as_submitted_after_the_user_already_marked_applied_adds_no_second_applied(
    client: TestClient, auth: dict[str, str], db_session: Session, site: Any
) -> None:
    unknown, attempt = unknown_attempt(client, auth, db_session, site)
    assert set_status(client, auth, unknown.aid, "APPLIED").status_code == 200
    before = len(history_rows(db_session, unknown.aid))
    verify = f"{base(unknown.aid)}/submissions/{attempt['id']}/verification"
    ok(client.post(verify, headers=auth, json={"outcome": "SUBMITTED"}))
    assert len(history_rows(db_session, unknown.aid)) == before
    assert tracking_of(unknown)["status"] == "APPLIED"


# --- A failure before the press -------------------------------------------------------------------


def test_a_failure_before_the_press_does_not_make_the_application_applied(ctx: SubmitCtx) -> None:
    authorization = ctx.ready_authorization()
    ctx.human._run(  # noqa: SLF001 - an overlay covers the button (as in the V0.11 test)
        "() => { const b = document.getElementById('submit-btn').getBoundingClientRect();"
        " const o = document.createElement('div');"
        " Object.assign(o.style, {position: 'absolute', left: (b.left + scrollX - 5) + 'px',"
        " top: (b.top + scrollY - 5) + 'px', width: (b.width + 10) + 'px',"
        " height: (b.height + 10) + 'px', background: 'white', zIndex: 9999});"
        " document.body.appendChild(o); }"
    )
    attempt = ok(ctx.submit(authorization["id"]))
    assert attempt["result"] == "FAILED_BEFORE_SUBMIT"

    tracking = tracking_of(ctx)
    assert tracking["status"] == "PREPARING" and tracking["applied_at"] is None
    assert tracking["submission"]["result"] == "FAILED_BEFORE_SUBMIT"
    assert tracking["submission"]["needs_verification"] is False
    assert [r.new_status.value for r in assert_history_is_consistent(ctx.db, ctx.aid)] == [
        "SAVED",
        "PREPARING",
    ]
    assert timeline(ctx.client, ctx.auth, ctx.aid)[0]["kind"] == "SUBMISSION"
    assert ctx.site.submit_clicks == 0


# --- Ownership ------------------------------------------------------------------------------------


def test_another_user_cannot_read_the_submission_timeline(
    ctx: SubmitCtx, other_auth: dict[str, str]
) -> None:
    ok(ctx.submit(ctx.ready_authorization()["id"]))
    response = ctx.client.get(f"{API}/applications/{ctx.aid}/timeline", headers=other_auth)
    assert response.status_code == 404
    assert "EXM-2026-0001" not in response.text
    listed = ok(ctx.client.get(f"{API}/applications", headers=other_auth))
    assert listed["items"] == []
