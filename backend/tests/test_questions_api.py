"""V0.9 application questions through the real API: CRUD, ownership, deterministic
and AI suggestions, validation, and -- above all -- Human Control: a suggestion
never overwrites the user's answer. The AI is a deterministic fake."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.provider import (
    AIInvalidResponseError,
    AINotConfiguredError,
    AIProviderUnavailableError,
    AIRateLimitError,
)
from app.models import (
    ApplicationContextSnapshot,
    ApplicationQuestion,
    ApplicationQuestionAnswer,
    ApplicationQuestionGeneration,
    GenerationMethod,
    GenerationOutcome,
)
from app.models.mixins import ImmutableRecordError
from tests.application_helpers import API, make_job, started, user_by_email
from tests.question_helpers import (
    FakeQuestionAI,
    act,
    add_question,
    error,
    fact_with,
    make_ready,
    needs_input,
    ok,
    option_answer,
    questions_url,
    text_answer,
)

WHY = "Why do you want to work for our company?"


@pytest.fixture
def fake_ai() -> FakeQuestionAI:
    return FakeQuestionAI()


@pytest.fixture
def auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {register_and_login()['access_token']}"}


@pytest.fixture
def other_auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = register_and_login(email="mallory@example.com")["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ready(client: TestClient, auth: dict[str, str], db_session: Session) -> dict[str, Any]:
    return make_ready(client, auth, db_session)


def gen(
    client: TestClient, h: dict[str, str], app: dict[str, Any], q: dict[str, Any], **body: Any
) -> Any:
    return act(client, h, app["id"], q["id"], "generate", body or None)


def regen(
    client: TestClient, h: dict[str, str], app: dict[str, Any], q: dict[str, Any], **body: Any
) -> Any:
    return act(client, h, app["id"], q["id"], "regenerate", body or None)


def get_q(
    client: TestClient, h: dict[str, str], app: dict[str, Any], q: dict[str, Any]
) -> dict[str, Any]:
    return ok(client.get(f"{questions_url(app['id'])}/{q['id']}", headers=h))


def manual(
    client: TestClient, h: dict[str, str], app: dict[str, Any], q: dict[str, Any], **body: Any
) -> Any:
    return act(client, h, app["id"], q["id"], "manual", body)


def rows(db: Session, model: type) -> list[Any]:
    db.expire_all()
    return list(db.scalars(select(model)).all())


# --- CRUD -------------------------------------------------------------------------------------


def test_create_list_get_update_delete(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    created = add_question(
        client,
        auth,
        ready["id"],
        WHY,
        required=True,
        max_length=500,
        field_name="why",
        source="manual",
    )
    assert created["question_text"] == WHY
    assert created["normalized_question_text"] == "why do you want to work for our company"
    assert len(created["question_hash"]) == 64
    assert created["question_type"] == "FREE_TEXT" and created["required"] is True
    assert created["max_length"] == 500 and created["options"] is None
    assert created["answer"]["status"] == "NOT_STARTED"
    assert created["answer"]["final_answer"] is None and created["answer"]["ai_suggestion"] is None

    second = add_question(client, auth, ready["id"], "Anything else?")
    assert second["sort_order"] == created["sort_order"] + 1

    listing = ok(client.get(questions_url(ready["id"]), headers=auth))
    assert [item["id"] for item in listing["items"]] == [created["id"], second["id"]]
    assert listing["ai"]["can_generate"] is True

    fetched = get_q(client, auth, ready, created)
    assert fetched["id"] == created["id"]

    updated = ok(
        client.patch(
            f"{questions_url(ready['id'])}/{created['id']}",
            headers=auth,
            json={"question_text": "Why this company?", "max_length": 300, "required": False},
        )
    )
    assert updated["question_text"] == "Why this company?"
    assert updated["normalized_question_text"] == "why this company"
    assert updated["question_hash"] != created["question_hash"]
    assert updated["max_length"] == 300 and updated["required"] is False

    cleared = ok(
        client.patch(
            f"{questions_url(ready['id'])}/{created['id']}", headers=auth, json={"max_length": None}
        )
    )
    assert cleared["max_length"] is None

    reordered = ok(
        client.patch(
            f"{questions_url(ready['id'])}/{second['id']}", headers=auth, json={"sort_order": 0}
        )
    )
    assert reordered["sort_order"] == 0

    gone = client.delete(f"{questions_url(ready['id'])}/{created['id']}", headers=auth)
    assert gone.status_code == 204
    assert (
        client.get(f"{questions_url(ready['id'])}/{created['id']}", headers=auth).status_code == 404
    )
    assert len(ok(client.get(questions_url(ready["id"]), headers=auth))["items"]) == 1


def test_choice_questions_and_yes_no_labels(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    choice = add_question(
        client,
        auth,
        ready["id"],
        "Seniority?",
        question_type="SINGLE_CHOICE",
        options=["Junior", "  Mid-level ", "Senior"],
    )
    assert choice["options"] == ["Junior", "Mid-level", "Senior"]
    labelled = add_question(
        client, auth, ready["id"], "Remote?", question_type="YES_NO", options=["Ja", "Nein"]
    )
    assert labelled["options"] == ["Ja", "Nein"]


@pytest.mark.parametrize(
    "body",
    [
        {"question_text": ""},
        {"question_text": "   "},
        {"question_text": "x" * 2001},
        {"question_text": "Q", "question_type": "SINGLE_CHOICE"},
        {"question_text": "Q", "question_type": "MULTI_CHOICE", "options": []},
        {"question_text": "Q", "question_type": "FREE_TEXT", "options": ["a", "b"]},
        {"question_text": "Q", "question_type": "YES_NO", "options": ["only one"]},
        {"question_text": "Q", "question_type": "SINGLE_CHOICE", "options": ["A", "a"]},
        {"question_text": "Q", "question_type": "SINGLE_CHOICE", "options": ["A", ""]},
        {"question_text": "Q", "max_length": 0},
        {"question_text": "Q", "question_type": "RICH_TEXT"},
        {"question_text": "Q", "source": "Not A Label!"},
        {"question_text": "Q", "source_metadata": {"x": "y" * 5000}},
    ],
)
def test_invalid_questions_are_refused(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], body: dict[str, Any]
) -> None:
    response = client.post(questions_url(ready["id"]), headers=auth, json=body)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "application_id",
        "id",
        "normalized_question_text",
        "question_hash",
        "status",
        "answer",
        "final_answer",
        "ai_suggestion",
        "ai_provenance",
        "validation",
        "created_at",
    ],
)
def test_a_client_cannot_set_server_owned_fields(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], field: str
) -> None:
    body = {"question_text": WHY, field: "x"}
    assert client.post(questions_url(ready["id"]), headers=auth, json=body).status_code == 422
    q = add_question(client, auth, ready["id"])
    url = f"{questions_url(ready['id'])}/{q['id']}"
    assert client.patch(url, headers=auth, json={field: "x"}).status_code == 422


def test_external_key_is_unique_per_application_and_source_neutral(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    first = add_question(
        client,
        auth,
        ready["id"],
        "Cover letter?",
        external_key="field-17",
        source="browser",
        field_name="question_17",
        source_metadata={"input_type": "textarea", "option_ids": {}},
    )
    assert first["external_key"] == "field-17" and first["source"] == "browser"
    assert first["source_metadata"] == {"input_type": "textarea", "option_ids": {}}
    clash = client.post(
        questions_url(ready["id"]),
        headers=auth,
        json={"question_text": "Other", "external_key": "field-17"},
    )
    error(clash, 409, "QUESTION_EXISTS")
    # a different application may use the same key
    job = make_job(db_session, user_by_email(db_session))
    second_app = started(client, auth, job, {})
    again = add_question(client, auth, second_app["id"], "Cover letter?", external_key="field-17")
    assert again["external_key"] == "field-17"


# --- Ownership --------------------------------------------------------------------------------


def test_another_users_application_and_questions_are_a_404(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = add_question(client, auth, ready["id"], "Email")
    ok(gen(client, auth, ready, q))
    base = questions_url(ready["id"])
    one = f"{base}/{q['id']}"
    for response in (
        client.get(base, headers=other_auth),
        client.post(base, headers=other_auth, json={"question_text": "Hi"}),
        client.get(one, headers=other_auth),
        client.patch(one, headers=other_auth, json={"required": True}),
        client.delete(one, headers=other_auth),
        client.get(f"{one}/generations", headers=other_auth),
        act(client, other_auth, ready["id"], q["id"], "generate"),
        act(client, other_auth, ready["id"], q["id"], "regenerate"),
        act(client, other_auth, ready["id"], q["id"], "accept"),
        act(client, other_auth, ready["id"], q["id"], "reject"),
        act(client, other_auth, ready["id"], q["id"], "clear"),
        act(client, other_auth, ready["id"], q["id"], "manual", {"answer": "hacked"}),
    ):
        assert response.status_code == 404, response.text
    # nothing was changed
    assert get_q(client, auth, ready, q)["answer"]["final_answer"] is None


def test_a_question_is_only_reachable_through_its_own_application(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    q = add_question(client, auth, ready["id"])
    second_app = started(client, auth, make_job(db_session, user_by_email(db_session)), {})
    assert (
        client.get(f"{questions_url(second_app['id'])}/{q['id']}", headers=auth).status_code == 404
    )
    assert act(client, auth, second_app["id"], q["id"], "generate").status_code == 404


def test_unauthenticated_requests_are_refused(client: TestClient, ready: dict[str, Any]) -> None:
    assert client.get(questions_url(ready["id"])).status_code == 401


def test_the_database_enforces_ownership(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    ready: dict[str, Any],
    db_session: Session,
) -> None:
    q = add_question(client, auth, ready["id"])
    mallory = user_by_email(db_session, "mallory@example.com")
    # a question for someone else's application
    with pytest.raises(IntegrityError):
        db_session.add(
            ApplicationQuestion(
                application_id=ready["id"],
                user_id=mallory.id,
                question_text="x",
                normalized_question_text="x",
                question_hash="0" * 64,
            )
        )
        db_session.commit()
    db_session.rollback()
    # an answer or an attempt for someone else's question
    with pytest.raises(IntegrityError):
        db_session.add(ApplicationQuestionAnswer(question_id=q["id"], user_id=mallory.id))
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.add(
            ApplicationQuestionGeneration(
                question_id=q["id"],
                user_id=mallory.id,
                method=GenerationMethod.AI,
                outcome=GenerationOutcome.FAILED,
                question_fingerprint="0" * 64,
                input_hash="0" * 64,
                prompt_version="v",
                prompt_hash="0" * 64,
                ai_provider="x",
            )
        )
        db_session.commit()
    db_session.rollback()


def test_the_database_keeps_answer_state_coherent(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    q = add_question(client, auth, ready["id"])
    answer = db_session.scalar(
        select(ApplicationQuestionAnswer).where(ApplicationQuestionAnswer.question_id == q["id"])
    )
    assert answer is not None
    from app.models import AnswerSource, AnswerStatus

    # "accepted" without a final answer
    answer.status = AnswerStatus.ACCEPTED
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    # a final answer without a source
    answer.final_answer = "x"
    answer.status = AnswerStatus.USER_EDITED
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    # a user-edited answer cannot claim to be the AI's
    answer.final_answer = "x"
    answer.status = AnswerStatus.USER_EDITED
    answer.answer_source = AnswerSource.AI
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    # a suggestion status without a suggestion
    answer.status = AnswerStatus.SUGGESTED
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_deleting_the_application_removes_everything(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    q = add_question(client, auth, ready["id"], "Email")
    ok(gen(client, auth, ready, q))
    assert rows(db_session, ApplicationQuestionGeneration)
    db_session.delete(
        db_session.get(__import__("app.models", fromlist=["Application"]).Application, ready["id"])
    )
    db_session.commit()
    assert not rows(db_session, ApplicationQuestion)
    assert not rows(db_session, ApplicationQuestionAnswer)
    assert not rows(db_session, ApplicationQuestionGeneration)


# --- Deterministic answers (no AI) ------------------------------------------------------------


def test_salary_comes_from_the_application_and_the_ai_is_not_called(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = add_question(client, auth, ready["id"], "What are your salary expectations?")
    calls = len(fake_ai.calls)  # the resume analysis
    answer = ok(gen(client, auth, ready, q))["answer"]
    assert fake_ai.question_calls == 0 and len(fake_ai.calls) == calls
    assert answer["status"] == "SUGGESTED"
    assert answer["ai_suggestion"] == "78.000 EUR brutto pro Jahr"
    assert answer["suggestion_method"] == "DETERMINISTIC"
    assert answer["ai_source_fact_ids"] == ["app.salary_expectation"]
    assert answer["source_facts"]["app.salary_expectation"]["text"].endswith("(annual gross)")
    provenance = answer["ai_provenance"]
    assert provenance["method"] == "DETERMINISTIC" and provenance["prompt_version"] is None
    assert provenance["validation_status"] == "not_applicable"
    assert answer["final_answer"] is None  # a suggestion is not an answer


def test_salary_type_is_always_the_salary_expectation(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = add_question(client, auth, ready["id"], "Vergütung", question_type="SALARY")
    assert ok(gen(client, auth, ready, q))["answer"]["ai_suggestion"] == "78000"


def test_missing_salary_needs_input_and_is_never_guessed(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeQuestionAI
) -> None:
    app = make_ready(client, auth, db_session, prep={})
    q = add_question(client, auth, app["id"], "Desired salary", question_type="NUMERIC")
    answer = ok(gen(client, auth, app, q))["answer"]
    assert answer["status"] == "NEEDS_INPUT" and answer["needs_input_reason"] == "SALARY_MISSING"
    assert "salary" in answer["needs_input_message"].lower()
    assert answer["ai_suggestion"] is None and fake_ai.question_calls == 0


@pytest.mark.parametrize(
    ("text", "question_type", "extra", "expected"),
    [
        ("When can you start?", "FREE_TEXT", {}, "Verfügbar ab 01.01.2027"),
        ("Earliest start date", "DATE", {}, "2027-01-01"),
        ("What is your notice period?", "FREE_TEXT", {}, "3 Monate"),
        ("Notice period (in months)", "NUMERIC", {}, "3"),
        (
            "Notice period",
            "SINGLE_CHOICE",
            {"options": ["1 month", "3 months"]},
            None,
        ),
        ("Email", "FREE_TEXT", {}, "alex.example@example.com"),
        ("Phone number", "FREE_TEXT", {}, "+49 170 1234567"),
        ("Current location", "FREE_TEXT", {}, "Berlin, Germany"),
        ("Preferred language of communication", "FREE_TEXT", {}, "Deutsch"),
    ],
)
def test_structured_questions_are_answered_without_ai(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeQuestionAI,
    text: str,
    question_type: str,
    extra: dict[str, Any],
    expected: str | None,
) -> None:
    q = add_question(client, auth, ready["id"], text, question_type=question_type, **extra)
    answer = ok(gen(client, auth, ready, q))["answer"]
    assert fake_ai.question_calls == 0
    assert answer["status"] == "SUGGESTED"
    if expected is None:
        assert answer["ai_selected_options"] == ["3 months"] and answer["ai_suggestion"] is None
    else:
        assert answer["ai_suggestion"] == expected
    assert answer["suggestion_method"] == "DETERMINISTIC"


def test_work_authorization_needs_an_explicit_value(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeQuestionAI
) -> None:
    unknown = make_ready(client, auth, db_session)
    q = add_question(
        client,
        auth,
        unknown["id"],
        "Are you authorized to work in Germany?",
        question_type="YES_NO",
    )
    answer = ok(gen(client, auth, unknown, q))["answer"]
    assert answer["status"] == "NEEDS_INPUT"
    assert answer["needs_input_reason"] == "WORK_AUTHORIZATION_MISSING"
    # now state it
    ok(
        client.patch(
            f"{API}/applications/{unknown['id']}",
            headers=auth,
            json={"work_authorization": "authorized"},
        )
    )
    again = ok(regen(client, auth, unknown, q))["answer"]
    assert again["status"] == "SUGGESTED" and again["ai_suggestion"] == "Ja"
    assert fake_ai.question_calls == 0


def test_contact_details_need_a_confirmed_resume(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    app = make_ready(client, auth, db_session, confirm=False)
    q = add_question(client, auth, app["id"], "Email")
    answer = ok(gen(client, auth, app, q))["answer"]
    assert answer["status"] == "NEEDS_INPUT" and answer["needs_input_reason"] == "CONTACT_MISSING"


def test_the_application_language_is_not_inferred_from_the_question(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    app = make_ready(client, auth, db_session, language=None)
    q = add_question(client, auth, app["id"], "Bevorzugte Sprache der Kommunikation")
    answer = ok(gen(client, auth, app, q))["answer"]
    assert answer["status"] == "NEEDS_INPUT" and answer["needs_input_reason"] == "LANGUAGE_MISSING"


def test_deterministic_answers_respect_the_character_limit(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    short = add_question(client, auth, ready["id"], "Salary expectations", max_length=6)
    assert ok(gen(client, auth, ready, short))["answer"]["ai_suggestion"] == "78.000"
    tiny = add_question(client, auth, ready["id"], "Salary expectations ", max_length=3)
    answer = ok(gen(client, auth, ready, tiny))["answer"]
    assert answer["status"] == "NEEDS_INPUT" and answer["needs_input_reason"] == "ANSWER_TOO_LONG"


def test_deterministic_answers_work_without_any_ai_provider(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    fake_ai.question_error = AINotConfiguredError("disabled")
    q = add_question(client, auth, ready["id"], "When can you start?")
    assert ok(gen(client, auth, ready, q))["answer"]["status"] == "SUGGESTED"


# --- AI suggestions ---------------------------------------------------------------------------


def test_an_ai_suggestion_is_validated_cited_and_stored_with_provenance(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeQuestionAI,
    db_session: Session,
) -> None:
    q = add_question(client, auth, ready["id"], WHY, max_length=500)
    result = ok(gen(client, auth, ready, q))
    answer = result["answer"]
    assert fake_ai.question_calls == 1
    assert answer["status"] == "SUGGESTED" and answer["suggestion_method"] == "AI"
    assert answer["ai_suggestion"].startswith("In meiner Arbeit:")
    assert answer["final_answer"] is None and answer["answer_source"] is None
    assert answer["ai_source_fact_ids"] and set(answer["ai_source_fact_ids"]) == set(
        answer["source_facts"]
    )

    provenance = answer["ai_provenance"]
    prompt_file = (
        Path(__file__).resolve().parents[1] / "app" / "ai" / "prompts" / "application_answer_v1.txt"
    )
    assert provenance["prompt_version"] == "application_answer_v1"
    assert provenance["prompt_hash"] == hashlib.sha256(prompt_file.read_bytes()).hexdigest()
    assert provenance["ai_provider"] == "fake" and provenance["ai_model"] == "fake-model-1"
    assert provenance["resume_version_number"] == 1 and provenance["resume_id"]
    assert len(provenance["input_hash"]) == 64 and len(provenance["question_fingerprint"]) == 64
    assert provenance["validation_status"] == "passed" and provenance["max_length"] == 500
    snapshot = db_session.scalar(
        select(ApplicationContextSnapshot).where(
            ApplicationContextSnapshot.application_id == ready["id"]
        )
    )
    assert snapshot is not None and provenance["application_context_snapshot_id"] == str(
        snapshot.id
    )

    attempts = ok(client.get(f"{questions_url(ready['id'])}/{q['id']}/generations", headers=auth))[
        "items"
    ]
    assert len(attempts) == 1 and attempts[0]["outcome"] == "SUGGESTED"
    assert attempts[0]["validation"]["status"] == "passed"
    assert attempts[0]["id"] == provenance["generation_id"] == answer["ai_generation_id"]
    # the prompt text is never part of any response
    assert "<question_context>" not in str(result) + str(attempts)


def test_the_ai_is_shown_the_question_and_trusted_facts_only(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = add_question(client, auth, ready["id"], WHY, max_length=250)
    ok(gen(client, auth, ready, q))
    context = fake_ai.question_contexts[0]
    assert context["question"] == {
        "text": WHY,
        "type": "FREE_TEXT",
        "required": False,
        "max_length": 250,
        "options": None,
    }
    assert context["output_language"]["code"] == "de"
    shown = fake_ai.question_prompts[0]
    for secret in ("78000", "78.000", "alex.example@example.com", "+49 170", "Jane", "Doe"):
        assert secret not in shown
    assert "app.available_from" in str(context["application_facts"])


def test_the_resume_must_be_confirmed_for_an_ai_suggestion_but_not_for_manual_answers(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeQuestionAI
) -> None:
    app = make_ready(client, auth, db_session, confirm=False)
    q = add_question(client, auth, app["id"], WHY)
    body = error(gen(client, auth, app, q), 409, "RESUME_MISSING")
    assert body["details"]["blockers"]
    assert fake_ai.question_calls == 0
    listing = ok(client.get(questions_url(app["id"]), headers=auth))
    assert listing["ai"]["can_generate"] is False
    # answering by hand is never blocked
    done = ok(manual(client, auth, app, q, answer="Because I like it."))
    assert done["answer"]["status"] == "USER_EDITED" and done["answer"]["answer_source"] == "USER"


def test_the_language_is_required_for_an_ai_suggestion(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    app = make_ready(client, auth, db_session, language=None)
    q = add_question(client, auth, app["id"], WHY)
    error(gen(client, auth, app, q), 409, "LANGUAGE_MISSING")


def test_the_ai_saying_it_cannot_answer_is_needs_input(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    fake_ai.answer_override = needs_input()
    q = add_question(
        client,
        auth,
        ready["id"],
        "How many years of Java experience do you have?",
        question_type="NUMERIC",
    )
    answer = ok(gen(client, auth, ready, q))["answer"]
    assert (
        answer["status"] == "NEEDS_INPUT" and answer["needs_input_reason"] == "FACTS_INSUFFICIENT"
    )
    assert answer["ai_suggestion"] is None and answer["final_answer"] is None
    attempt = ok(client.get(f"{questions_url(ready['id'])}/{q['id']}/generations", headers=auth))[
        "items"
    ][0]
    assert attempt["outcome"] == "NEEDS_INPUT" and attempt["validation"]["status"] == "passed"


def test_an_invented_number_is_rejected_and_nothing_is_suggested(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    """The spec's example: "Worked extensively with Java" never becomes "5"."""
    fake_ai.respond = lambda ctx: text_answer(
        "5", fact_with(ctx, "SAP IS-U")["id"], answer_type="NUMBER"
    )
    q = add_question(
        client,
        auth,
        ready["id"],
        "How many years of Java experience do you have?",
        question_type="NUMERIC",
    )
    body = error(gen(client, auth, ready, q), 502, "ANSWER_REJECTED")
    assert {v["code"] for v in body["details"]["violations"]} >= {"UNSUPPORTED_NUMBER"}
    answer = get_q(client, auth, ready, q)["answer"]
    assert answer["status"] == "NOT_STARTED" and answer["ai_suggestion"] is None
    attempt = ok(client.get(f"{questions_url(ready['id'])}/{q['id']}/generations", headers=auth))[
        "items"
    ][0]
    assert attempt["outcome"] == "REJECTED" and attempt["suggestion"] is None
    assert (
        attempt["validation"]["status"] == "failed" and attempt["error_code"] == "ANSWER_REJECTED"
    )
    assert attempt["prompt_version"] == "application_answer_v1"


