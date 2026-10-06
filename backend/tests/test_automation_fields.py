"""V0.10 form discovery, mapping to `ApplicationQuestion`, answer approval, filling and
verification, and the user's control over what was written -- through the real API and a
real (headless) Chromium on a local fake employer website. No test calls a real AI.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ApplicationQuestion,
    AutomationEvent,
    AutomationField,
)
from tests.application_helpers import API
from tests.automation_helpers import (
    FakeAutomationAI,
    Human,
    answer_question,
    base,
    field_named,
    get_fields,
    get_session,
    make_application,
    post,
    session_url,
    started_session,
)
from tests.fake_site import FakeApplicationSite
from tests.question_helpers import add_question, error, ok

EMAIL = "Email address"
PHONE = "Phone number"
SALARY = "Expected annual salary (EUR)"
START = "Earliest start date"
WHY = "Why do you want to work for us?"


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


class Ctx:
    """An application with a running assistant on the fake form."""

    def __init__(
        self,
        client: TestClient,
        auth: dict[str, str],
        db: Session,
        site: FakeApplicationSite,
        application: dict[str, Any],
        session: dict[str, Any],
    ) -> None:
        self.client, self.auth, self.db, self.site = client, auth, db, site
        self.app, self.session = application, session
        self.aid, self.sid = application["id"], session["id"]
        self.human = Human(self.sid)

    def fields(self) -> list[dict[str, Any]]:
        return get_fields(self.client, self.auth, self.aid, self.sid)

    def field(self, label: str) -> dict[str, Any]:
        return field_named(self.fields(), label)

    def answer(self, label: str, text: str | None = None, options: list[str] | None = None) -> None:
        question = self.field(label)["question"]
        assert question is not None, label
        answer_question(self.client, self.auth, self.aid, question["id"], text, options)

    def fill(self, **body: Any) -> dict[str, Any]:
        return ok(post(self.client, self.auth, self.aid, self.sid, "fill", body or None))

    def act(self, action: str, body: dict[str, Any] | None = None) -> Any:
        return post(self.client, self.auth, self.aid, self.sid, action, body)

    def session_now(self) -> dict[str, Any]:
        return get_session(self.client, self.auth, self.aid, self.sid)

    def questions(self) -> list[dict[str, Any]]:
        response = self.client.get(f"{API}/applications/{self.aid}/questions", headers=self.auth)
        return ok(response)["items"]  # type: ignore[no-any-return]


def make_ctx(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    site: FakeApplicationSite,
    *,
    path: str = "/apply",
    prep: dict[str, Any] | None = None,
    resume: bool = False,
    before_start: Any = None,
) -> Ctx:
    application = make_application(client, auth, db, site, path=path, prep=prep, resume=resume)
    if before_start is not None:
        before_start(application)
    session = started_session(client, auth, application["id"])
    return Ctx(client, auth, db, site, application, session)


@pytest.fixture
def ctx(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> Ctx:
    return make_ctx(client, auth, db_session, site)


# --- Discovery -----------------------------------------------------------------------------------


def test_every_kind_of_control_is_discovered_with_neutral_metadata(ctx: Ctx) -> None:
    fields = {f["label"]: f for f in ctx.fields()}
    kinds = {label: f["input_type"] for label, f in fields.items()}
    assert kinds["First name"] == "text"
    assert kinds[EMAIL] == "email"
    assert kinds[PHONE] == "tel"
    assert kinds["LinkedIn profile"] == "url"
    assert kinds[SALARY] == "number"
    assert kinds[START] == "date"
    assert kinds["Application language"] == "select"
    assert kinds["Are you legally authorized to work in Germany?"] == "radio"
    assert kinds["Which technologies do you work with?"] == "checkbox_group"
    assert kinds["Open to relocation"] == "checkbox"
    assert kinds[WHY] == "textarea"
    assert kinds["Resume / CV"] == "file" and kinds["Cover letter"] == "file"

    email = fields[EMAIL]
    assert email["required"] is True and email["name"] == "email" and email["dom_id"] == "email"
    assert fields["Phone number"]["required"] is False
    assert fields[WHY]["max_length"] == 300 and fields["Postal code"]["max_length"] == 5
    assert fields["Country"]["options"] == [
        {"value": "DE", "label": "Germany"},
        {"value": "AT", "label": "Austria"},
        {"value": "CH", "label": "Switzerland"},
    ]
    assert fields["Are you legally authorized to work in Germany?"]["required"] is True
    assert fields["Resume / CV"]["required"] is True
    # The page's own form / field names, not browser objects or HTML.
    for field in fields.values():
        assert set(field).isdisjoint({"locator", "meta", "html", "selector"})


def test_field_identity_is_stable_and_not_positional(ctx: Ctx, db_session: Session) -> None:
    rows = list(db_session.scalars(select(AutomationField).order_by(AutomationField.position)))
    keys = {row.label: row.field_key for row in rows}
    assert keys[EMAIL] == "field:id:email"
    assert keys["Are you legally authorized to work in Germany?"] == "group:name:authorized"
    assert keys["Which technologies do you work with?"] == "group:name:tech"
    assert not any("nth" in key or key.split(":")[-1].isdigit() for key in keys.values())
    # Rediscovery keeps every identity and creates nothing new.
    before = {r.id: r.field_key for r in rows}
    ok(ctx.act("discover"))
    db_session.expire_all()
    after = {r.id: r.field_key for r in db_session.scalars(select(AutomationField))}
    assert after == before


def test_generated_ids_are_not_trusted_as_identity(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    context = make_ctx(client, auth, db_session, site, path="/ids")
    rows = list(db_session.scalars(select(AutomationField).order_by(AutomationField.position)))
    assert [r.field_key for r in rows] == [
        "field:label:phone-number:tel",
        "field:label:last-name:text",
    ]
    assert all(r.dom_id is None for r in rows)
    assert context.field("Phone number")["dom_id"] is None


def test_a_dynamic_question_appears_after_an_answer_and_is_not_filled_unapproved(
    ctx: Ctx,
) -> None:
    ctx.human.check("input[name=sponsorship][value=yes]")
    refreshed = ok(ctx.act("discover"))
    assert refreshed["counts"]["found"] == 25
    details = ctx.field("Please explain your visa status")
    assert details["input_type"] == "textarea"
    assert details["question"]["source"] == "browser"
    assert details["answer"]["state"] == "NEEDS_INPUT"
    ctx.fill()
    assert ctx.human.value("#visa_details") == ""  # no approved answer: left empty


def test_a_field_that_disappears_stays_on_record(ctx: Ctx) -> None:
    ctx.human.check("input[name=sponsorship][value=yes]")
    ok(ctx.act("discover"))
    ctx.human.check("input[name=sponsorship][value=no]")
    ok(ctx.act("discover"))
    fields = ctx.fields()
    hidden = field_named(fields, "Please explain your visa status")
    assert hidden["present"] is False
    assert ok(ctx.act("discover"))["counts"]["found"] == 24


# --- Questions -----------------------------------------------------------------------------------


def test_discovered_fields_become_neutral_browser_questions(ctx: Ctx) -> None:
    questions = {q["question_text"]: q for q in ctx.questions()}
    email = questions[EMAIL]
    assert email["source"] == "browser"
    assert email["external_key"] == "browser:field:id:email"
    assert email["field_name"] == "email"
    assert email["question_type"] == "FREE_TEXT" and email["required"] is True
    metadata = email["source_metadata"]
    assert metadata["input_type"] == "email" and metadata["normalized_label"] == "email address"
    assert metadata["selector_strategy"] == "id" and "html" not in str(metadata).lower()
    assert questions[SALARY]["question_type"] == "SALARY"
    assert questions[START]["question_type"] == "DATE"
    assert questions["Do you require visa sponsorship?"]["question_type"] == "YES_NO"
    assert questions["Do you require visa sponsorship?"]["options"] == ["Yes", "No"]
    assert questions["Country"]["question_type"] == "SINGLE_CHOICE"
    assert questions["Which technologies do you work with?"]["question_type"] == "MULTI_CHOICE"
    assert questions[WHY]["max_length"] == 300
    # Files and legal confirmations are not questions.
    assert "Resume / CV" not in questions and "I agree to the privacy policy" not in questions


def test_rediscovery_in_a_new_session_reuses_the_questions_and_their_answers(
    ctx: Ctx, client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    ctx.answer(EMAIL, "me@example.com")
    count = len(ctx.questions())
    email_id = ctx.field(EMAIL)["question"]["id"]
    ok(ctx.act("stop", {"close_browser": True}))

    again = started_session(client, auth, ctx.aid)
    assert again["id"] != ctx.sid
    fields = get_fields(client, auth, ctx.aid, again["id"])
    assert field_named(fields, EMAIL)["question"]["id"] == email_id
    assert len(ctx.questions()) == count  # no duplicates
    assert field_named(fields, EMAIL)["answer"]["text"] == "me@example.com"
    keys = db_session.scalars(
        select(ApplicationQuestion.external_key).where(
            ApplicationQuestion.external_key.is_not(None)
        )
    ).all()
    assert len(keys) == len(set(keys))


def test_a_changed_label_keeps_the_question_and_the_answer(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    context = make_ctx(client, auth, db_session, site, path="/relabel?label=Email")
    question = context.field("Email")["question"]
    answer_question(client, auth, context.aid, question["id"], "kept@example.com")
    context.human.goto(site.url("/relabel?label=Email%20address"))
    ok(context.act("discover"))
    renamed = context.field("Email address")
    assert renamed["question"]["id"] == question["id"]  # the same question, not a new one
    assert renamed["question"]["question_text"] == "Email address"
    assert renamed["answer"]["text"] == "kept@example.com"
    assert len(context.questions()) == 1


def test_a_label_change_that_the_answer_cannot_satisfy_is_flagged_not_applied(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    context = make_ctx(client, auth, db_session, site)
    question = context.field(WHY)["question"]
    assert question["question_text"] == WHY
    context.answer(WHY, "x" * 250)  # fits the 300 limit
    # The form now allows less: the answer would no longer fit.
    context.human._page_do(  # noqa: SLF001
        lambda page: page.evaluate("document.getElementById('why').maxLength = 100")
    )
    ok(context.act("discover"))
    refreshed = context.field(WHY)
    assert refreshed["question"]["id"] == question["id"]
    assert refreshed["answer"]["text"] == "x" * 250  # untouched
    assert refreshed["mapping"]["status"] == "NEEDS_REVIEW"
    assert refreshed["mapping"]["reason"] == "FIELD_CHANGED"
    ctx_q = next(q for q in context.questions() if q["id"] == question["id"])
    assert ctx_q["max_length"] == 300  # the question was not changed under the answer


# --- Mapping -------------------------------------------------------------------------------------


def test_an_existing_question_with_the_same_words_is_reused(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    holder: dict[str, Any] = {}

    def before(application: dict[str, Any]) -> None:
        holder["q"] = add_question(client, auth, application["id"], "Phone number")

    context = make_ctx(client, auth, db_session, site, before_start=before)
    phone = context.field(PHONE)
    assert phone["question"]["id"] == holder["q"]["id"]
    assert phone["mapping"]["method"] == "EXACT_TEXT" and phone["mapping"]["confidence"] == "HIGH"
    assert phone["mapping"]["status"] == "AUTO_MAPPED"
    assert phone["question"]["source"] == "manual"  # the user's question is not rewritten
    assert sum(1 for q in context.questions() if q["question_text"] == "Phone number") == 1


def test_an_existing_question_on_the_same_topic_is_reused(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    holder: dict[str, Any] = {}

    def before(application: dict[str, Any]) -> None:
        holder["q"] = add_question(client, auth, application["id"], "Gehaltsvorstellung")

    context = make_ctx(client, auth, db_session, site, before_start=before)
    salary = context.field(SALARY)
    assert salary["question"]["id"] == holder["q"]["id"]
    assert salary["mapping"]["method"] == "TOPIC" and salary["mapping"]["status"] == "AUTO_MAPPED"


def test_an_ambiguous_match_waits_for_the_user_and_is_not_filled(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    holder: dict[str, Any] = {}

    def before(application: dict[str, Any]) -> None:
        holder["a"] = add_question(client, auth, application["id"], "Phone number")
        holder["b"] = add_question(client, auth, application["id"], "phone number?")
        answer_question(client, auth, application["id"], holder["a"]["id"], "+49 170 1234567")

    context = make_ctx(client, auth, db_session, site, before_start=before)
    phone = context.field(PHONE)
    assert phone["mapping"]["status"] == "NEEDS_REVIEW"
    assert phone["mapping"]["reason"] == "AMBIGUOUS_MATCH"
    assert phone["display_status"] == "needs_review" and phone["question"] is None
    context.fill()
    assert context.human.value("#phone") == ""  # an unconfirmed mapping is never filled

    # The user decides.
    confirmed = ok(
        post(
            client,
            auth,
            context.aid,
            context.sid,
            f"fields/{phone['id']}/mapping",
            {"action": "change", "question_id": holder["a"]["id"]},
        )
    )
    assert confirmed["mapping"]["status"] == "USER_CONFIRMED"
    assert confirmed["mapping"]["method"] == "USER"
    context.fill()
    assert context.human.value("#phone") == "+49 170 1234567"


def test_the_user_can_confirm_leave_unmapped_or_add_a_question_and_it_is_remembered(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    def before(application: dict[str, Any]) -> None:
        add_question(client, auth, application["id"], "What number can we call you on?")
        add_question(client, auth, application["id"], "What is the best number to call you on?")

    context = make_ctx(client, auth, db_session, site, before_start=before)
    base_url = f"fields/{context.field(PHONE)['id']}/mapping"
    # Leave unmapped: never filled, and the decision outlives the session.
    unmapped = ok(post(client, auth, context.aid, context.sid, base_url, {"action": "unmap"}))
    assert unmapped["mapping"]["status"] == "UNMAPPED" and unmapped["question"] is None
    assert unmapped["display_status"] == "unmapped"
    ok(context.act("stop", {"close_browser": True}))
    later = started_session(client, auth, context.aid)
    again = field_named(get_fields(client, auth, context.aid, later["id"]), PHONE)
    assert again["mapping"]["status"] == "UNMAPPED" and again["mapping"]["method"] == "USER"

    # Add a question for the field.
    created = ok(
        post(
            client,
            auth,
            context.aid,
            later["id"],
            f"fields/{again['id']}/mapping",
            {"action": "create"},
        )
    )
    assert created["mapping"]["status"] == "USER_CONFIRMED"
    assert created["question"]["source"] == "browser"
    # `confirm` needs a proposal; `change` needs a question.
    error(
        post(
            client,
            auth,
            context.aid,
            later["id"],
            f"fields/{again['id']}/mapping",
            {"action": "change"},
        ),
        422,
        "VALIDATION_ERROR",
    )


def test_a_field_cannot_be_mapped_to_another_users_question(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    ctx: Ctx,
) -> None:
    foreign = make_application(client, other_auth, db_session, site, email="mallory@example.com")
    question = add_question(client, other_auth, foreign["id"], "A secret question")
    error(
        post(
            client,
            auth,
            ctx.aid,
            ctx.sid,
            f"fields/{ctx.field(PHONE)['id']}/mapping",
            {"action": "change", "question_id": question["id"]},
        ),
        404,
        "NOT_FOUND",
    )


def test_a_user_decision_is_not_overridden_by_rediscovery(ctx: Ctx) -> None:
    phone = ctx.field(PHONE)
    ok(
        post(
            ctx.client,
            ctx.auth,
            ctx.aid,
            ctx.sid,
            f"fields/{phone['id']}/mapping",
            {"action": "unmap"},
        )
    )
    ok(ctx.act("discover"))
    assert ctx.field(PHONE)["mapping"]["status"] == "UNMAPPED"


def test_moving_a_field_to_another_question_removes_the_untouched_question_made_for_it(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    holder: dict[str, Any] = {}

    def before(application: dict[str, Any]) -> None:
        holder["q"] = add_question(
            client, auth, application["id"], "How can we reach you by phone?"
        )

    context = make_ctx(client, auth, db_session, site, before_start=before)
    phone = context.field(PHONE)
    made_for_field = phone["question"]["id"]
    assert made_for_field != holder["q"]["id"]
    ok(
        post(
            client,
            auth,
            context.aid,
            context.sid,
            f"fields/{phone['id']}/mapping",
            {"action": "change", "question_id": holder["q"]["id"]},
        )
    )
    assert made_for_field not in {q["id"] for q in context.questions()}


# --- AI mapping ----------------------------------------------------------------------------------


def _ai_ctx(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    site: FakeApplicationSite,
    fake_ai: FakeAutomationAI,
    responder: Any,
    candidate_text: str = "Describe your motivation for joining our company",
) -> tuple[Ctx, dict[str, Any]]:
    """`responder` is a function of the field context, or of (context, holder) where
    `holder["q"]` is the manual candidate question."""
    holder: dict[str, Any] = {}
    fake_ai.mapping = lambda context: (
        responder(context, holder) if responder.__code__.co_argcount == 2 else responder(context)
    )

    def before(application: dict[str, Any]) -> None:
        holder["q"] = add_question(client, auth, application["id"], candidate_text)
        answer_question(
            client, auth, application["id"], holder["q"]["id"], "Because the work matters."
        )

    return make_ctx(client, auth, db, site, before_start=before), holder


def test_a_high_confidence_ai_mapping_is_applied_with_its_provenance(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    fake_ai: FakeAutomationAI,
) -> None:
    def respond(context: dict[str, Any], holder: dict[str, Any]) -> dict[str, Any]:
        if context["field"]["label"] != WHY:
            return {"application_question_id": None, "confidence": "LOW", "reason": "no match"}
        return {
            "application_question_id": holder["q"]["id"],
            "confidence": "HIGH",
            "reason": "Both ask about motivation.",
        }

    context, holder = _ai_ctx(client, auth, db_session, site, fake_ai, respond)
    why = context.field(WHY)
    assert why["question"]["id"] == holder["q"]["id"]
    assert why["mapping"] == {
        "status": "AUTO_MAPPED",
        "method": "AI",
        "confidence": "HIGH",
        "reason": "AI_MATCH",
    }
    row = db_session.scalar(select(AutomationField).where(AutomationField.label == WHY))
    assert row is not None and row.mapping_provenance is not None
    assert row.mapping_provenance["prompt_version"] == "field_mapping_v1"
    assert len(row.mapping_provenance["prompt_hash"]) == 64
    assert row.mapping_provenance["ai_provider"] == "fake"
    context.fill()
    assert context.human.value("#why") == "Because the work matters."


def test_only_the_field_and_the_candidate_questions_are_shown_to_the_ai(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    fake_ai: FakeAutomationAI,
) -> None:
    _ai_ctx(
        client,
        auth,
        db_session,
        site,
        fake_ai,
        lambda c: {"application_question_id": None, "confidence": "LOW", "reason": "none"},
    )
    assert fake_ai.mapping_contexts
    seen = str(fake_ai.mapping_contexts) + " ".join(c["prompt"] for c in fake_ai.calls)
    assert "Because the work matters." not in seen  # the user's answer is not sent
    assert "78000" not in seen and "jane@example.com" not in seen
    for context in fake_ai.mapping_contexts:
        assert set(context) == {"field", "candidates"}
        assert set(context["field"]) == {"label", "input_type", "required", "options", "max_length"}
        assert all(set(c) == {"id", "question", "type", "options"} for c in context["candidates"])


@pytest.mark.parametrize("confidence", ["MEDIUM", "LOW"])
def test_a_less_than_high_ai_mapping_waits_for_the_user_and_is_not_filled(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    fake_ai: FakeAutomationAI,
    confidence: str,
) -> None:
    def respond(context: dict[str, Any], holder: dict[str, Any]) -> dict[str, Any]:
        if context["field"]["label"] == WHY:
            return {
                "application_question_id": holder["q"]["id"],
                "confidence": confidence,
                "reason": "maybe",
            }
        return {"application_question_id": None, "confidence": "LOW", "reason": "none"}

    context, holder = _ai_ctx(client, auth, db_session, site, fake_ai, respond)
    why = context.field(WHY)
    assert why["mapping"]["status"] == "NEEDS_REVIEW" and why["mapping"]["method"] == "AI"
    assert why["proposed_question"]["id"] == holder["q"]["id"]
    assert why["question"] is None
    context.fill()
    assert context.human.value("#why") == ""
    # Confirming the proposal makes it the user's decision, and it fills.
    ok(
        post(
            client,
            auth,
            context.aid,
            context.sid,
            f"fields/{why['id']}/mapping",
            {"action": "confirm"},
        )
    )
    context.fill()
    assert context.human.value("#why") == "Because the work matters."


def test_the_ai_cannot_pick_a_question_that_does_not_exist(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    fake_ai: FakeAutomationAI,
) -> None:
    context, holder = _ai_ctx(
        client,
        auth,
        db_session,
        site,
        fake_ai,
        lambda c: {
            "application_question_id": str(uuid.uuid4()),
            "confidence": "HIGH",
            "reason": "invented",
        },
    )
    why = context.field(WHY)
    assert why["question"]["id"] != holder["q"]["id"]  # the invented id was ignored
    assert why["mapping"]["method"] == "CREATED"
    assert why["question"]["source"] == "browser"


def test_the_ai_is_not_asked_when_a_deterministic_rule_decides_and_its_failure_is_harmless(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    fake_ai: FakeAutomationAI,
) -> None:
    from app.ai.provider import AIProviderUnavailableError

    fake_ai.mapping_error = AIProviderUnavailableError("down")
    holder: dict[str, Any] = {}

    def before(application: dict[str, Any]) -> None:
        holder["phone"] = add_question(client, auth, application["id"], "Phone number")
        add_question(client, auth, application["id"], "Describe your motivation to join")

    context = make_ctx(client, auth, db_session, site, before_start=before)
    assert context.field(PHONE)["question"]["id"] == holder["phone"]["id"]  # no AI needed
    assert context.field(WHY)["mapping"]["method"] in ("CREATED", "SIMILAR")  # AI failed: no crash
    assert not any(
        c["field"]["label"] == PHONE for c in fake_ai.mapping_contexts
    ), "the AI was asked about a field a rule had already mapped"


# --- What may be filled --------------------------------------------------------------------------


def test_deterministic_application_data_is_filled_and_verified(ctx: Ctx) -> None:
    session = ctx.fill()
    assert ctx.human.value("#salary") == "78000"
    assert ctx.human.value("#start_date") == "2027-01-01"
    assert ctx.human.value("#language") == "en"
    assert ctx.human.is_checked("input[name=authorized][value=yes]")
    assert ctx.human.is_checked("input[name=sponsorship][value=no]")
    # The parts of the name come from the account the user registered with.
    assert ctx.human.value("#first_name") == "Jane" and ctx.human.value("#last_name") == "Doe"
    assert ctx.field("First name")["answer"]["source"] == "DETERMINISTIC"
    assert ctx.human.value("#notice") != ""  # "3 months"
    salary = ctx.field(SALARY)
    assert salary["display_status"] == "filled" and salary["fill"]["status"] == "FILLED"
    assert salary["answer"] == {
        "state": "APPROVED",
        "source": "DETERMINISTIC",
        "text": "78000",
        "options": None,
        "reason": None,
        "message": None,
    }
    assert session["counts"]["filled"] >= 7
    assert ctx.site.submit_clicks == 0


def test_the_users_own_answer_beats_deterministic_data(ctx: Ctx) -> None:
    ctx.answer(SALARY, "85000")
    ctx.fill()
    assert ctx.human.value("#salary") == "85000"
    assert ctx.field(SALARY)["answer"]["source"] == "USER"


def test_a_missing_answer_is_surfaced_and_never_invented_even_when_required(ctx: Ctx) -> None:
    email = ctx.field(EMAIL)
    assert email["required"] is True and email["required_missing"] is True
    assert email["display_status"] == "needs_input"
    assert email["display_message"] == "Required — no approved answer."
    optional = ctx.field("Current employer")
    assert optional["display_status"] == "needs_input" and optional["required_missing"] is False
    ctx.fill()
    assert ctx.human.value("#email") == "" and ctx.human.value("#employer") == ""
    assert email["fill"]["status"] == "NOT_FILLED"


def test_an_unreviewed_ai_suggestion_is_shown_but_never_filled(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    fake_ai: FakeAutomationAI,
) -> None:
    context = make_ctx(client, auth, db_session, site, resume=True)
    why = context.field(WHY)
    generated = ok(
        client.post(
            f"{API}/applications/{context.aid}/questions/{why['question']['id']}/generate",
            headers=auth,
        )
    )
    assert generated["answer"]["status"] == "SUGGESTED"
    suggestion = generated["answer"]["ai_suggestion"]
    assert suggestion

    reviewed = context.field(WHY)
    assert reviewed["answer"]["state"] == "SUGGESTION_AVAILABLE"
    assert reviewed["answer"]["text"] == suggestion
    assert reviewed["display_status"] == "needs_review"
    ctx_session = context.fill()
    assert context.human.value("#why") == ""  # NOT typed into the real form
    assert ctx_session["counts"]["needs_review"] >= 1

    # Once the user accepts it, it is theirs and it is filled.
    ok(
        client.post(
            f"{API}/applications/{context.aid}/questions/{why['question']['id']}/accept",
            headers=auth,
        )
    )
    assert context.field(WHY)["answer"]["source"] == "ACCEPTED_AI"
    context.fill()
    assert context.human.value("#why") == suggestion


def test_a_rejected_suggestion_is_not_filled(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    fake_ai: FakeAutomationAI,
) -> None:
    context = make_ctx(client, auth, db_session, site, resume=True)
    salary = context.field(SALARY)["question"]
    # A deterministic suggestion the user turns down is not applied behind their back.
    ok(
        client.post(
            f"{API}/applications/{context.aid}/questions/{salary['id']}/generate", headers=auth
        )
    )
    ok(
        client.post(
            f"{API}/applications/{context.aid}/questions/{salary['id']}/reject", headers=auth
        )
    )
    assert context.field(SALARY)["answer"]["state"] == "NEEDS_INPUT"
    context.fill()
    assert context.human.value("#salary") == ""


def test_suggestions_can_be_requested_for_the_mapped_questions_but_are_never_accepted(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
    fake_ai: FakeAutomationAI,
) -> None:
    context = make_ctx(client, auth, db_session, site, resume=True)
    result = ok(post(client, auth, context.aid, context.sid, "suggest"))
    assert (
        result["requested"] >= 1 and result["suggested"] + result["failed"] == result["requested"]
    )
    context.fill()
    # Whatever was suggested, nothing unreviewed reached the page.
    assert context.human.value("#why") == ""
    statuses = {q["answer"]["status"] for q in context.questions()}
    assert "ACCEPTED" not in statuses and "USER_EDITED" not in statuses


# --- Control types -------------------------------------------------------------------------------


def test_every_supported_control_is_filled_and_the_page_sees_real_input_events(ctx: Ctx) -> None:
    ctx.answer("First name", "Erika")
    ctx.answer(EMAIL, "erika@example.com")
    ctx.answer(PHONE, "+49 170 1234567")
    ctx.answer("LinkedIn profile", "https://www.linkedin.com/in/erika")
    ctx.answer("Years of experience", "7")
    ctx.answer("Country", options=["Austria"])
    ctx.answer("Which technologies do you work with?", options=["Python", "Go"])
    ctx.answer("Open to relocation", "Yes")
    ctx.answer(WHY, "Because the work is meaningful.\nAnd the team is great.")
    ctx.answer(START, "2027-03-01")
    ctx.fill()

    human = ctx.human
    assert human.value("#first_name") == "Erika"
    assert human.value("#email") == "erika@example.com"
    assert human.value("#phone") == "+49 170 1234567"
    assert human.value("#linkedin") == "https://www.linkedin.com/in/erika"
    assert human.value("#years") == "7"
    assert human.value("#country") == "AT"
    assert human.is_checked("input[name=tech][value=python]") and human.is_checked(
        "input[name=tech][value=go]"
    )
    assert not human.is_checked("input[name=tech][value=java]")
    assert human.is_checked("#relocate")
    assert human.value("#why") == "Because the work is meaningful.\nAnd the team is great."
    assert human.value("#start_date") == "2027-03-01"
    events = human.events()
    for key in (
        "first_name:input",
        "email:input",
        "country:change",
        "tech:change",
        "relocate:change",
    ):
        assert events.get(key, 0) >= 1, key  # framework-style listeners saw the changes
    for label in (EMAIL, PHONE, "Country", "Open to relocation", WHY):
        assert ctx.field(label)["display_status"] == "filled", label


def test_a_no_unchecks_a_checkbox_and_a_group_is_set_exactly(ctx: Ctx) -> None:
    ctx.answer("Open to relocation", "No")
    ctx.answer("Which technologies do you work with?", options=["Java"])
    ctx.fill()
    assert not ctx.human.is_checked("#relocate")
    assert ctx.human.is_checked("input[name=tech][value=java]")
    assert ctx.field("Open to relocation")["display_status"] == "filled"


def test_a_value_the_browser_changes_is_a_failed_fill_not_a_silent_success(ctx: Ctx) -> None:
    ctx.answer("Reference code", "ab-12")
    session = ctx.fill()
    reference = ctx.field("Reference code")
    assert ctx.human.value("#reference") == "AB-12"  # the page rewrote it
    assert reference["display_status"] == "failed"
    assert (
        reference["fill"]["status"] == "FILL_FAILED"
        and reference["fill"]["error_code"] == "VALUE_MISMATCH"
    )
    assert (
        reference["fill"]["error_message"]
        == "The browser shows a different value than the approved answer."
    )
    assert session["counts"]["failed"] == 1
    # It is not retried over the page's own change either: the assistant does not loop.
    again = ctx.fill()
    assert again["counts"]["failed"] == 1


def test_an_answer_that_does_not_fit_the_form_is_reported_and_never_shortened(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    holder: dict[str, Any] = {}

    def before(application: dict[str, Any]) -> None:
        for text, answer in (("Postal code", "1234567"), ("Country", None)):
            q = add_question(
                client,
                auth,
                application["id"],
                text,
                **(
                    {"question_type": "SINGLE_CHOICE", "options": ["Narnia", "Germany"]}
                    if answer is None
                    else {}
                ),
            )
            holder[text] = q
        answer_question(client, auth, application["id"], holder["Postal code"]["id"], "1234567")
        answer_question(
            client, auth, application["id"], holder["Country"]["id"], options=["Narnia"]
        )

    context = make_ctx(client, auth, db_session, site, before_start=before)
    context.fill()
    postal = context.field("Postal code")
    assert (
        postal["answer"]["state"] == "UNFILLABLE"
        and postal["answer"]["reason"] == "ANSWER_TOO_LONG"
    )
    assert postal["display_status"] == "needs_review"
    assert context.human.value("#postal") == ""  # nothing truncated into the form
    country = context.field("Country")
    assert country["answer"]["reason"] == "OPTION_NOT_FOUND"
    assert context.human.value("#country") == ""


def test_filling_is_idempotent(ctx: Ctx, db_session: Session) -> None:
    ctx.fill()
    filled = db_session.scalar(
        select(func.count())
        .select_from(AutomationEvent)
        .where(AutomationEvent.event_type == "FIELD_FILLED")
    )
    assert filled and filled > 0
    ctx.fill()
    ctx.fill()
    again = db_session.scalar(
        select(func.count())
        .select_from(AutomationEvent)
        .where(AutomationEvent.event_type == "FIELD_FILLED")
    )
    assert again == filled  # nothing was written a second time
    assert ctx.field(SALARY)["display_status"] == "filled"


def test_a_changed_answer_replaces_what_the_assistant_itself_wrote(ctx: Ctx) -> None:
    ctx.fill()
    assert ctx.human.value("#salary") == "78000"
    ctx.answer(SALARY, "81000")
    ctx.fill()
    assert ctx.human.value("#salary") == "81000"


def test_a_field_that_is_already_correct_is_marked_filled_without_a_write(
    ctx: Ctx, db_session: Session
) -> None:
    ctx.human.type_into("#salary", "78000")  # the user typed exactly the approved value
    ctx.fill()
    assert ctx.field(SALARY)["display_status"] == "filled"
    wrote = db_session.scalars(
        select(AutomationEvent).where(
            AutomationEvent.event_type == "FIELD_FILLED",
            AutomationEvent.field_id == uuid.UUID(ctx.field(SALARY)["id"]),
        )
    ).all()
    assert wrote == []


# --- Human control over what is written ----------------------------------------------------------


def test_what_the_user_changed_is_never_overwritten(ctx: Ctx) -> None:
    ctx.answer(EMAIL, "approved@example.com")
    ctx.fill()
    assert ctx.human.value("#email") == "approved@example.com"

    ctx.human.type_into("#email", "mine@example.org")  # the user edits the field
    again = ctx.fill()
    assert ctx.human.value("#email") == "mine@example.org"
    email = ctx.field(EMAIL)
    assert email["display_status"] == "user_modified" and email["fill"]["status"] == "USER_MODIFIED"
    assert again["counts"]["user_modified"] == 1

    # A field the user typed into before the assistant ever wrote it is theirs as well.
    ctx.human.type_into("#phone", "0151 000")
    ctx.answer(PHONE, "+49 170 1234567")
    ctx.fill()
    assert ctx.human.value("#phone") == "0151 000"
    assert ctx.field(PHONE)["display_status"] == "user_modified"


def test_a_field_the_user_cleared_is_theirs_too(ctx: Ctx) -> None:
    ctx.answer(EMAIL, "approved@example.com")
    ctx.fill()
    ctx.human.type_into("#email", "")
    ctx.fill()
    assert ctx.human.value("#email") == ""
    assert ctx.field(EMAIL)["display_status"] == "user_modified"


def test_a_field_that_left_the_page_is_not_written_to(ctx: Ctx) -> None:
    ctx.answer(PHONE, "+49 170 1234567")
    ctx.human._page_do(  # noqa: SLF001
        lambda page: page.evaluate("document.getElementById('phone').remove()")
    )
    ctx.fill()
    phone = ctx.field(PHONE)
    assert phone["present"] is False and phone["fill"]["status"] == "NOT_FILLED"
    assert ctx.session_now()["counts"]["found"] == 23


def test_after_a_page_reload_nothing_is_overwritten_silently(ctx: Ctx) -> None:
    ctx.answer(EMAIL, "approved@example.com")
    ctx.fill()
    assert ctx.human.value("#email") == "approved@example.com"
    ctx.human._page_do(lambda page: page.reload())  # noqa: SLF001
    assert ctx.human.value("#email") == ""  # the page reset itself
    ctx.fill()
    # The assistant cannot tell a reload from the user clearing the field: it asks.
    assert ctx.human.value("#email") == ""
    assert ctx.field(EMAIL)["display_status"] == "user_modified"
    field_id = ctx.field(EMAIL)["id"]
    ctx.fill(field_ids=[field_id], overwrite_field_ids=[field_id])
    assert ctx.human.value("#email") == "approved@example.com"


def test_the_user_can_ask_for_one_field_to_be_written_again(ctx: Ctx) -> None:
    ctx.answer(EMAIL, "approved@example.com")
    ctx.fill()
    ctx.human.type_into("#email", "mine@example.org")
    ctx.fill()
    assert ctx.human.value("#email") == "mine@example.org"
    field_id = ctx.field(EMAIL)["id"]
    ctx.human.type_into("#phone", "0151 000")
    ctx.answer(PHONE, "+49 170 1234567")
    ctx.fill(field_ids=[field_id], overwrite_field_ids=[field_id])
    assert ctx.human.value("#email") == "approved@example.com"  # asked for explicitly
    assert ctx.human.value("#phone") == "0151 000"  # not named: untouched


def test_overwrite_must_name_fields_in_the_request(ctx: Ctx) -> None:
    field_id = ctx.field(EMAIL)["id"]
    response = ctx.act("fill", {"field_ids": [], "overwrite_field_ids": [field_id]})
    assert response.status_code == 422


def test_resuming_keeps_the_users_edits_and_fills_only_what_is_empty(ctx: Ctx) -> None:
    ctx.answer(EMAIL, "approved@example.com")
    ctx.answer(PHONE, "+49 170 1234567")
    ctx.fill()
    ok(ctx.act("take-control"))
    ctx.human.type_into("#email", "changed@example.org")
    ctx.human.type_into("#phone", "")  # cleared
    ctx.human.type_into("#employer", "Acme")  # typed by the user, no approved answer anywhere
    ctx.answer("Years of experience", "9")  # a new approved answer meanwhile
    session = ok(ctx.act("resume", {"fill": True}))
    assert session["status"] in ("RUNNING", "COMPLETED")
    assert ctx.human.value("#email") == "changed@example.org"
    assert ctx.human.value("#phone") == ""
    assert ctx.human.value("#employer") == "Acme"
    assert ctx.human.value("#years") == "9"  # empty + approved: filled
    assert ctx.field(EMAIL)["display_status"] == "user_modified"
    assert ctx.field(PHONE)["display_status"] == "user_modified"


def test_resuming_without_fill_only_looks(ctx: Ctx) -> None:
    ctx.answer(EMAIL, "approved@example.com")
    ok(ctx.act("pause"))
    ok(ctx.act("resume"))
    assert ctx.human.value("#email") == ""  # resuming does not refill anything by itself


def test_no_field_changes_while_the_user_is_in_control_or_paused(ctx: Ctx) -> None:
    ctx.answer(EMAIL, "approved@example.com")
    ok(ctx.act("take-control"))
    error(ctx.act("fill"), 409, "INVALID_SESSION_STATE")
    ctx.human.type_into("#last_name", "Typed by me")
    assert ctx.human.value("#email") == ""
    ok(ctx.act("resume"))
    ok(ctx.act("pause"))
    error(ctx.act("fill"), 409, "INVALID_SESSION_STATE")
    assert ctx.human.value("#email") == "" and ctx.human.value("#last_name") == "Typed by me"


# --- Legal confirmations -------------------------------------------------------------------------


def test_legal_confirmations_are_never_ticked_even_when_asked(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    def before(application: dict[str, Any]) -> None:
        for text in (
            "I agree to the privacy policy",
            "I certify that the information above is true and complete",
        ):
            q = add_question(client, auth, application["id"], text, question_type="YES_NO")
            answer_question(client, auth, application["id"], q["id"], "Yes")

    context = make_ctx(client, auth, db_session, site, before_start=before)
    for label in (
        "I agree to the privacy policy",
        "I certify that the information above is true and complete",
    ):
        field = context.field(label)
        assert field["display_status"] == "action_required"
        assert field["user_action_reason"] == "LEGAL_CONSENT"
        assert "never ticks" in field["user_action_message"]
        assert field["question"] is None and field["mapping"]["status"] == "UNMAPPED"
    session = context.fill()
    assert not context.human.is_checked("#privacy") and not context.human.is_checked("#truth")
    assert (
        context.field("I agree to the privacy policy")["fill"]["status"] == "USER_ACTION_REQUIRED"
    )
    assert session["counts"]["action_required"] >= 2
    # The user ticks it themselves; the assistant leaves it alone.
    context.human.check("#privacy")
    context.fill()
    assert context.human.is_checked("#privacy")


# --- The submit button ---------------------------------------------------------------------------


def test_the_submit_button_is_recognized_but_never_pressed(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from playwright.async_api import ElementHandle, Frame, Keyboard, Locator, Mouse, Page

    attempts: list[str] = []

    def tripwire(name: str) -> Any:
        async def refuse(self: Any, *args: Any, **kwargs: Any) -> None:
            attempts.append(name)
            raise AssertionError(f"the assistant tried to {name}")

        return refuse

    for cls in (Page, Frame, Locator, ElementHandle):
        for method in ("click", "dblclick", "tap", "dispatch_event"):
            if hasattr(cls, method):
                monkeypatch.setattr(cls, method, tripwire(f"{cls.__name__}.{method}"))
    monkeypatch.setattr(Keyboard, "press", tripwire("keyboard.press"))
    monkeypatch.setattr(Mouse, "click", tripwire("mouse.click"))

    ctx.answer(EMAIL, "me@example.com")
    ctx.answer("Open to relocation", "Yes")
    ctx.fill()
    ok(ctx.act("take-control"))
    ok(ctx.act("resume", {"fill": True}))
    ok(ctx.act("discover"))
    assert attempts == []
    assert ctx.site.submit_clicks == 0
    session = ctx.session_now()
    assert session["submit_detected"] is True
    assert session["submit_notice"] == (
        "The application appears ready for submission. V0.10 does not submit applications. "
        "Please submit manually."
    )
    assert session["status"] in ("RUNNING", "COMPLETED")


def test_a_page_without_a_submit_control_stays_running_after_a_fill(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    context = make_ctx(client, auth, db_session, site, path="/relabel?label=Email%20address")
    context.answer(EMAIL, "me@example.com")
    done = context.fill()
    assert done["status"] == "RUNNING" and done["submit_detected"] is False
    assert done["submit_notice"] is None


def test_completed_means_nothing_is_left_for_the_assistant_and_it_is_not_final(ctx: Ctx) -> None:
    done = ctx.fill()
    assert done["submit_detected"] is True
    assert done["status"] == "COMPLETED" and done["controls"]["can_fill"] is True
    # New approved answers bring it back to work.
    ctx.answer("Years of experience", "5")
    again = ok(ctx.act("discover"))
    assert again["status"] == "RUNNING"
    filled = ctx.fill()
    assert ctx.human.value("#years") == "5" and filled["status"] == "COMPLETED"


def test_the_filler_cannot_click_by_construction() -> None:
    from app.services.automation import safety
    from app.services.automation.browser import base

    assert safety.SUBMIT_ACTIONS_ALLOWED is False
    for cls in (base.BrowserPage, base.BrowserSession, base.BrowserDriver):
        assert not [n for n in dir(cls) if "click" in n or "submit" in n or "press" in n]


def test_field_endpoints_reject_unknown_keys_and_files_only_take_files(ctx: Ctx) -> None:
    phone = ctx.field(PHONE)
    response = post(
        ctx.client,
        ctx.auth,
        ctx.aid,
        ctx.sid,
        f"fields/{phone['id']}/mapping",
        {"action": "unmap", "user_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    error(
        ctx.client.get(
            f"{session_url(ctx.aid, ctx.sid)}/fields/{phone['id']}/documents", headers=ctx.auth
        ),
        409,
        "NOT_A_FILE_FIELD",
    )
    assert base(ctx.aid).endswith("/automation")
