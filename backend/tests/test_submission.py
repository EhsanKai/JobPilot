"""V0.11 review & submit, end to end through the real API and a real (headless) Chromium on
the fake employer's `/submit-apply` form, whose final button really submits it.

    final review -> consents -> explicit authorization -> one press -> verified result

What is proven here: nothing is submitted without a current review and an explicit,
unexpired, unused authorization of exactly that state; every relevant change invalidates
them; the press happens at most once (also under concurrent requests); success needs
evidence; an uncertain result is SUBMISSION_UNKNOWN and is never retried; and the fake site's
press counter agrees with every claim.
"""

import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.main import app
from app.models import (
    Application,
    AutomationEvent,
    AutomationField,
    FinalReview,
    MappingStatus,
    SubmissionAttempt,
    SubmissionAuthorization,
)
from app.services.automation import documents, submission
from app.services.automation.browser.base import BrowserError
from app.services.automation.browser.playwright_driver import PlaywrightFinalSubmission
from tests.application_helpers import API, user_by_email
from tests.automation_helpers import FakeAutomationAI, Human, base, session_url
from tests.conftest import TestSessionLocal
from tests.document_helpers import generate
from tests.fake_site import FakeApplicationSite
from tests.question_helpers import error, ok
from tests.submission_helpers import (
    PRIVACY,
    TALENT_POOL,
    SubmitCtx,
    make_submit_ctx,
    post_action,
)

WHY = "Why do you want to join us?"


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


def codes(issues: list[dict[str, Any]]) -> set[str]:
    return {i["code"] for i in issues}


def blockers(ctx: SubmitCtx) -> set[str]:
    return codes(ctx.final_review()["current"]["blockers"])