@pytest.mark.parametrize(
    ("output", "code"),
    [
        (text_answer("I led a team of engineers at Globex Inc."), "MISSING_SOURCE"),
        (text_answer("I admire your innovative culture.", "exp1.r1"), "COMPANY_KNOWLEDGE_CLAIM"),
        (text_answer("I developed SAP IS-U.", "does.not.exist"), "UNKNOWN_FACT_ID"),
        (text_answer("I plan projects.", "job.resp1"), "JOB_FACT_AS_SOURCE"),
        (text_answer("I developed SAP IS-U for 12 years.", "exp1.r1"), "UNSUPPORTED_NUMBER"),
    ],
)
def test_unsupported_ai_answers_are_rejected_whole(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeQuestionAI,
    output: dict[str, Any],
    code: str,
) -> None:
    fake_ai.answer_override = output
    q = add_question(client, auth, ready["id"], WHY)
    body = error(gen(client, auth, ready, q), 502, "ANSWER_REJECTED")
    assert code in {v["code"] for v in body["details"]["violations"]}
    assert get_q(client, auth, ready, q)["answer"]["status"] == "NOT_STARTED"


def test_malformed_output_is_a_failed_attempt_not_a_suggestion(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    fake_ai.answer_override = {
        "answer": "x",
        "needs_user_input": True,
        "answer_type": "NEEDS_INPUT",
    }
    q = add_question(client, auth, ready["id"], WHY)
    error(gen(client, auth, ready, q), 502, "AI_INVALID_RESPONSE")
    attempt = ok(client.get(f"{questions_url(ready['id'])}/{q['id']}/generations", headers=auth))[
        "items"
    ][0]
    assert attempt["outcome"] == "FAILED" and attempt["error_code"] == "AI_INVALID_RESPONSE"
    assert get_q(client, auth, ready, q)["answer"]["ai_suggestion"] is None


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (AINotConfiguredError("x"), 503, "AI_NOT_CONFIGURED"),
        (AIRateLimitError("x"), 429, "AI_RATE_LIMITED"),
        (AIProviderUnavailableError("x"), 503, "AI_UNAVAILABLE"),
        (AIInvalidResponseError("x"), 502, "AI_INVALID_RESPONSE"),
        (RuntimeError("boom with secret resume text"), 500, "GENERATION_ERROR"),
    ],
)
def test_provider_failures_are_safe_and_recorded(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeQuestionAI,
    exc: Exception,
    status: int,
    code: str,
) -> None:
    fake_ai.question_error = exc
    q = add_question(client, auth, ready["id"], WHY)
    body = error(gen(client, auth, ready, q), status, code)
    assert "secret" not in str(body)
    attempt = ok(client.get(f"{questions_url(ready['id'])}/{q['id']}/generations", headers=auth))[
        "items"
    ][0]
    assert attempt["outcome"] == "FAILED" and attempt["error_code"] == code
    # the user can still answer, and retry generating
    assert ok(manual(client, auth, ready, q, answer="Mine."))["answer"]["status"] == "USER_EDITED"