def authorization_row(db: Session, authorization_id: str) -> SubmissionAuthorization:
    row = db.scalar(
        select(SubmissionAuthorization)
        .where(SubmissionAuthorization.id == uuid.UUID(authorization_id))
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


def review_row(db: Session, review_id: str) -> FinalReview:
    row = db.scalar(
        select(FinalReview)
        .where(FinalReview.id == uuid.UUID(review_id))
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


def label_text(human: Human, element_id: str, text: str) -> None:
    """Change the visible text of a checkbox's label (the page rewords a consent)."""
    human._run(  # noqa: SLF001
        "args => { const label = document.getElementById(args[0]).parentElement;"
        " label.lastChild.textContent = ' ' + args[1]; }",
        element_id,
        text,
    )


# --- The happy path ------------------------------------------------------------------------------


def test_review_authorize_and_submit_exactly_once_with_a_verified_confirmation(
    ctx: SubmitCtx, db_session: Session
) -> None:
    ctx.prepare()
    review = ctx.final_review()
    current = review["current"]
    assert current["ready"] is True and current["blockers"] == []
    assert review["can_confirm_review"] is True and review["can_authorize"] is False

    # What will be submitted, where, from which source -- all visible before anything happens.
    fields = {f["label"]: f for f in current["fields"]}
    assert fields["First name"]["value"] == "Jane"
    assert fields["First name"]["source"] == "DETERMINISTIC"
    assert fields[WHY]["value"] == "Because I like integration work."
    assert fields[WHY]["source"] == "USER" and fields[WHY]["verification"] == "VERIFIED"
    assert fields["Are you legally authorized to work in Germany?"]["value_label"] == "Yes"
    assert all(f["verification"] in ("VERIFIED", "EMPTY") for f in current["fields"])
    cv = next(d for d in current["documents"] if d["label"] == "Resume / CV")
    assert cv["document_type"] == "tailored_cv" and cv["version_number"] == 1
    assert cv["verification"] == "VERIFIED" and len(cv["content_hash"]) == 64
    consents = {c["text"]: c for c in current["consents"]}
    assert consents[PRIVACY]["authorized"] and consents[PRIVACY]["given"]
    assert not consents[TALENT_POOL]["authorized"] and not consents[TALENT_POOL]["given"]
    assert current["submit_control"] == {"label": "Submit application"}
    assert current["destination"]["host"] == "127.0.0.1"
    assert current["destination"]["company"] == "Example GmbH"
    assert "?" not in current["destination"]["url"]
    assert ctx.site.submit_clicks == 0  # reviewing presses nothing

    confirmed = ok(ctx.confirm_review(current["state_hash"]), 201)
    assert confirmed["status"] == "ACTIVE" and confirmed["state_hash"] == current["state_hash"]
    again = ctx.final_review()
    assert again["review_is_current"] is True and again["can_authorize"] is True
    assert ctx.site.submit_clicks == 0

    authorization = ok(ctx.authorize(confirmed), 201)
    assert authorization["status"] == "AUTHORIZED"
    assert authorization["reviewed_state_hash"] == current["state_hash"]
    assert authorization["destination_host"] == "127.0.0.1"
    assert ctx.site.submit_clicks == 0  # authorizing presses nothing either

    attempt = ok(ctx.submit(authorization["id"]))
    assert attempt["result"] == "SUCCESS"
    assert attempt["message"] == "Application submitted successfully."
    assert attempt["confirmation_reference"] == "EXM-2026-0001"
    assert attempt["confirmation_summary"]
    assert attempt["submit_triggered_at"] and attempt["finished_at"]
    assert attempt["reviewed_state_hash"] == current["state_hash"]
    assert attempt["company"] == "Example GmbH"
    assert ctx.site.submit_clicks == 1 and ctx.site.received == ["EXM-2026-0001"]

    # The authorization is spent, the review used, the application submitted, the assistant done.
    assert authorization_row(db_session, authorization["id"]).status.value == "CONSUMED"
    assert review_row(db_session, confirmed["id"]).status.value == "USED"
    application = db_session.get(Application, uuid.UUID(ctx.aid), populate_existing=True)
    assert application is not None and application.status.value == "submitted"
    session = ctx.session_now()
    assert session["status"] == "STOPPED"

    # Never twice with one authorization.
    error(ctx.submit(authorization["id"]), 409, "AUTHORIZATION_CONSUMED")
    assert ctx.site.submit_clicks == 1 and len(ctx.site.received) == 1
    listed = ctx.attempts()
    assert listed["submitted"] is True and len(listed["items"]) == 1

    # The audit trail holds codes only -- no submitted value.
    events = db_session.scalars(
        select(AutomationEvent).where(AutomationEvent.session_id == uuid.UUID(ctx.sid))
    )
    types = [e.event_type for e in events]
    for expected in (
        "CONSENT_AUTHORIZED",
        "FINAL_REVIEW_CREATED",
        "SUBMISSION_AUTHORIZED",
        "SUBMISSION_STARTED",
        "SUBMISSION_TRIGGERED",
        "SUBMISSION_CONFIRMED",
    ):
        assert expected in types
    logged = str([e.data for e in db_session.scalars(select(AutomationEvent))])
    assert "integration work" not in logged and "jane@example.com" not in logged
    row = db_session.scalar(select(SubmissionAttempt))
    assert row is not None
    stored = " ".join(str(getattr(row, c.key)) for c in SubmissionAttempt.__table__.columns)
    assert "integration work" not in stored and "jane@example.com" not in stored


def test_an_application_that_was_submitted_cannot_be_submitted_again(
    ctx: SubmitCtx, client: TestClient, auth: dict[str, str]
) -> None:
    authorization = ctx.ready_authorization()
    assert ok(ctx.submit(authorization["id"]))["result"] == "SUCCESS"
    # A new browser session of the same application: blocked before anything else.
    fresh = ok(client.post(f"{base(ctx.aid)}/start", headers=auth), 201)
    again = SubmitCtx(client, auth, ctx.db, ctx.site, ctx.app, fresh)
    assert "ALREADY_SUBMITTED" in blockers(again)
    assert ctx.site.submit_clicks == 1


# --- Authorization is refused while anything is unresolved ----------------------------------------


def test_an_incomplete_application_cannot_be_reviewed_or_authorized(ctx: SubmitCtx) -> None:
    found = blockers(ctx)
    assert {"NEEDS_INPUT", "DOCUMENT_NOT_ATTACHED", "CONSENT_NOT_AUTHORIZED"} & found
    review = ctx.final_review()
    assert review["can_confirm_review"] is False
    body = error(ctx.confirm_review(review["current"]["state_hash"]), 409, "REVIEW_BLOCKED")
    assert body["details"]["blockers"]
    assert ctx.site.submit_clicks == 0


def test_an_unanswered_field_blocks_the_review(ctx: SubmitCtx) -> None:
    ctx.prepare()
    ok(post_action(ctx, "take-control"))
    ctx.human.type_into("#motivation", "")
    ok(post_action(ctx, "resume"))
    # The user emptied it: an empty answered field is not what was approved.
    found = blockers(ctx)
    assert found & {"REQUIRED_FIELD_EMPTY", "NOT_FILLED_YET", "NEEDS_INPUT"}


def test_an_unresolved_mapping_blocks_the_review(ctx: SubmitCtx, db_session: Session) -> None:
    ctx.prepare()
    row = db_session.scalar(
        select(AutomationField).where(
            AutomationField.session_id == uuid.UUID(ctx.sid),
            AutomationField.label == "Phone number",
        )
    )
    assert row is not None
    row.mapping_status = MappingStatus.NEEDS_REVIEW
    db_session.commit()
    assert "MAPPING_NEEDS_REVIEW" in blockers(ctx)


def test_a_missing_required_consent_blocks_the_review(ctx: SubmitCtx) -> None:
    ctx.prepare(consent=False)
    assert "CONSENT_NOT_AUTHORIZED" in blockers(ctx)
    # Ticking it in the browser is not an authorization in JobPilot.
    ctx.human.check("#privacy")
    assert "CONSENT_NOT_AUTHORIZED" in blockers(ctx)
    error(ctx.confirm_review(), 409, "REVIEW_BLOCKED")
    assert ctx.site.submit_clicks == 0


def test_a_review_is_confirmed_only_for_the_state_the_user_saw(ctx: SubmitCtx) -> None:
    ctx.prepare()
    error(ctx.confirm_review("0" * 64), 409, "REVIEW_CHANGED")


def test_authorization_needs_the_reviewed_hash_and_an_explicit_yes(ctx: SubmitCtx) -> None:
    ctx.prepare()
    review = ok(ctx.confirm_review(), 201)
    error(ctx.authorize(review, reviewed_state_hash="1" * 64), 409, "REVIEW_HASH_MISMATCH")
    assert ctx.authorize(review, confirm=False).status_code == 422
    body = {"final_review_id": review["id"], "reviewed_state_hash": review["state_hash"]}
    response = ctx.client.post(
        f"{session_url(ctx.aid, ctx.sid)}/submission-authorization", headers=ctx.auth, json=body
    )
    assert response.status_code == 422  # no "confirm": no authorization
    first = ok(ctx.authorize(review), 201)
    error(ctx.authorize(review), 409, "ALREADY_AUTHORIZED")
    assert first["status"] == "AUTHORIZED"


def test_a_stale_review_cannot_be_authorized(ctx: SubmitCtx, db_session: Session) -> None:
    ctx.prepare()
    review = ok(ctx.confirm_review(), 201)
    ctx.human.type_into("#phone", "+49 30 999999")  # the user edits a field after the review
    error(ctx.authorize(review), 409, "STATE_CHANGED")
    assert review_row(db_session, review["id"]).status.value == "INVALIDATED"
    error(ctx.authorize(review), 409, "REVIEW_NOT_CURRENT")
    assert ctx.site.submit_clicks == 0


def test_a_changed_answer_blocks_authorization(ctx: SubmitCtx) -> None:
    ctx.prepare()
    review = ok(ctx.confirm_review(), 201)
    ctx.answer(WHY, "A different reason.")  # changed in the Questions section, not the page
    response = ctx.authorize(review)
    assert response.status_code == 409
    assert response.json()["error"]["code"] in ("AUTHORIZATION_BLOCKED", "STATE_CHANGED")
    assert "ANSWER_CHANGED" in blockers(ctx)
    assert ctx.site.submit_clicks == 0


def test_a_changed_document_invalidates_the_review(ctx: SubmitCtx, db_session: Session) -> None:
    ctx.prepare()
    review = ok(ctx.confirm_review(), 201)
    assert generate(ctx.client, ctx.auth, ctx.aid, "cv").status_code == 201  # a v2 appears
    # The pinned v1 is still what is attached: a newer version never replaces it by itself.
    current = ctx.final_review()["current"]
    cv = next(d for d in current["documents"] if d["label"] == "Resume / CV")
    assert cv["version_number"] == 1
    assert "NEWER_DOCUMENT_VERSION" in codes(current["warnings"])
    # Choosing the newer version is a change: the review is gone.
    field = ctx.field("Resume / CV")
    choices = ok(
        ctx.client.get(
            f"{session_url(ctx.aid, ctx.sid)}/fields/{field['id']}/documents", headers=ctx.auth
        )
    )["items"]
    v2 = next(c for c in choices if c["type"] == "tailored_cv" and c["version_number"] == 2)
    ok(
        ctx.client.put(
            f"{session_url(ctx.aid, ctx.sid)}/fields/{field['id']}/document",
            headers=ctx.auth,
            json={"document_type": "tailored_cv", "document_id": v2["id"]},
        )
    )
    row = review_row(db_session, review["id"])
    assert row.status.value == "INVALIDATED" and row.invalidation_reason == "DOCUMENT_CHANGED"
    error(ctx.authorize(review), 409, "REVIEW_NOT_CURRENT")


def test_a_page_on_another_website_cannot_be_authorized(ctx: SubmitCtx) -> None:
    ctx.prepare()
    review = ok(ctx.confirm_review(), 201)
    ctx.human.goto(ctx.site.url("/submit-apply", host="localhost"))
    ctx.session_now()  # the assistant notices the other website
    assert "UNEXPECTED_DOMAIN" in blockers(ctx)
    error(ctx.authorize(review), 409, "REVIEW_NOT_CURRENT")
    assert ctx.site.submit_clicks == 0


def test_a_security_check_blocks_review_and_authorization(ctx: SubmitCtx) -> None:
    ctx.prepare()
    review = ok(ctx.confirm_review(), 201)
    ctx.human._run(  # noqa: SLF001 - the site shows a CAPTCHA widget
        "() => { const d = document.createElement('div'); d.className = 'g-recaptcha';"
        " d.style.width = '304px'; d.style.height = '78px'; document.body.prepend(d); }"
    )
    assert "SECURITY_CHALLENGE" in blockers(ctx)
    assert ctx.session_now()["status"] == "BLOCKED"
    error(ctx.authorize(review), 409, "REVIEW_NOT_CURRENT")
    assert ctx.site.submit_clicks == 0


def test_a_blocked_session_cannot_be_authorized(ctx: SubmitCtx) -> None:
    ctx.prepare()
    review = ok(ctx.confirm_review(), 201)
    ok(post_action(ctx, "pause"))
    assert "SESSION_PAUSED" in blockers(ctx)
    error(ctx.authorize(review), 409, "REVIEW_NOT_CURRENT")


def test_another_users_review_authorization_and_submission_are_not_found(
    ctx: SubmitCtx, other_auth: dict[str, str]
) -> None:
    authorization = ctx.ready_authorization()
    review_id = authorization["final_review_id"]
    url = session_url(ctx.aid, ctx.sid)
    client = ctx.client
    for response in (
        client.get(f"{url}/final-review", headers=other_auth),
        client.post(
            f"{url}/final-review", headers=other_auth, json={"expected_state_hash": "0" * 64}
        ),
        client.post(
            f"{url}/submission-authorization",
            headers=other_auth,
            json={"final_review_id": review_id, "reviewed_state_hash": "0" * 64, "confirm": True},
        ),
        client.post(
            f"{url}/submit", headers=other_auth, json={"authorization_id": authorization["id"]}
        ),
        client.get(f"{base(ctx.aid)}/submissions", headers=other_auth),
    ):
        assert response.status_code == 404, response.text
    assert ctx.site.submit_clicks == 0
    assert authorization_row(ctx.db, authorization["id"]).status.value == "AUTHORIZED"


# --- Invalidation: any relevant change ends the review and the authorization ----------------------


def _authorized(ctx: SubmitCtx) -> tuple[dict[str, Any], dict[str, Any]]:
    authorization = ctx.ready_authorization()
    return authorization, {"id": authorization["final_review_id"]}


@pytest.mark.parametrize(
    ("action", "reason"),
    [
        ("take-control", "USER_TAKEOVER"),
        ("pause", "PAUSED"),
        ("fill", "FILL"),
    ],
)
def test_user_actions_invalidate_the_authorization(
    ctx: SubmitCtx, db_session: Session, action: str, reason: str
) -> None:
    authorization, review = _authorized(ctx)
    ok(post_action(ctx, action))
    row = authorization_row(db_session, authorization["id"])
    assert row.status.value == "INVALIDATED" and row.invalidation_reason == reason
    assert review_row(db_session, review["id"]).status.value == "INVALIDATED"
    error(ctx.submit(authorization["id"]), 409, "AUTHORIZATION_INVALIDATED")
    if action != "fill":
        ok(post_action(ctx, "resume"))  # handing back control does not restore anything
        assert authorization_row(db_session, authorization["id"]).status.value == "INVALIDATED"
    assert ctx.site.submit_clicks == 0


def test_a_mapping_change_invalidates_the_authorization(
    ctx: SubmitCtx, db_session: Session
) -> None:
    authorization, _ = _authorized(ctx)
    phone = ctx.field("Phone number")
    ok(
        ctx.client.post(
            f"{session_url(ctx.aid, ctx.sid)}/fields/{phone['id']}/mapping",
            headers=ctx.auth,
            json={"action": "unmap"},
        )
    )
    row = authorization_row(db_session, authorization["id"])
    assert row.invalidation_reason == "MAPPING_CHANGED"
    error(ctx.submit(authorization["id"]), 409, "AUTHORIZATION_INVALIDATED")


def test_a_consent_change_invalidates_the_authorization(
    ctx: SubmitCtx, db_session: Session
) -> None:
    authorization, _ = _authorized(ctx)
    privacy = ctx.consent_item(PRIVACY)
    response = ctx.client.delete(
        f"{session_url(ctx.aid, ctx.sid)}/consents/{privacy['field_id']}", headers=ctx.auth
    )
    assert response.status_code == 204
    assert (
        authorization_row(db_session, authorization["id"]).invalidation_reason == "CONSENT_CHANGED"
    )
    assert ctx.human.is_checked("#privacy") is False  # withdrawn, so JobPilot unticked it
    assert "CONSENT_NOT_AUTHORIZED" in blockers(ctx)


def test_a_document_change_invalidates_the_authorization(
    ctx: SubmitCtx, db_session: Session
) -> None:
    authorization, _ = _authorized(ctx)
    field = ctx.field("Resume / CV")
    response = ctx.client.delete(
        f"{session_url(ctx.aid, ctx.sid)}/fields/{field['id']}/document", headers=ctx.auth
    )
    assert response.status_code == 200
    assert (
        authorization_row(db_session, authorization["id"]).invalidation_reason == "DOCUMENT_CHANGED"
    )


def test_a_reload_invalidates_the_authorization(ctx: SubmitCtx, db_session: Session) -> None:
    authorization, review = _authorized(ctx)

    def reload(page: Any) -> Any:
        return page.reload()

    ctx.human._page_do(reload)  # noqa: SLF001
    ctx.session_now()  # the assistant looks at its browser
    row = authorization_row(db_session, authorization["id"])
    assert row.status.value == "INVALIDATED" and row.invalidation_reason == "NAVIGATION"
    error(ctx.submit(authorization["id"]), 409, "AUTHORIZATION_INVALIDATED")
    assert ctx.site.submit_clicks == 0


def test_an_edit_after_authorization_stops_the_submission_before_the_press(
    ctx: SubmitCtx,
) -> None:
    authorization = ctx.ready_authorization()
    ctx.human.type_into("#motivation", "Something I never reviewed.")
    attempt = ok(ctx.submit(authorization["id"]))
    assert attempt["result"] == "FAILED_BEFORE_SUBMIT"
    assert attempt["failure_code"] == "STATE_CHANGED"
    assert attempt["submit_triggered_at"] is None
    assert attempt["message"].startswith("The application could not be submitted")
    assert ctx.site.submit_clicks == 0 and ctx.site.received == []
    # The authorization was used up by that attempt: it cannot be retried with it.
    error(ctx.submit(authorization["id"]), 409, "AUTHORIZATION_CONSUMED")


def test_a_take_over_in_the_last_moment_wins(ctx: SubmitCtx, db_session: Session) -> None:
    authorization = ctx.ready_authorization()
    ok(post_action(ctx, "take-control"))
    error(ctx.submit(authorization["id"]), 409, "AUTHORIZATION_INVALIDATED")
    assert ctx.site.submit_clicks == 0


# --- Submitting: never without a valid authorization ----------------------------------------------


def test_nothing_is_submitted_without_an_authorization(ctx: SubmitCtx) -> None:
    ctx.prepare()
    ok(ctx.confirm_review(), 201)
    error(ctx.submit(str(uuid.uuid4())), 404, "NOT_FOUND")
    assert ctx.site.submit_clicks == 0


def test_an_expired_authorization_is_refused(ctx: SubmitCtx, db_session: Session) -> None:
    authorization = ctx.ready_authorization()
    row = authorization_row(db_session, authorization["id"])
    row.created_at = datetime.now(UTC) - timedelta(hours=2)
    row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()
    error(ctx.submit(authorization["id"]), 409, "AUTHORIZATION_EXPIRED")
    assert authorization_row(db_session, authorization["id"]).status.value == "EXPIRED"
    assert ctx.site.submit_clicks == 0


def test_an_authorization_of_another_application_or_session_is_refused(
    ctx: SubmitCtx, client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    authorization = ctx.ready_authorization()
    # Another application of the same user.
    other = make_submit_ctx(client, auth, db_session, ctx.site)
    error(other.submit(authorization["id"]), 404, "NOT_FOUND")
    # An ended session of the same application.
    ok(post_action(other, "stop"))
    old_session = ctx.sid
    response = client.post(
        f"{session_url(other.aid, old_session)}/submit",
        headers=auth,
        json={"authorization_id": authorization["id"]},
    )
    assert response.status_code == 404
    assert ctx.site.submit_clicks == 0


def test_an_authorization_of_another_session_of_the_same_application_is_refused(
    ctx: SubmitCtx, client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    authorization = ctx.ready_authorization()
    first_session = ctx.sid
    ok(post_action(ctx, "stop"))  # invalidates it -- and a stopped session is not the place
    second = ok(client.post(f"{base(ctx.aid)}/start", headers=auth), 201)
    response = client.post(
        f"{session_url(ctx.aid, second['id'])}/submit",
        headers=auth,
        json={"authorization_id": authorization["id"]},
    )
    error(response, 409, "WRONG_SESSION")
    assert first_session != second["id"]
    assert ctx.site.submit_clicks == 0


def test_a_tampered_destination_is_refused_before_the_press(
    ctx: SubmitCtx, db_session: Session
) -> None:
    authorization = ctx.ready_authorization()
    row = authorization_row(db_session, authorization["id"])
    row.destination_host = "evil.example.org"
    db_session.commit()
    attempt = ok(ctx.submit(authorization["id"]))
    assert attempt["result"] == "FAILED_BEFORE_SUBMIT"
    assert attempt["failure_code"] == "WRONG_DESTINATION"
    assert ctx.site.submit_clicks == 0


def test_the_submit_request_takes_no_url_selector_or_values(ctx: SubmitCtx) -> None:
    authorization = ctx.ready_authorization()
    for extra in (
        {"url": "https://evil.example.org/apply"},
        {"button_selector": "#submit-btn"},
        {"form_data": {"email": "x@example.com"}},
        {"session_id": ctx.sid},
    ):
        assert ctx.submit(authorization["id"], **extra).status_code == 422
    assert ctx.site.submit_clicks == 0
    for path, body in (
        ("final-review", {"expected_state_hash": "0" * 64, "url": "x"}),
        (
            f"consents/{ctx.consent_item(PRIVACY)['field_id']}",
            {"consent_text_hash": "0" * 64, "selector": "#privacy"},
        ),
    ):
        response = ctx.client.post(
            f"{session_url(ctx.aid, ctx.sid)}/{path}", headers=ctx.auth, json=body
        )
        assert response.status_code == 422


def test_there_is_no_generic_click_endpoint() -> None:
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not [p for p in paths if re.search(r"click|press|evaluate|script|navigate|goto", p)]
    automation = sorted({p.split("/automation", 1)[1] for p in paths if "/automation" in p})
    assert "/{session_id}/submit" in automation
    assert all(not p.endswith("/click") for p in automation)


# --- Exactly once --------------------------------------------------------------------------------


def test_concurrent_submissions_press_the_button_once(ctx: SubmitCtx) -> None:
    authorization = ctx.ready_authorization()
    barrier = threading.Barrier(2)
    outcomes: list[Any] = []

    def run() -> None:
        own = TestSessionLocal()
        try:
            user = user_by_email(own)
            barrier.wait()
            attempt = submission.submit(
                own,
                user,
                uuid.UUID(ctx.aid),
                uuid.UUID(ctx.sid),
                uuid.UUID(authorization["id"]),
            )
            outcomes.append(attempt.result.value if attempt.result else None)
        except AppError as exc:
            outcomes.append(exc.code)
        finally:
            own.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert sorted(outcomes) == sorted(["SUCCESS", "AUTHORIZATION_CONSUMED"]) or sorted(
        outcomes
    ) == sorted(["SUCCESS", "SUBMISSION_IN_PROGRESS"]), outcomes
    assert ctx.site.submit_clicks == 1 and len(ctx.site.received) == 1
    rows = list(ctx.db.scalars(select(SubmissionAttempt).execution_options(populate_existing=True)))
    assert len(rows) == 1
    assert authorization_row(ctx.db, authorization["id"]).status.value == "CONSUMED"


def test_the_database_allows_one_attempt_per_authorization(ctx: SubmitCtx) -> None:
    authorization = ctx.ready_authorization()
    ok(ctx.submit(authorization["id"]))
    row = ctx.db.scalar(select(SubmissionAttempt))
    assert row is not None
    from sqlalchemy.exc import IntegrityError

    duplicate = SubmissionAttempt(
        user_id=row.user_id,
        application_id=row.application_id,
        session_id=row.session_id,
        authorization_id=row.authorization_id,
        final_review_id=row.final_review_id,
        reviewed_state_hash=row.reviewed_state_hash,
        destination_domain=row.destination_domain,
        destination_url=row.destination_url,
        started_at=datetime.now(UTC),
    )
    ctx.db.add(duplicate)
    with pytest.raises(IntegrityError, match="uq_automation_submission_attempts_authorization"):
        ctx.db.flush()
    ctx.db.rollback()


def test_a_finished_attempt_is_append_only(ctx: SubmitCtx) -> None:
    from app.models.mixins import ImmutableRecordError

    authorization = ctx.ready_authorization()
    ok(ctx.submit(authorization["id"]))
    row = ctx.db.scalar(select(SubmissionAttempt).execution_options(populate_existing=True))
    assert row is not None
    row.confirmation_reference = "FORGED-1"
    with pytest.raises(ImmutableRecordError):
        ctx.db.flush()
    ctx.db.rollback()


# --- Consents ------------------------------------------------------------------------------------


def test_each_consent_is_authorized_on_its_own_and_ticked_only_then(ctx: SubmitCtx) -> None:
    ctx.prepare(consent=False)
    assert ctx.human.is_checked("#privacy") is False
    assert ctx.human.is_checked("#talent_pool") is False
    assert ctx.consent(PRIVACY).status_code == 204
    assert ctx.human.is_checked("#privacy") is True
    # Authorizing one consent says nothing about another.
    assert ctx.human.is_checked("#talent_pool") is False
    consents = {c["text"]: c for c in ctx.final_review()["current"]["consents"]}
    assert consents[PRIVACY]["authorized"] and not consents[TALENT_POOL]["authorized"]
    # A fill pass still never ticks a consent by itself.
    ok(post_action(ctx, "fill"))
    assert ctx.human.is_checked("#talent_pool") is False


def test_a_consent_can_be_authorized_without_being_ticked(ctx: SubmitCtx) -> None:
    ctx.prepare(consent=False)
    assert ctx.consent(PRIVACY, give=False).status_code == 204
    assert ctx.human.is_checked("#privacy") is False
    assert "CONSENT_NOT_GIVEN" in blockers(ctx)
    ctx.human.check("#privacy")  # the user ticks it themselves
    assert blockers(ctx) == set()


def test_a_consent_ticked_but_not_authorized_blocks_submission(ctx: SubmitCtx) -> None:
    ctx.prepare()
    ctx.human.check("#talent_pool")  # an optional consent, given in the page only
    assert "CONSENT_GIVEN_WITHOUT_AUTHORIZATION" in blockers(ctx)


def test_a_consent_is_authorized_only_for_the_text_the_user_saw(ctx: SubmitCtx) -> None:
    ctx.prepare(consent=False)
    error(ctx.consent(PRIVACY, text_hash="a" * 64), 409, "CONSENT_TEXT_CHANGED")
    assert ctx.human.is_checked("#privacy") is False


def test_a_changed_consent_text_invalidates_review_and_authorization(
    ctx: SubmitCtx, db_session: Session
) -> None:
    authorization, review = _authorized(ctx)
    old_hash = ctx.consent_item(PRIVACY)["text_hash"]
    label_text(ctx.human, "privacy", "I agree that my data is shared with partners *")
    current = ctx.final_review()
    assert "CONSENT_TEXT_CHANGED" in codes(current["current"]["blockers"])
    assert review_row(db_session, review["id"]).status.value == "INVALIDATED"
    assert authorization_row(db_session, authorization["id"]).status.value == "INVALIDATED"
    # The old authorization does not carry over to the new words.
    privacy = next(
        c for c in current["current"]["consents"] if c["text"].startswith("I agree that")
    )
    assert privacy["authorized"] is False and privacy["text_hash"] != old_hash
    error(ctx.submit(authorization["id"]), 409, "AUTHORIZATION_INVALIDATED")
    assert ctx.site.submit_clicks == 0


def test_a_consent_is_never_a_question_or_a_generic_yes(ctx: SubmitCtx) -> None:
    ctx.prepare(consent=False)
    fields = ctx.fields()
    privacy = next(f for f in fields if f["label"].startswith("I agree to the processing"))
    assert privacy["question"] is None and privacy["user_action_reason"] == "LEGAL_CONSENT"
    # There is no endpoint that authorizes every consent at once.
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not [p for p in paths if "consents" in p and "{field_id}" not in p]


# --- The result: evidence, or UNKNOWN -- never a retry --------------------------------------------


def test_an_ambiguous_result_is_unknown_and_never_retried(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    ctx = make_submit_ctx(client, auth, db_session, site, path="/submit-ambiguous")
    authorization = ctx.ready_authorization()
    attempt = ok(ctx.submit(authorization["id"]))
    assert attempt["result"] == "SUBMISSION_UNKNOWN"
    assert attempt["failure_code"] == "NO_CONFIRMATION"
    assert attempt["submit_triggered_at"] is not None
    assert "will NOT submit again automatically" in attempt["message"]
    assert site.submit_clicks == 1  # pressed once ...
    error(ctx.submit(authorization["id"]), 409, "AUTHORIZATION_CONSUMED")
    assert site.submit_clicks == 1  # ... and never again
    listed = ctx.attempts()
    assert listed["needs_verification"] is True and listed["submitted"] is False

    # A new session cannot submit until the user has checked what happened.
    fresh = ok(client.post(f"{base(ctx.aid)}/start", headers=auth), 201)
    again = SubmitCtx(client, auth, db_session, site, ctx.app, fresh)
    assert "PREVIOUS_SUBMISSION_UNKNOWN" in blockers(again)
    verify = f"{base(ctx.aid)}/submissions/{attempt['id']}/verification"
    assert client.post(verify, headers=auth, json={"outcome": "MAYBE"}).status_code == 422
    checked = ok(client.post(verify, headers=auth, json={"outcome": "NOT_SUBMITTED"}))
    assert checked["user_verified_outcome"] == "NOT_SUBMITTED"
    error(client.post(verify, headers=auth, json={"outcome": "SUBMITTED"}), 409, "ALREADY_VERIFIED")
    assert "PREVIOUS_SUBMISSION_UNKNOWN" not in blockers(again)
    assert site.submit_clicks == 1


def test_a_user_who_finds_it_submitted_marks_the_application_submitted(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    ctx = make_submit_ctx(client, auth, db_session, site, path="/submit-ambiguous")
    attempt = ok(ctx.submit(ctx.ready_authorization()["id"]))
    verify = f"{base(ctx.aid)}/submissions/{attempt['id']}/verification"
    ok(client.post(verify, headers=auth, json={"outcome": "SUBMITTED"}))
    assert ctx.attempts()["submitted"] is True
    application = db_session.get(Application, uuid.UUID(ctx.aid), populate_existing=True)
    assert application is not None and application.status.value == "submitted"


def test_a_browser_that_disconnects_after_the_press_is_unknown(
    ctx: SubmitCtx, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = ctx.ready_authorization()
    reads = {"n": 0}
    original = PlaywrightFinalSubmission.read_outcome

    def read_outcome(self: PlaywrightFinalSubmission) -> Any:
        reads["n"] += 1
        if reads["n"] == 1:
            return original(self)  # what the page showed before the press
        self._session.close()  # noqa: SLF001 - the browser goes away after the press
        raise BrowserError("BROWSER_CLOSED", "The browser was closed.")

    monkeypatch.setattr(PlaywrightFinalSubmission, "read_outcome", read_outcome)
    attempt = ok(ctx.submit(authorization["id"]))
    assert attempt["result"] == "SUBMISSION_UNKNOWN"
    assert attempt["failure_code"] == "BROWSER_DISCONNECTED"
    assert ctx.site.submit_clicks == 1


def test_an_error_during_the_press_is_unknown_not_failed(
    ctx: SubmitCtx, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = ctx.ready_authorization()
    original = PlaywrightFinalSubmission.submit_final_application

    def press_then_fail(self: PlaywrightFinalSubmission, permit: Any) -> None:
        original(self, permit)
        raise BrowserError("TIMEOUT", "The page did not respond in time.")

    monkeypatch.setattr(PlaywrightFinalSubmission, "submit_final_application", press_then_fail)
    attempt = ok(ctx.submit(authorization["id"]))
    assert attempt["result"] == "SUBMISSION_UNKNOWN" and attempt["failure_code"] == "SUBMIT_ERROR"
    assert ctx.site.submit_clicks == 1


def test_a_button_that_cannot_be_pressed_fails_before_submit(ctx: SubmitCtx) -> None:
    authorization = ctx.ready_authorization()
    ctx.human._run(  # noqa: SLF001 - an overlay covers the button (not a form control)
        "() => { const b = document.getElementById('submit-btn').getBoundingClientRect();"
        " const o = document.createElement('div');"
        " Object.assign(o.style, {position: 'absolute', left: (b.left + scrollX - 5) + 'px',"
        " top: (b.top + scrollY - 5) + 'px', width: (b.width + 10) + 'px',"
        " height: (b.height + 10) + 'px', background: 'white', zIndex: 9999});"
        " document.body.appendChild(o); }"
    )
    attempt = ok(ctx.submit(authorization["id"]))
    assert attempt["result"] == "FAILED_BEFORE_SUBMIT"
    assert attempt["failure_code"] == "SUBMIT_CONTROL_NOT_READY"
    assert attempt["submit_triggered_at"] is None
    assert ctx.site.submit_clicks == 0


def test_two_possible_final_buttons_block_the_review(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    ctx = make_submit_ctx(client, auth, db_session, site, path="/submit-two-buttons")
    ctx.prepare()
    assert "MULTIPLE_SUBMIT_CONTROLS" in blockers(ctx)
    assert ctx.final_review()["current"]["submit_control"] is None
    assert site.submit_clicks == 0


def test_an_attempt_interrupted_by_a_restart_is_never_resumed(ctx: SubmitCtx) -> None:
    authorization = ctx.ready_authorization()
    row = authorization_row(ctx.db, authorization["id"])
    started = datetime.now(UTC) - timedelta(hours=1)
    row.status = row.status.__class__.CONSUMED
    row.consumed_at = started
    attempt = SubmissionAttempt(
        user_id=row.user_id,
        application_id=row.application_id,
        session_id=row.session_id,
        authorization_id=row.id,
        final_review_id=row.final_review_id,
        reviewed_state_hash=row.reviewed_state_hash,
        destination_domain="127.0.0.1",
        destination_url="http://127.0.0.1/submit-apply",
        started_at=started,
        submit_triggered_at=started,
    )
    ctx.db.add(attempt)
    ctx.db.commit()
    listed = ctx.attempts()["items"]
    assert listed[0]["result"] == "SUBMISSION_UNKNOWN"
    assert listed[0]["failure_code"] == "INTERRUPTED_AFTER_PRESS"
    assert ctx.site.submit_clicks == 0


def test_the_submissions_list_shows_safe_metadata_only(ctx: SubmitCtx) -> None:
    attempt = ok(ctx.submit(ctx.ready_authorization()["id"]))
    listed = ctx.client.get(
        f"{API}/applications/{ctx.aid}/automation/submissions", headers=ctx.auth
    )
    text = listed.text
    assert attempt["id"] in text
    for secret in ("cookie", "password", "token", "integration work", "jane@example.com", "?ref="):
        assert secret not in text.lower()