def test_character_limit_for_ai_output(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = add_question(client, auth, ready["id"], WHY, max_length=40)
    # the faithful fake restates a long responsibility: over the limit -> rejected
    body = error(gen(client, auth, ready, q), 502, "ANSWER_REJECTED")
    assert "ANSWER_TOO_LONG" in {v["code"] for v in body["details"]["violations"]}
    assert get_q(client, auth, ready, q)["answer"]["ai_suggestion"] is None
    fake_ai.respond = lambda ctx: text_answer(
        "I develop SAP IS-U.", fact_with(ctx, "SAP IS-U")["id"]
    )
    answer = ok(regen(client, auth, ready, q))["answer"]
    assert answer["ai_suggestion"] == "I develop SAP IS-U." and len(answer["ai_suggestion"]) <= 40


def test_choice_answers_use_only_listed_options(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = add_question(
        client,
        auth,
        ready["id"],
        "Which technology do you know best?",
        question_type="SINGLE_CHOICE",
        options=["ABAP", "SQL", "Python"],
    )
    fake_ai.respond = lambda ctx: option_answer("ABAP Lead", facts=(fact_with(ctx, "ABAP")["id"],))
    error(gen(client, auth, ready, q), 502, "ANSWER_REJECTED")
    fake_ai.respond = lambda ctx: option_answer("abap", facts=(fact_with(ctx, "ABAP")["id"],))
    answer = ok(gen(client, auth, ready, q))["answer"]
    assert answer["ai_selected_options"] == ["ABAP"] and answer["ai_suggestion"] is None
    accepted = ok(act(client, auth, ready["id"], q["id"], "accept"))["answer"]
    assert accepted["final_selected_options"] == ["ABAP"] and accepted["final_answer"] is None


def test_multi_choice(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = add_question(
        client,
        auth,
        ready["id"],
        "Which do you know?",
        question_type="MULTI_CHOICE",
        options=["ABAP", "SQL", "Python"],
    )
    fake_ai.respond = lambda ctx: option_answer(
        "ABAP", "SQL", facts=(fact_with(ctx, "ABAP")["id"], fact_with(ctx, "SQL")["id"])
    )
    assert ok(gen(client, auth, ready, q))["answer"]["ai_selected_options"] == ["ABAP", "SQL"]


def test_yes_no_driving_licence_is_answered_only_from_a_supporting_fact(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = add_question(
        client, auth, ready["id"], "Do you have a driving license?", question_type="YES_NO"
    )
    fake_ai.respond = lambda ctx: text_answer(
        "Yes", fact_with(ctx, "Driving license")["id"], answer_type="YES_NO"
    )
    assert ok(gen(client, auth, ready, q))["answer"]["ai_suggestion"] == "Yes"
    # "No" because nothing was found is refused
    other = add_question(
        client, auth, ready["id"], "Do you have a pilot license?", question_type="YES_NO"
    )
    fake_ai.respond = lambda ctx: text_answer(
        "No", fact_with(ctx, "SAP IS-U")["id"], answer_type="YES_NO"
    )
    error(gen(client, auth, ready, other), 502, "ANSWER_REJECTED")


# --- Human Control ----------------------------------------------------------------------------


def suggested(
    client: TestClient, auth: dict[str, str], app: dict[str, Any], text: str = WHY, **fields: Any
) -> dict[str, Any]:
    q = add_question(client, auth, app["id"], text, **fields)
    ok(gen(client, auth, app, q))
    return q


def test_accept_copies_the_suggestion_and_keeps_it_auditable(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready)
    before = get_q(client, auth, ready, q)["answer"]
    after = ok(act(client, auth, ready["id"], q["id"], "accept"))["answer"]
    assert after["status"] == "ACCEPTED" and after["answer_source"] == "AI"
    assert after["final_answer"] == before["ai_suggestion"] == after["ai_suggestion"]
    assert after["basis_generation_id"] == before["ai_generation_id"]


def test_accepting_a_deterministic_suggestion_is_marked_deterministic(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready, "When can you start?")
    answer = ok(act(client, auth, ready["id"], q["id"], "accept"))["answer"]
    assert answer["status"] == "ACCEPTED" and answer["answer_source"] == "DETERMINISTIC"


def test_nothing_to_accept_or_reject(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = add_question(client, auth, ready["id"])
    error(act(client, auth, ready["id"], q["id"], "accept"), 409, "NO_SUGGESTION")
    error(act(client, auth, ready["id"], q["id"], "reject"), 409, "NO_SUGGESTION")


def test_a_client_cannot_supply_or_alter_what_is_accepted(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready)
    for body in (
        {"answer": "I am the best"},
        {"ai_suggestion": "x"},
        {"source_fact_ids": ["exp1"]},
        {"status": "ACCEPTED"},
    ):
        assert act(client, auth, ready["id"], q["id"], "accept", body).status_code == 422
    regenerate_bodies: tuple[dict[str, Any], ...] = (
        {"source_fact_ids": ["exp1"]},
        {"acknowledge_existing_answer": True, "status": "x"},
    )
    for body in regenerate_bodies:
        assert act(client, auth, ready["id"], q["id"], "regenerate", body).status_code == 422
    manual_body = {"answer": "x", "status": "ACCEPTED"}
    assert manual(client, auth, ready, q, **manual_body).status_code == 422
    assert get_q(client, auth, ready, q)["answer"]["status"] == "SUGGESTED"


def test_editing_the_suggestion_keeps_the_original_and_marks_the_answer_hybrid(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready)
    original = get_q(client, auth, ready, q)["answer"]
    edited = ok(
        manual(
            client,
            auth,
            ready,
            q,
            answer="I have around four years of experience.",
            based_on_suggestion=True,
        )
    )["answer"]
    assert edited["status"] == "USER_EDITED" and edited["answer_source"] == "HYBRID"
    assert edited["final_answer"] == "I have around four years of experience."
    # the AI's words are untouched and still point at their attempt
    assert edited["ai_suggestion"] == original["ai_suggestion"]
    assert (
        edited["ai_generation_id"] == original["ai_generation_id"] == edited["basis_generation_id"]
    )
    assert edited["ai_provenance"] == original["ai_provenance"]


def test_a_manual_answer_needs_no_ai_at_all(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = add_question(client, auth, ready["id"])
    calls = len(fake_ai.calls)
    answer = ok(manual(client, auth, ready, q, answer="  Because the work fits me.  "))["answer"]
    assert len(fake_ai.calls) == calls
    assert answer["status"] == "USER_EDITED" and answer["answer_source"] == "USER"
    assert answer["final_answer"] == "Because the work fits me."
    assert answer["basis_generation_id"] is None and answer["ai_suggestion"] is None
    assert not rows_any_generation(client, auth, ready, q)


def rows_any_generation(
    client: TestClient, auth: dict[str, str], app: dict[str, Any], q: dict[str, Any]
) -> list[Any]:
    return ok(client.get(f"{questions_url(app['id'])}/{q['id']}/generations", headers=auth))[
        "items"
    ]  # type: ignore[no-any-return]


def test_typing_out_the_suggestion_unchanged_is_accepting_it(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready)
    text = get_q(client, auth, ready, q)["answer"]["ai_suggestion"]
    answer = ok(manual(client, auth, ready, q, answer=text))["answer"]
    assert answer["status"] == "ACCEPTED" and answer["answer_source"] == "AI"


def test_editing_an_accepted_answer_makes_it_hybrid_and_keeps_its_basis(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready)
    accepted = ok(act(client, auth, ready["id"], q["id"], "accept"))["answer"]
    edited = ok(manual(client, auth, ready, q, answer="Reworded by me."))["answer"]
    assert (
        edited["answer_source"] == "HYBRID"
        and edited["basis_generation_id"] == accepted["basis_generation_id"]
    )
    # and a second edit stays hybrid
    again = ok(manual(client, auth, ready, q, answer="Reworded again."))["answer"]
    assert again["answer_source"] == "HYBRID"


def test_reject_without_an_answer(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready)
    rejected = ok(act(client, auth, ready["id"], q["id"], "reject"))["answer"]
    assert rejected["status"] == "REJECTED" and rejected["suggestion_dismissed"] is True
    assert rejected["final_answer"] is None and rejected["ai_suggestion"]  # still auditable
    # a rejected suggestion can still be taken back
    assert ok(act(client, auth, ready["id"], q["id"], "accept"))["answer"]["status"] == "ACCEPTED"


def test_reject_leaves_the_users_answer_alone(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready)
    ok(manual(client, auth, ready, q, answer="Mine."))
    answer = ok(act(client, auth, ready["id"], q["id"], "reject"))["answer"]
    assert answer["status"] == "USER_EDITED" and answer["final_answer"] == "Mine."
    assert answer["suggestion_dismissed"] is True


def test_clear_removes_only_the_users_answer(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready)
    ok(manual(client, auth, ready, q, answer="Mine."))
    cleared = ok(act(client, auth, ready["id"], q["id"], "clear"))["answer"]
    assert cleared["final_answer"] is None and cleared["answer_source"] is None
    assert cleared["status"] == "SUGGESTED" and cleared["ai_suggestion"]
    only_manual = add_question(client, auth, ready["id"], "Anything?")
    ok(manual(client, auth, ready, only_manual, answer="x"))
    assert (
        ok(act(client, auth, ready["id"], only_manual["id"], "clear"))["answer"]["status"]
        == "NOT_STARTED"
    )


def test_generate_twice_points_at_regenerate(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = suggested(client, auth, ready)
    error(gen(client, auth, ready, q), 409, "ALREADY_GENERATED")
    assert fake_ai.question_calls == 1


def test_regeneration_creates_a_new_attempt_and_keeps_the_old_one(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = suggested(client, auth, ready)
    first = get_q(client, auth, ready, q)["answer"]
    fake_ai.respond = lambda ctx: text_answer(
        "I develop SAP IS-U applications.", fact_with(ctx, "SAP IS-U")["id"]
    )
    second = ok(regen(client, auth, ready, q))["answer"]
    assert second["ai_suggestion"] == "I develop SAP IS-U applications."
    assert second["ai_generation_id"] != first["ai_generation_id"]
    attempts = rows_any_generation(client, auth, ready, q)
    assert [a["id"] for a in attempts][::-1][0] == first["ai_generation_id"]
    assert len(attempts) == 2
    assert (
        next(a for a in attempts if a["id"] == first["ai_generation_id"])["suggestion"]
        == first["ai_suggestion"]
    )


def test_regeneration_never_overwrites_the_users_answer(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = suggested(client, auth, ready)
    ok(
        manual(
            client,
            auth,
            ready,
            q,
            answer="I have around four years of experience.",
            based_on_suggestion=True,
        )
    )
    # a user-edited answer: regenerating needs an explicit acknowledgement ...
    body = error(regen(client, auth, ready, q), 409, "EXISTING_ANSWER_CONFIRMATION_REQUIRED")
    assert body["details"]["status"] == "USER_EDITED"
    assert error(gen(client, auth, ready, q), 409, "ALREADY_GENERATED")
    assert fake_ai.question_calls == 1  # no AI call without the acknowledgement
    # ... and even then only the suggestion changes
    fake_ai.respond = lambda ctx: text_answer(
        "I develop SAP IS-U applications.", fact_with(ctx, "SAP IS-U")["id"]
    )
    answer = ok(regen(client, auth, ready, q, acknowledge_existing_answer=True))["answer"]
    assert answer["final_answer"] == "I have around four years of experience."
    assert answer["status"] == "USER_EDITED" and answer["answer_source"] == "HYBRID"
    assert answer["ai_suggestion"] == "I develop SAP IS-U applications."
    # taking the new suggestion would replace the answer: that must be confirmed
    error(act(client, auth, ready["id"], q["id"], "accept"), 409, "ANSWER_WOULD_BE_REPLACED")
    assert (
        get_q(client, auth, ready, q)["answer"]["final_answer"]
        == "I have around four years of experience."
    )
    replaced = ok(act(client, auth, ready["id"], q["id"], "accept", {"replace_existing": True}))[
        "answer"
    ]
    assert (
        replaced["final_answer"] == "I develop SAP IS-U applications."
        and replaced["status"] == "ACCEPTED"
    )


def test_a_failed_or_rejected_regeneration_changes_nothing(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = suggested(client, auth, ready)
    before = get_q(client, auth, ready, q)["answer"]
    fake_ai.answer_override = text_answer("I led 40 engineers.", "exp1.r1")
    error(regen(client, auth, ready, q), 502, "ANSWER_REJECTED")
    fake_ai.answer_override = None
    fake_ai.question_error = AIRateLimitError("x")
    error(regen(client, auth, ready, q), 429, "AI_RATE_LIMITED")
    after = get_q(client, auth, ready, q)["answer"]
    assert after["ai_suggestion"] == before["ai_suggestion"] and after["status"] == before["status"]
    assert after["ai_generation_id"] == before["ai_generation_id"]
    assert len(rows_any_generation(client, auth, ready, q)) == 3  # the good one + 2 failures


def test_a_regeneration_that_needs_input_replaces_only_the_suggestion(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    q = suggested(client, auth, ready)
    ok(act(client, auth, ready["id"], q["id"], "accept"))
    fake_ai.answer_override = needs_input()
    answer = ok(regen(client, auth, ready, q, acknowledge_existing_answer=True))["answer"]
    assert answer["ai_suggestion"] is None and answer["status"] == "ACCEPTED"
    assert answer["final_answer"]  # the accepted answer is untouched


# --- What a user may write --------------------------------------------------------------------


def test_a_user_answer_over_the_limit_is_refused_never_truncated(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = add_question(client, auth, ready["id"], WHY, max_length=10)
    body = error(manual(client, auth, ready, q, answer="x" * 11), 422, "ANSWER_TOO_LONG")
    assert body["details"] == {"length": 11, "max_length": 10}
    assert get_q(client, auth, ready, q)["answer"]["final_answer"] is None
    exact = ok(manual(client, auth, ready, q, answer="y" * 10))["answer"]
    assert exact["final_answer"] == "y" * 10


def test_the_answer_is_stored_exactly_as_written(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = add_question(client, auth, ready["id"])
    text = "Line one.\n\nLine two with ünïcode and  double  spaces."
    assert ok(manual(client, auth, ready, q, answer=text))["answer"]["final_answer"] == text


def test_choice_answers_must_be_listed_options(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = add_question(
        client,
        auth,
        ready["id"],
        "Seniority?",
        question_type="SINGLE_CHOICE",
        options=["Junior", "Senior"],
    )
    error(manual(client, auth, ready, q, selected_options=["Senior / Lead"]), 422, "INVALID_OPTION")
    error(
        manual(client, auth, ready, q, selected_options=["Junior", "Senior"]), 422, "INVALID_OPTION"
    )
    error(manual(client, auth, ready, q, answer="Senior"), 422, "INVALID_OPTION")
    answer = ok(manual(client, auth, ready, q, selected_options=["senior"]))["answer"]
    assert answer["final_selected_options"] == ["Senior"] and answer["final_answer"] is None
    assert answer["status"] == "USER_EDITED"


def test_text_questions_take_text_and_numbers_take_numbers(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    free = add_question(client, auth, ready["id"], "Why?")
    error(manual(client, auth, ready, free, selected_options=["x"]), 422, "INVALID_ANSWER")
    number = add_question(client, auth, ready["id"], "How many projects?", question_type="NUMERIC")
    error(manual(client, auth, ready, number, answer="many"), 422, "INVALID_NUMBER")
    assert ok(manual(client, auth, ready, number, answer="12"))["answer"]["final_answer"] == "12"
    yes_no = add_question(client, auth, ready["id"], "Remote?", question_type="YES_NO")
    error(manual(client, auth, ready, yes_no, answer="Perhaps"), 422, "INVALID_OPTION")
    assert ok(manual(client, auth, ready, yes_no, answer="yes"))["answer"]["final_answer"] == "Yes"


def test_an_empty_manual_answer_is_refused(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = add_question(client, auth, ready["id"])
    for body in ({}, {"answer": "   "}, {"answer": "x", "selected_options": ["y"]}):
        assert manual(client, auth, ready, q, **body).status_code == 422


def test_editing_a_question_never_silently_changes_the_answer(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = add_question(client, auth, ready["id"], WHY, max_length=100)
    ok(manual(client, auth, ready, q, answer="x" * 80))
    url = f"{questions_url(ready['id'])}/{q['id']}"
    error(client.patch(url, headers=auth, json={"max_length": 50}), 409, "ANSWER_CONFLICT")
    error(
        client.patch(url, headers=auth, json={"question_type": "NUMERIC"}), 409, "ANSWER_CONFLICT"
    )
    still = get_q(client, auth, ready, q)
    assert still["max_length"] == 100 and still["answer"]["final_answer"] == "x" * 80
    assert ok(client.patch(url, headers=auth, json={"max_length": 80}))["max_length"] == 80


def test_a_suggestion_for_a_changed_question_is_flagged_and_cannot_be_accepted_if_unfit(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    fake_ai.respond = lambda ctx: text_answer(
        "I develop SAP IS-U applications.", fact_with(ctx, "SAP IS-U")["id"]
    )
    q = add_question(client, auth, ready["id"], WHY)
    ok(gen(client, auth, ready, q))
    url = f"{questions_url(ready['id'])}/{q['id']}"
    assert get_q(client, auth, ready, q)["answer"]["suggestion_outdated"] is False
    changed = ok(client.patch(url, headers=auth, json={"max_length": 10}))
    assert changed["answer"]["suggestion_outdated"] is True
    error(act(client, auth, ready["id"], q["id"], "accept"), 409, "SUGGESTION_OUTDATED")


def test_a_deterministic_suggestion_goes_stale_when_the_data_changes(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    q = suggested(client, auth, ready, "What are your salary expectations?")
    ok(
        client.patch(
            f"{API}/applications/{ready['id']}", headers=auth, json={"salary_expectation": "90000"}
        )
    )
    error(act(client, auth, ready["id"], q["id"], "accept"), 409, "SUGGESTION_OUTDATED")
    fresh = ok(regen(client, auth, ready, q))["answer"]
    assert fresh["ai_suggestion"] == "90.000 EUR brutto pro Jahr"
    assert ok(act(client, auth, ready["id"], q["id"], "accept"))["answer"]["status"] == "ACCEPTED"


def test_attempts_are_write_once(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    suggested(client, auth, ready)
    attempt = rows(db_session, ApplicationQuestionGeneration)[0]
    attempt.suggestion = "tampered"
    with pytest.raises(ImmutableRecordError):
        db_session.commit()
    db_session.rollback()


def test_the_questions_list_carries_every_state(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeQuestionAI
) -> None:
    a = add_question(client, auth, ready["id"], "When can you start?")
    b = add_question(client, auth, ready["id"], "Why?")
    c = add_question(client, auth, ready["id"], "Salary expectations")
    add_question(client, auth, ready["id"], "Untouched")
    ok(gen(client, auth, ready, a))
    ok(act(client, auth, ready["id"], a["id"], "accept"))
    ok(manual(client, auth, ready, b, answer="Mine."))
    ok(gen(client, auth, ready, c))
    ok(act(client, auth, ready["id"], c["id"], "reject"))
    statuses = [
        item["answer"]["status"]
        for item in ok(client.get(questions_url(ready["id"]), headers=auth))["items"]
    ]
    assert statuses == ["ACCEPTED", "USER_EDITED", "REJECTED", "NOT_STARTED"]
