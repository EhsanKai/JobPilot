"""V0.8 tailored documents through the real API: generation, versions,
provenance, salary rules, authorization and failures. The AI is a
deterministic fake (`FakeDocumentAI`): no test calls a real provider."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.provider import (
    AIAuthenticationError,
    AIContextLimitError,
    AIError,
    AIInvalidResponseError,
    AINotConfiguredError,
    AIProvider,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
    T,
)
from app.models import (
    ApplicationContextSnapshot,
    GeneratedDocument,
    GeneratedDocumentStatus,
    GeneratedDocumentType,
    Job,
    User,
)
from app.models.mixins import ImmutableRecordError
from app.schemas.document_ai import AICoverLetter
from app.services.documents import service as document_service
from tests.application_helpers import (
    API,
    confirmed_resume,
    make_job,
    patch,
    started,
    upload_resume,
    user_by_email,
)
from tests.document_helpers import (
    FakeDocumentAI,
    documents_url,
    faithful_letter,
    generate,
    listing,
)
from tests.resume_helpers import expected_parsed_resume

CV = "cv"
LETTER = "cover-letter"

SALARY = {
    "salary_expectation": "78000",
    "salary_currency": "EUR",
    "mention_salary_in_letter": True,
}


@pytest.fixture
def fake_ai() -> FakeDocumentAI:
    return FakeDocumentAI()


@pytest.fixture
def auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {register_and_login()['access_token']}"}


@pytest.fixture
def other_auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = register_and_login(email="mallory@example.com")["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ready(client: TestClient, auth: dict[str, str], db_session: Session) -> dict[str, Any]:
    """An application with a confirmed resume and a complete preparation (German)."""
    return make_ready(client, auth, db_session)


def make_ready(
    client: TestClient,
    headers: dict[str, str],
    db: Session,
    *,
    email: str = "jane@example.com",
    language: str | None = "de",
    prep: dict[str, Any] | None = None,
    confirm: bool = True,
    job: Job | None = None,
) -> dict[str, Any]:
    user = user_by_email(db, email)
    job = job or make_job(db, user)
    if confirm:
        confirmed_resume(client, headers)
    body: dict[str, Any] = {
        "available_from": "2027-01-01",
        "notice_period": {"value": 3, "unit": "months"},
        **(prep if prep is not None else SALARY),
    }
    if language:
        body["application_language"] = language
    return started(client, headers, job, body)


def ok(response: Any, status: int = 201) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return response.json()  # type: ignore[no-any-return]


def error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body  # type: ignore[no-any-return]


def rows(db: Session, model: type = GeneratedDocument) -> list[Any]:
    db.expire_all()
    return list(db.scalars(select(model)).all())


# --- Tailored CV ------------------------------------------------------------------------------


def test_generating_a_cv_creates_a_validated_document_with_provenance(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeDocumentAI,
    db_session: Session,
) -> None:
    before = datetime.now(UTC)
    document = ok(generate(client, auth, ready["id"], CV))

    assert document["document_type"] == "tailored_cv"
    assert document["application_id"] == ready["id"]
    assert document["status"] == "validated"
    assert document["version_number"] == 1
    assert document["is_current"] is True and document["is_outdated"] is False
    assert document["language"] == "de"
    assert document["validation_status"] == "passed" and document["violation_count"] == 0
    assert document["error_code"] is None

    # Provenance: resume version, context snapshot, prompt, provider, model, time.
    assert document["resume_id"] == ready["resume"]["id"]
    assert document["resume_version_number"] == ready["resume"]["version_number"]
    snapshot = db_session.scalar(
        select(ApplicationContextSnapshot).where(
            ApplicationContextSnapshot.application_id == uuid.UUID(ready["id"])
        )
    )
    assert document["application_context_snapshot_id"] == str(snapshot.id)
    assert document["prompt_version"] == "tailored_cv_v1"
    assert document["ai_provider"] == "fake" and document["ai_model"] == "fake-model-1"
    assert len(document["input_hash"]) == 64
    created = datetime.fromisoformat(document["created_at"].replace("Z", "+00:00"))
    assert before - timedelta(seconds=5) <= created <= datetime.now(UTC) + timedelta(seconds=5)
    assert document["completed_at"] is not None

    # One AI call; the stored row agrees with the response.
    assert fake_ai.document_calls == 1
    (row,) = rows(db_session)
    assert row.status is GeneratedDocumentStatus.VALIDATED and row.version_number == 1
    assert row.user_id == user_by_email(db_session).id


def test_supported_facts_survive_into_the_cv_and_plain_facts_are_copied_from_the_resume(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    document = ok(generate(client, auth, ready["id"], CV))
    content = document["content"]

    assert content["kind"] == "tailored_cv" and content["language"] == "de"
    assert content["header"]["full_name"] == "Alex Example"
    assert content["header"]["email"] == "alex.example@example.com"
    # employers, titles and dates are the resume's, not the AI's
    roles = [(e["company"], e["job_title"], e["start_date"]) for e in content["experience"]]
    assert roles == [
        ("Example Energy GmbH", "SAP IS-U Developer", "2023-01"),
        ("Sample Consulting AG", "SAP ABAP Developer", "2019-03"),
    ]
    assert content["education"][0]["degree"] == "M.Sc."
    assert [s["name"] for s in content["skills"]] == [
        "SAP ABAP",
        "SAP IS-U",
        "SAP BTP",
        "SQL",
        "Git",
    ]
    assert {lang["language"] for lang in content["languages"]} == {"German", "English", "Spanish"}
    assert content["certifications"][0]["name"].startswith("SAP Certified Development Associate")
    # every AI-written text cites its facts, and the cited facts are resolvable
    bullet = content["experience"][0]["bullets"][2]
    assert "30 %" in bullet["text"] and bullet["source_fact_ids"] == ["exp1.a1"]
    assert document["source_facts"]["exp1.a1"]["text"].startswith("Reduced batch runtime by 30%")
    assert document["source_facts"]["exp1.a1"]["ref"].startswith("resume_profile:")


def test_the_cv_never_contains_a_salary_or_notes_or_the_job_company(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    patch(client, auth, ready["id"], {"notes": "Secret note about the recruiter."})
    document = ok(generate(client, auth, ready["id"], CV))
    dumped = json.dumps(document, ensure_ascii=False)
    for private in ("78000", "78.000", "Secret note", "Example GmbH"):
        assert private not in dumped


def test_generation_uses_the_application_job_snapshot_not_the_live_job(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeDocumentAI,
    db_session: Session,
) -> None:
    job = db_session.get(Job, uuid.UUID(ready["job_id"]))
    job.title = "Completely Different Title"
    job.company_name = "Other Corp"
    job.responsibilities = ["A different responsibility"]
    db_session.commit()

    ok(generate(client, auth, ready["id"], CV))
    letter = ok(generate(client, auth, ready["id"], LETTER))
    job_in_prompt = fake_ai.contexts[-1]["target_job"]
    assert job_in_prompt["title"] == "Senior Project Manager"
    assert job_in_prompt["company"] == "Example GmbH"
    assert job_in_prompt["responsibilities"][0]["text"] == "Plan and run projects"
    assert letter["content"]["subject"] == "Bewerbung als Senior Project Manager"
    assert letter["content"]["recipient"]["company_name"] == "Example GmbH"


def test_generation_uses_the_resume_version_the_application_references(
    client: TestClient,
    auth: dict[str, str],
    fake_ai: FakeDocumentAI,
    db_session: Session,
) -> None:
    app = make_ready(client, auth, db_session)  # builds on resume 1 (fixture resume)
    # A second, different resume is uploaded and confirmed afterwards.
    changed = expected_parsed_resume()
    changed["work_experience"][0]["company"] = "Brand New Employer GmbH"
    fake_ai.response = changed
    second = confirmed_resume(client, auth, "second.pdf")
    assert second["version_number"] == 2

    document = ok(generate(client, auth, app["id"], CV))
    assert document["resume_version_number"] == 1
    assert document["content"]["experience"][0]["company"] == "Example Energy GmbH"

    # Choosing the other version is an explicit change of the application.
    assert patch(client, auth, app["id"], {"resume_id": second["id"]}).status_code == 200
    document = ok(generate(client, auth, app["id"], CV))
    assert document["resume_version_number"] == 2
    assert document["content"]["experience"][0]["company"] == "Brand New Employer GmbH"


def test_only_confirmed_resume_facts_are_used(
    client: TestClient,
    auth: dict[str, str],
    fake_ai: FakeDocumentAI,
    db_session: Session,
) -> None:
    app = make_ready(client, auth, db_session)
    resume_id = app["resume"]["id"]
    # Reject the second role during a re-review: it must not be a fact any more.
    assert client.post(f"{API}/resumes/{resume_id}/profile/reopen", headers=auth).status_code == 200
    profile = client.get(f"{API}/resumes/{resume_id}/profile", headers=auth).json()["profile"]
    second = profile["data"]["work_experience"][1]["id"]
    assert (
        client.patch(
            f"{API}/resumes/{resume_id}/profile/items/{second}",
            headers=auth,
            json={"action": "reject"},
        ).status_code
        == 200
    )
    # A resume under review is not confirmed: no document yet.
    error(generate(client, auth, app["id"], CV), 409, "RESUME_NOT_CONFIRMED")
    assert (
        client.post(f"{API}/resumes/{resume_id}/profile/confirm", headers=auth).status_code == 200
    )

    document = ok(generate(client, auth, app["id"], CV))
    assert [e["company"] for e in document["content"]["experience"]] == ["Example Energy GmbH"]
    assert "exp2" not in json.dumps(fake_ai.contexts[-1])


def test_unsupported_claims_are_rejected_and_never_become_a_document(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeDocumentAI,
    db_session: Session,
) -> None:
    def tamper(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        output["experience"][0]["bullets"][0]["text"] = (
            "Entwickelte Services in AWS bei Initech GmbH und leitete ein Team von 12 Personen."
        )
        output["skills"].append({"source_fact_id": "skill99", "emphasized": True})
        return output

    fake_ai.cv_tamper = tamper
    body = error(generate(client, auth, ready["id"], CV), 502, "DOCUMENT_REJECTED")
    assert "nothing was saved as a document" in body["message"]
    violations = {v["code"] for v in body["details"]["violations"]}
    assert {"UNSUPPORTED_ORGANIZATION", "UNSUPPORTED_NUMBER", "UNKNOWN_SKILL"} <= violations
    assert any(v["excerpt"] == "Initech GmbH" for v in body["details"]["violations"])

    # The attempt is kept as history -- as REJECTED, without content or version.
    (row,) = rows(db_session)
    assert row.status is GeneratedDocumentStatus.REJECTED
    assert row.content is None and row.version_number is None
    assert row.validation["status"] == "failed" and row.validation["violations"]
    assert "Initech" not in json.dumps(row.content)  # the rejected text itself is not stored

    state = listing(client, auth, ready["id"])
    (item,) = state["items"]
    assert item["status"] == "rejected" and item["version_number"] is None
    assert item["is_current"] is False and item["validation_status"] == "failed"
    assert item["violation_count"] == len(violations) or item["violation_count"] >= 3
    detail = ok(client.get(f"{documents_url(ready['id'])}/{item['id']}", headers=auth), 200)
    assert detail["content"] is None and detail["validation"]["status"] == "failed"


def test_a_rejected_attempt_leaves_the_previous_document_current(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    first = ok(generate(client, auth, ready["id"], CV))
    fake_ai.cv_tamper = lambda output, context: {
        **output,
        "summary": {"text": "Deployed to AWS.", "source_fact_ids": ["summary"]},
    }
    error(generate(client, auth, ready["id"], CV), 502, "DOCUMENT_REJECTED")

    items = listing(client, auth, ready["id"])["items"]
    assert [i["status"] for i in items] == ["rejected", "validated"]  # newest first
    current = [i for i in items if i["is_current"]]
    assert [c["id"] for c in current] == [first["id"]]

    # The next good generation is version 2: failed attempts don't use up numbers.
    fake_ai.cv_tamper = None
    assert ok(generate(client, auth, ready["id"], CV))["version_number"] == 2


# --- Versions and regeneration ----------------------------------------------------------------


def test_regeneration_creates_a_new_version_and_preserves_the_previous_one(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeDocumentAI,
    db_session: Session,
) -> None:
    first = ok(generate(client, auth, ready["id"], CV))
    stored_first = json.dumps(first["content"], sort_keys=True)
    second = ok(generate(client, auth, ready["id"], CV))

    assert (first["version_number"], second["version_number"]) == (1, 2)
    assert first["id"] != second["id"]
    assert fake_ai.document_calls == 2

    # v1 is untouched and still readable; only v2 is current.
    again = ok(client.get(f"{documents_url(ready['id'])}/{first['id']}", headers=auth), 200)
    assert json.dumps(again["content"], sort_keys=True) == stored_first
    assert again["version_number"] == 1 and again["is_current"] is False
    items = listing(client, auth, ready["id"])["items"]
    assert [(i["version_number"], i["is_current"]) for i in items] == [(2, True), (1, False)]
    assert len(rows(db_session)) == 2


def test_the_ai_original_is_write_once(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    ok(generate(client, auth, ready["id"], CV))
    (row,) = rows(db_session)
    row.content = {**row.content, "language": "en"}
    with pytest.raises(ImmutableRecordError):
        db_session.flush()
    db_session.rollback()
    row = db_session.scalars(select(GeneratedDocument)).one()
    row.ai_model = "another-model"
    with pytest.raises(ImmutableRecordError):
        db_session.flush()
    db_session.rollback()


def test_cv_and_letter_are_generated_independently(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    cv = ok(generate(client, auth, ready["id"], CV))
    assert fake_ai.document_calls == 1
    assert {i["document_type"] for i in listing(client, auth, ready["id"])["items"]} == {
        "tailored_cv"
    }
    letter = ok(generate(client, auth, ready["id"], LETTER))
    assert fake_ai.document_calls == 2
    assert (cv["version_number"], letter["version_number"]) == (1, 1)

    # Regenerating the CV leaves the letter alone (no AI call for it, same row).
    ok(generate(client, auth, ready["id"], CV))
    assert fake_ai.document_calls == 3
    items = listing(client, auth, ready["id"])["items"]
    letters = [i for i in items if i["document_type"] == "cover_letter"]
    assert [(i["id"], i["version_number"]) for i in letters] == [(letter["id"], 1)]
    assert [i["version_number"] for i in items if i["document_type"] == "tailored_cv"] == [2, 1]

    # ... and regenerating the letter leaves the CV alone.
    ok(generate(client, auth, ready["id"], LETTER))
    items = listing(client, auth, ready["id"])["items"]
    assert [i["version_number"] for i in items if i["document_type"] == "tailored_cv"] == [2, 1]
    assert [i["version_number"] for i in items if i["document_type"] == "cover_letter"] == [2, 1]


def test_documents_persist_and_are_listed_for_the_application_only(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    db_session: Session,
) -> None:
    other_job = make_job(db_session, user_by_email(db_session))
    other = started(client, auth, other_job, {"application_language": "en"})
    cv = ok(generate(client, auth, ready["id"], CV))
    assert listing(client, auth, other["id"])["items"] == []
    # a document is only reachable through its own application
    error(client.get(f"{documents_url(other['id'])}/{cv['id']}", headers=auth), 404, "NOT_FOUND")
    assert [i["id"] for i in listing(client, auth, ready["id"])["items"]] == [cv["id"]]


def test_a_document_becomes_outdated_when_its_inputs_change(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    ok(generate(client, auth, ready["id"], CV))
    ok(generate(client, auth, ready["id"], LETTER))

    def outdated() -> dict[str, bool]:
        return {
            i["document_type"]: i["is_outdated"]
            for i in listing(client, auth, ready["id"])["items"]
            if i["is_current"]
        }

    assert outdated() == {"tailored_cv": False, "cover_letter": False}
    # A letter depends on the application's availability; the CV does not.
    assert patch(client, auth, ready["id"], {"available_from": "2027-06-01"}).status_code == 200
    assert outdated() == {"tailored_cv": False, "cover_letter": True}
    # The language changes both.
    assert patch(client, auth, ready["id"], {"application_language": "en"}).status_code == 200
    assert outdated() == {"tailored_cv": True, "cover_letter": True}
    # Regenerating brings the document up to date.
    assert ok(generate(client, auth, ready["id"], CV))["is_outdated"] is False
    assert outdated()["tailored_cv"] is False


# --- Cover letter -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "salutation", "subject", "closing"),
    [
        (
            "de",
            "Sehr geehrte Damen und Herren,",
            "Bewerbung als Senior Project Manager",
            "Mit freundlichen Grüßen",
        ),
        (
            "en",
            "Dear Hiring Team,",
            "Application for the position of Senior Project Manager",
            "Kind regards",
        ),
    ],
)
def test_the_letter_follows_the_application_language(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    fake_ai: FakeDocumentAI,
    language: str,
    salutation: str,
    subject: str,
    closing: str,
) -> None:
    # The job is German-speaking in both cases: the language is the application's.
    job = make_job(db_session, user_by_email(db_session), title="Senior Project Manager")
    app = make_ready(client, auth, db_session, language=language, job=job)
    letter = ok(generate(client, auth, app["id"], LETTER))
    content = letter["content"]

    assert letter["language"] == language and content["language"] == language
    assert content["salutation"] == salutation
    assert content["subject"] == subject
    assert content["closing_formula"] == closing
    assert content["signature"] == "Alex Example"
    assert content["sender"]["email"] == "alex.example@example.com"
    assert content["recipient"] == {
        "company_name": "Example GmbH",
        "job_title": "Senior Project Manager",
    }
    roles = [p["role"] for p in content["paragraphs"]]
    assert roles[0] == "opening" and roles[-1] == "closing" and "experience" in roles
    assert fake_ai.contexts[-1]["output_language"]["code"] == language
    assert letter["prompt_version"] == "cover_letter_v1"


def test_the_language_is_never_inferred_from_the_job(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    # A German job posting, but no language chosen: JobPilot does not guess.
    job = make_job(
        db_session,
        user_by_email(db_session),
        title="Projektleiter (m/w/d)",
        location="Köln",
        description="Anforderungen\n• Erfahrung im Projektmanagement",
    )
    app = make_ready(client, auth, db_session, language=None, job=job)
    for kind in (CV, LETTER):
        body = error(generate(client, auth, app["id"], kind), 409, "LANGUAGE_MISSING")
        assert "never guesses" in body["message"]
    assert rows(db_session) == []


def test_salary_is_stated_only_when_the_user_chose_to_mention_it(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    letter = ok(generate(client, auth, ready["id"], LETTER))
    assert letter["content"]["salary_statement"] == (
        "Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr."
    )
    # the AI itself never saw the figure, and never writes it
    assert "78" not in json.dumps(fake_ai.contexts[-1])
    assert not any("78" in p["text"] for p in letter["content"]["paragraphs"])

    assert patch(client, auth, ready["id"], {"mention_salary_in_letter": False}).status_code == 200
    letter = ok(generate(client, auth, ready["id"], LETTER))
    assert letter["content"]["salary_statement"] is None
    assert "78" not in json.dumps(letter["content"])

    assert (
        patch(
            client,
            auth,
            ready["id"],
            {"application_language": "en", "mention_salary_in_letter": True},
        ).status_code
        == 200
    )
    letter = ok(generate(client, auth, ready["id"], LETTER))
    assert (
        letter["content"]["salary_statement"]
        == "My salary expectation is 78,000 EUR gross per year."
    )


def test_an_undecided_salary_mention_blocks_the_letter_but_not_the_cv(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeDocumentAI
) -> None:
    app = make_ready(
        client,
        auth,
        db_session,
        prep={"salary_expectation": "78000", "salary_currency": "EUR"},
    )
    assert app["mention_salary_in_letter"] is None
    state = listing(client, auth, app["id"])
    assert state["cover_letter"]["can_generate"] is False
    assert [b["code"] for b in state["cover_letter"]["blockers"]] == ["SALARY_DECISION_REQUIRED"]
    assert state["tailored_cv"]["can_generate"] is True

    body = error(generate(client, auth, app["id"], LETTER), 409, "SALARY_DECISION_REQUIRED")
    assert "Choose Yes or No" in body["message"]
    assert fake_ai.document_calls == 0 and rows(db_session) == []
    ok(generate(client, auth, app["id"], CV))  # the CV does not depend on it

    # Deciding unblocks it. Without any salary there is nothing to decide.
    assert patch(client, auth, app["id"], {"mention_salary_in_letter": False}).status_code == 200
    assert listing(client, auth, app["id"])["cover_letter"]["can_generate"] is True
    assert (
        patch(
            client,
            auth,
            app["id"],
            {"salary_expectation": None, "salary_currency": None, "mention_salary_in_letter": None},
        ).status_code
        == 200
    )
    assert listing(client, auth, app["id"])["cover_letter"]["can_generate"] is True
    assert ok(generate(client, auth, app["id"], LETTER))["content"]["salary_statement"] is None


def test_choosing_to_mention_a_missing_salary_blocks_the_letter(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    app = make_ready(client, auth, db_session, prep={"mention_salary_in_letter": True})
    error(generate(client, auth, app["id"], LETTER), 409, "SALARY_EXPECTATION_REQUIRED")


def test_the_ai_cannot_smuggle_a_salary_into_the_letter(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    patch(client, auth, ready["id"], {"mention_salary_in_letter": False})

    def tamper(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        output["paragraphs"][-1]["text"] += " Meine Gehaltsvorstellung liegt bei 95.000 EUR."
        return output

    fake_ai.letter_tamper = tamper
    body = error(generate(client, auth, ready["id"], LETTER), 502, "DOCUMENT_REJECTED")
    assert "SALARY_MENTION_NOT_ALLOWED" in {v["code"] for v in body["details"]["violations"]}


def test_unsupported_company_and_candidate_claims_in_a_letter_are_rejected(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeDocumentAI,
    db_session: Session,
) -> None:
    def tamper(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        output["paragraphs"][0]["text"] += (
            " Ihre Unternehmenskultur bewundere ich seit Jahren,"
            " und ich würde für die Stelle umziehen."
        )
        output["paragraphs"][1]["text"] += " Dabei nutzte ich AWS und Kubernetes."
        return output

    fake_ai.letter_tamper = tamper
    body = error(generate(client, auth, ready["id"], LETTER), 502, "DOCUMENT_REJECTED")
    codes = {v["code"] for v in body["details"]["violations"]}
    assert {"COMPANY_KNOWLEDGE_CLAIM", "PERSONAL_CIRCUMSTANCE_CLAIM"} <= codes
    assert codes & {"UNSUPPORTED_SKILL", "UNSUPPORTED_TECHNOLOGY"}
    assert [r.status for r in rows(db_session)] == [GeneratedDocumentStatus.REJECTED]


def test_the_letter_may_mention_availability_only_from_the_application(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    def tamper(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        output["paragraphs"][-1]["text"] = (
            "Ich stehe ab dem 1. Januar 2027 mit einer Kündigungsfrist von 3 Monaten zur Verfügung."
        )
        output["paragraphs"][-1]["source_fact_ids"] = ["app.available_from", "app.notice_period"]
        return output

    fake_ai.letter_tamper = tamper
    assert ok(generate(client, auth, ready["id"], LETTER))["status"] == "validated"

    def invented(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        output["paragraphs"][-1]["text"] = (
            "Ich stehe ab sofort mit einer Frist von 6 Wochen zur Verfügung."
        )
        output["paragraphs"][-1]["source_fact_ids"] = ["app.available_from"]
        return output

    fake_ai.letter_tamper = invented
    error(generate(client, auth, ready["id"], LETTER), 502, "DOCUMENT_REJECTED")


def test_the_match_is_guidance_for_the_letter_and_never_recalculated(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeDocumentAI,
    db_session: Session,
) -> None:
    ok(generate(client, auth, ready["id"], LETTER))
    assert "match_guidance" not in fake_ai.contexts[-1]  # nothing calculated yet

    assert client.post(f"{API}/jobs/{ready['job_id']}/match", headers=auth).status_code == 200
    from app.models import JobMatch

    matches = db_session.scalar(select(func.count()).select_from(JobMatch))
    ok(generate(client, auth, ready["id"], LETTER))
    guidance = fake_ai.contexts[-1].get("match_guidance")
    assert guidance is not None
    assert db_session.scalar(select(func.count()).select_from(JobMatch)) == matches
    ok(generate(client, auth, ready["id"], CV))
    assert "match_guidance" not in fake_ai.contexts[-1]  # the CV does not use it


def test_a_match_fact_cannot_support_a_claim_the_resume_does_not(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    """Whatever the match says, a skill in the letter must be in a cited resume fact."""
    assert client.post(f"{API}/jobs/{ready['job_id']}/match", headers=auth).status_code == 200

    def tamper(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        output["paragraphs"][1]["text"] += " Ich beherrsche Excel."
        return output

    fake_ai.letter_tamper = tamper
    body = error(generate(client, auth, ready["id"], LETTER), 502, "DOCUMENT_REJECTED")
    assert {v["code"] for v in body["details"]["violations"]} & {
        "UNSUPPORTED_SKILL",
        "UNSUPPORTED_TECHNOLOGY",
    }


# --- Provider neutrality and secrecy ----------------------------------------------------------


class OtherProvider(AIProvider):
    """Not Anthropic, not the fake: any provider behind the abstraction works."""

    name = "acme-ai"
    model = None

    def __init__(self) -> None:
        self.inner = FakeDocumentAI()

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
        return await self.inner.generate_structured(system=system, prompt=prompt, schema=schema)


def test_any_provider_behind_the_abstraction_works_and_is_recorded(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    db_session: Session,
) -> None:
    from app.ai.factory import get_ai_provider
    from app.main import app

    provider = OtherProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    document = ok(generate(client, auth, ready["id"], CV))
    assert document["ai_provider"] == "acme-ai" and document["ai_model"] is None
    assert provider.inner.document_calls == 1


def test_prompts_and_provider_details_are_never_exposed(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    cv = ok(generate(client, auth, ready["id"], CV))
    letter = ok(generate(client, auth, ready["id"], LETTER))
    responses = [
        json.dumps(cv),
        json.dumps(letter),
        json.dumps(listing(client, auth, ready["id"])),
        client.get(f"{documents_url(ready['id'])}/{cv['id']}", headers=auth).text,
    ]
    system_prompts = [
        call["system"] for call in fake_ai.calls if "tailor" in call["system"].lower()
    ]
    assert system_prompts
    for body in responses:
        for prompt in system_prompts:
            assert prompt[:60] not in body
        assert "<generation_context>" not in body and "api_key" not in body.lower()


def test_generation_logs_codes_never_resume_content(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeDocumentAI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    fake_ai.cv_error = AITimeoutError("timeout while sending Example Energy GmbH secrets")
    error(generate(client, auth, ready["id"], CV), 504, "AI_TIMEOUT")
    fake_ai.cv_error = None
    fake_ai.cv_tamper = lambda output, context: {
        **output,
        "summary": {"text": "Used Terraform at Initech GmbH.", "source_fact_ids": ["summary"]},
    }
    error(generate(client, auth, ready["id"], CV), 502, "DOCUMENT_REJECTED")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for private in ("Example Energy", "Initech", "alex.example", "meter-to-cash", "secrets"):
        assert private not in logged


# --- Failures ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exception", "status", "code", "message_part"),
    [
        (AITimeoutError("read timeout"), 504, "AI_TIMEOUT", "took too long"),
        (AIProviderUnavailableError("HTTP 503"), 503, "AI_UNAVAILABLE", "temporarily unavailable"),
        (AIRateLimitError("HTTP 429"), 429, "AI_RATE_LIMITED", "too many requests"),
        (
            AIAuthenticationError("bad key sk-ant-secret"),
            502,
            "AI_AUTH_FAILED",
            "Generation failed",
        ),
        (AIContextLimitError("prompt too long"), 502, "AI_CONTEXT_LIMIT", "too long"),
        (
            AIInvalidResponseError("1 validation error(s): summary:missing"),
            502,
            "AI_INVALID_RESPONSE",
            "couldn't use",
        ),
        (
            AINotConfiguredError("ANTHROPIC_API_KEY is not set"),
            503,
            "AI_NOT_CONFIGURED",
            "no AI provider",
        ),
        (
            AIError("something sk-ant-secret odd"),
            502,
            "AI_ERROR",
            "Generation failed. Please try again.",
        ),
    ],
)
def test_an_ai_failure_is_a_safe_error_and_never_a_document(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeDocumentAI,
    db_session: Session,
    exception: AIError,
    status: int,
    code: str,
    message_part: str,
) -> None:
    fake_ai.cv_error = exception
    body = error(generate(client, auth, ready["id"], CV), status, code)
    assert message_part in body["message"]
    assert "sk-ant" not in json.dumps(body) and "read timeout" not in json.dumps(body)

    (row,) = rows(db_session)
    assert row.status is GeneratedDocumentStatus.FAILED
    assert row.content is None and row.version_number is None and row.validation is None
    assert row.error_code == code and row.completed_at is not None
    assert body["details"]["document_id"] == str(row.id)

    # Not stuck "generating", and the user can simply try again.
    (item,) = listing(client, auth, ready["id"])["items"]
    assert item["status"] == "failed" and item["error_message"] == body["message"]
    fake_ai.cv_error = None
    assert ok(generate(client, auth, ready["id"], CV))["version_number"] == 1


def test_malformed_output_is_a_failure_and_stores_nothing(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    # missing "summary", wrong types: schema validation fails before truth validation
    fake_ai.cv_override = {"experience": "not a list", "skills": 3}
    body = error(generate(client, auth, ready["id"], CV), 502, "AI_INVALID_RESPONSE")
    assert "validation" not in body["details"]
    (item,) = listing(client, auth, ready["id"])["items"]
    assert item["status"] == "failed" and item["validation_status"] == "not_run"
    assert item["version_number"] is None


def test_a_letter_with_the_wrong_shape_is_a_failure(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    fake_ai.letter_override = {"language": "de", "paragraphs": [{"role": "poem", "text": "x"}]}
    error(generate(client, auth, ready["id"], LETTER), 502, "AI_INVALID_RESPONSE")


def test_an_unexpected_provider_exception_is_contained(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    fake_ai.cv_error = RuntimeError("boom: password=hunter2")
    body = error(generate(client, auth, ready["id"], CV), 500, "GENERATION_ERROR")
    assert "hunter2" not in json.dumps(body)
    assert listing(client, auth, ready["id"])["items"][0]["status"] == "failed"


def test_a_cancelled_request_does_not_leave_a_generation_running(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    class Cancelled(FakeDocumentAI):
        async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
            raise asyncio.CancelledError

    user = user_by_email(db_session)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            document_service.generate(
                db_session,
                user,
                uuid.UUID(ready["id"]),
                GeneratedDocumentType.TAILORED_CV,
                Cancelled(),
            )
        )
    (row,) = rows(db_session)
    assert row.status is GeneratedDocumentStatus.FAILED
    assert row.error_code == "GENERATION_CANCELLED"


def test_missing_prerequisites_are_reported_with_what_to_do(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeDocumentAI
) -> None:
    user = user_by_email(db_session)

    # no resume at all
    job = make_job(db_session, user)
    app = started(client, auth, job, {"application_language": "de"})
    assert app["resume"] is None
    state = listing(client, auth, app["id"])
    assert [b["code"] for b in state["tailored_cv"]["blockers"]] == ["RESUME_MISSING"]
    error(generate(client, auth, app["id"], CV), 409, "RESUME_MISSING")

    # a resume that has not been analyzed and confirmed
    resume = upload_resume(client, auth)
    assert patch(client, auth, app["id"], {"resume_id": resume["id"]}).status_code == 200
    body = error(generate(client, auth, app["id"], LETTER), 409, "RESUME_NOT_CONFIRMED")
    assert "confirm" in body["message"].lower()

    assert fake_ai.document_calls == 0 and rows(db_session) == []


def test_a_confirmed_resume_without_facts_cannot_produce_a_document(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    from app.models import ResumeProfile

    profile = db_session.scalars(select(ResumeProfile)).one()
    data = dict(profile.reviewed_data)
    for section in (
        "personal_information",
        "work_experience",
        "education",
        "skills",
        "languages",
        "certifications",
        "projects",
        "additional_information",
    ):
        data[section] = []
    data["summary"] = None
    profile.reviewed_data = data
    db_session.commit()
    error(generate(client, auth, ready["id"], CV), 409, "RESUME_FACTS_MISSING")


def test_a_missing_application_context_is_reported(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    db_session.execute(
        text("DELETE FROM application_context_snapshots WHERE application_id = :id"),
        {"id": ready["id"]},
    )
    db_session.commit()
    error(generate(client, auth, ready["id"], CV), 409, "APPLICATION_CONTEXT_MISSING")
    assert listing(client, auth, ready["id"])["items"] == []


def test_deleting_the_resume_keeps_documents_and_their_provenance(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    cv = ok(generate(client, auth, ready["id"], CV))
    assert client.delete(f"{API}/resumes/{ready['resume']['id']}", headers=auth).status_code in (
        200,
        204,
    )
    kept = ok(client.get(f"{documents_url(ready['id'])}/{cv['id']}", headers=auth), 200)
    assert kept["resume_id"] is None and kept["resume_version_number"] == 1
    assert kept["content"]["experience"][0]["company"] == "Example Energy GmbH"
    # without a resume nothing new can be generated
    state = listing(client, auth, ready["id"])
    assert [b["code"] for b in state["tailored_cv"]["blockers"]] == ["RESUME_MISSING"]
    assert kept["is_outdated"] is False


# --- In-flight generations --------------------------------------------------------------------


def _generating_row(db: Session, application: dict[str, Any], *, age_minutes: int = 0) -> uuid.UUID:
    snapshot = db.scalar(
        select(ApplicationContextSnapshot).where(
            ApplicationContextSnapshot.application_id == uuid.UUID(application["id"])
        )
    )
    row = GeneratedDocument(
        application_id=uuid.UUID(application["id"]),
        user_id=user_by_email(db).id,
        document_type=GeneratedDocumentType.TAILORED_CV,
        status=GeneratedDocumentStatus.GENERATING,
        language="de",
        application_context_snapshot_id=snapshot.id,
        input_hash="0" * 64,
        prompt_version="tailored_cv_v1",
        ai_provider="fake",
        source_facts={},
    )
    db.add(row)
    db.commit()
    if age_minutes:
        db.execute(
            text("UPDATE generated_documents SET created_at = now() - make_interval(mins => :m)"),
            {"m": age_minutes},
        )
        db.commit()
    return row.id


def test_only_one_generation_per_type_runs_at_a_time(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    _generating_row(db_session, ready)
    error(generate(client, auth, ready["id"], CV), 409, "GENERATION_IN_PROGRESS")
    assert listing(client, auth, ready["id"])["items"][0]["status"] == "generating"
    # the other type is independent
    assert ok(generate(client, auth, ready["id"], LETTER))["status"] == "validated"


def test_the_database_allows_one_in_flight_generation_per_application_and_type(
    ready: dict[str, Any], db_session: Session
) -> None:
    _generating_row(db_session, ready)
    with pytest.raises(IntegrityError, match="uq_generated_documents_one_in_flight"):
        _generating_row(db_session, ready)
    db_session.rollback()


def test_an_abandoned_generation_never_leaves_the_ui_stuck(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    stuck = _generating_row(db_session, ready, age_minutes=60)
    # shown as failed (not "generating") even before anything is retried
    (item,) = listing(client, auth, ready["id"])["items"]
    assert item["status"] == "failed" and item["error_code"] == "GENERATION_ABANDONED"
    assert (
        ok(client.get(f"{documents_url(ready['id'])}/{item['id']}", headers=auth), 200)["status"]
        == "failed"
    )
    # ... and it does not block a new generation, which marks it failed for good
    assert ok(generate(client, auth, ready["id"], CV))["version_number"] == 1
    db_session.expire_all()
    assert db_session.get(GeneratedDocument, stuck).status is GeneratedDocumentStatus.FAILED


# --- Database rules ---------------------------------------------------------------------------


def _insert(db: Session, application: dict[str, Any], **values: Any) -> None:
    snapshot = db.scalar(
        select(ApplicationContextSnapshot).where(
            ApplicationContextSnapshot.application_id == uuid.UUID(application["id"])
        )
    )
    base: dict[str, Any] = {
        "application_id": uuid.UUID(application["id"]),
        "user_id": user_by_email(db).id,
        "document_type": GeneratedDocumentType.TAILORED_CV,
        "status": GeneratedDocumentStatus.FAILED,
        "language": "de",
        "application_context_snapshot_id": snapshot.id,
        "input_hash": "0" * 64,
        "prompt_version": "tailored_cv_v1",
        "ai_provider": "fake",
        "source_facts": {},
    }
    base.update(values)
    db.add(GeneratedDocument(**base))
    db.flush()


_RAW_INSERT = (
    "INSERT INTO generated_documents (id, application_id, user_id, document_type, status, "
    "version_number, language, application_context_snapshot_id, input_hash, prompt_version, "
    "ai_provider, source_facts, content) "
    "SELECT gen_random_uuid(), a.id, a.user_id, 'TAILORED_CV', {status}, {version}, {language}, "
    "s.id, repeat('0', 64), 'tailored_cv_v1', 'fake', '{{}}'::jsonb, {content} "
    "FROM applications a JOIN application_context_snapshots s ON s.application_id = a.id"
)


@pytest.mark.parametrize(
    ("status", "version", "language", "content", "constraint"),
    [
        # only a validated attempt has a version and content ...
        ("'FAILED'", "1", "'de'", "NULL", "ck_generated_documents_version_iff_validated"),
        (
            "'REJECTED'",
            "NULL",
            "'de'",
            "'{}'::jsonb",
            "ck_generated_documents_content_iff_validated",
        ),
        # ... and a validated one must have both
        (
            "'VALIDATED'",
            "NULL",
            "'de'",
            "'{}'::jsonb",
            "ck_generated_documents_version_iff_validated",
        ),
        ("'VALIDATED'", "1", "'de'", "NULL", "ck_generated_documents_content_iff_validated"),
        ("'VALIDATED'", "0", "'de'", "'{}'::jsonb", "ck_generated_documents_version_number"),
        ("'FAILED'", "NULL", "'d1'", "NULL", "ck_generated_documents_language"),
    ],
)
def test_the_database_refuses_documents_that_were_not_validated_as_valid(
    ready: dict[str, Any],
    db_session: Session,
    status: str,
    version: str,
    language: str,
    content: str,
    constraint: str,
) -> None:
    sql = _RAW_INSERT.format(status=status, version=version, language=language, content=content)
    with pytest.raises(IntegrityError, match=constraint):
        db_session.execute(text(sql))
    db_session.rollback()


def test_version_numbers_are_unique_per_application_and_type(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    ok(generate(client, auth, ready["id"], CV))
    with pytest.raises(IntegrityError, match="uq_generated_documents_version"):
        db_session.execute(
            text(
                "INSERT INTO generated_documents (id, application_id, user_id, document_type, "
                "status, version_number, language, application_context_snapshot_id, input_hash, "
                "prompt_version, ai_provider, source_facts, content) "
                "SELECT gen_random_uuid(), application_id, user_id, document_type, status, "
                "version_number, language, application_context_snapshot_id, input_hash, "
                "prompt_version, ai_provider, source_facts, content FROM generated_documents"
            )
        )
    db_session.rollback()


def test_a_document_cannot_be_stored_for_another_users_application(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    ready: dict[str, Any],
    db_session: Session,
) -> None:
    mallory = user_by_email(db_session, "mallory@example.com")
    with pytest.raises(IntegrityError, match="fk_generated_documents_application"):
        _insert(db_session, ready, user_id=mallory.id)
    db_session.rollback()


def test_documents_go_with_their_application_user_and_snapshot(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    ok(generate(client, auth, ready["id"], CV))
    assert len(rows(db_session)) == 1
    db_session.delete(db_session.get(User, user_by_email(db_session).id))
    db_session.commit()
    assert rows(db_session) == []


# --- Security ---------------------------------------------------------------------------------


def test_every_document_endpoint_requires_authentication(
    client: TestClient, ready: dict[str, Any]
) -> None:
    base = documents_url(ready["id"])
    random_id = uuid.uuid4()
    for method, url in (
        ("get", base),
        ("get", f"{base}/{random_id}"),
        ("post", f"{base}/cv"),
        ("post", f"{base}/cover-letter"),
    ):
        assert getattr(client, method)(url).status_code == 401


def test_another_users_application_and_documents_are_not_found(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeDocumentAI,
    db_session: Session,
) -> None:
    cv = ok(generate(client, auth, ready["id"], CV))
    calls = fake_ai.document_calls
    base = documents_url(ready["id"])
    unknown = documents_url(str(uuid.uuid4()))

    attempts = [
        client.get(base, headers=other_auth),
        client.get(f"{base}/{cv['id']}", headers=other_auth),
        client.post(f"{base}/cv", headers=other_auth),
        client.post(f"{base}/cover-letter", headers=other_auth),
    ]
    unknowns = [
        client.get(unknown, headers=auth),
        client.get(f"{unknown}/{cv['id']}", headers=auth),
        client.post(f"{unknown}/cv", headers=auth),
        client.post(f"{unknown}/cover-letter", headers=auth),
    ]
    for response in [*attempts, *unknowns]:
        assert response.status_code == 404, response.text
        assert response.json()["error"]["message"] == "Application not found."
    # nothing was generated, and nothing about the document leaks
    assert fake_ai.document_calls == calls
    assert all(cv["id"] not in r.text for r in attempts)
    assert len(rows(db_session)) == 1


def test_another_users_document_id_under_my_own_application_is_not_found(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    ready: dict[str, Any],
    db_session: Session,
) -> None:
    cv = ok(generate(client, auth, ready["id"], CV))
    mallory = user_by_email(db_session, "mallory@example.com")
    mallory_app = make_ready(client, other_auth, db_session, email="mallory@example.com", prep={})
    mine = client.get(f"{documents_url(mallory_app['id'])}/{cv['id']}", headers=other_auth)
    assert mine.status_code == 404 and mine.json()["error"]["message"] == "Document not found."
    assert listing(client, other_auth, mallory_app["id"])["items"] == []
    assert mallory.id != user_by_email(db_session).id


def test_a_resume_reference_to_someone_elses_resume_is_never_followed(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    ready: dict[str, Any],
    db_session: Session,
    fake_ai: FakeDocumentAI,
) -> None:
    """V0.7 refuses to create such a reference; if a bad row ever existed,
    generation must still not read another user's resume."""
    changed = expected_parsed_resume()
    changed["work_experience"][0]["company"] = "Mallory Secret Employer"
    fake_ai.response = changed
    mallory_resume = confirmed_resume(client, other_auth, "mallory.pdf")
    db_session.execute(
        text("UPDATE applications SET resume_id = :resume WHERE id = :id"),
        {"resume": mallory_resume["id"], "id": ready["id"]},
    )
    db_session.commit()
    error(generate(client, auth, ready["id"], CV), 409, "RESUME_MISSING")
    assert fake_ai.document_calls == 0
    assert "Mallory" not in json.dumps(listing(client, auth, ready["id"]))


def test_generated_content_never_reaches_another_user(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    ready: dict[str, Any],
    db_session: Session,
) -> None:
    ok(generate(client, auth, ready["id"], CV))
    ok(generate(client, auth, ready["id"], LETTER))
    mallory_app = make_ready(client, other_auth, db_session, email="mallory@example.com")
    ok(generate(client, other_auth, mallory_app["id"], CV))
    mine = json.dumps(listing(client, auth, ready["id"]))
    theirs = json.dumps(listing(client, other_auth, mallory_app["id"]))
    my_ids = {i["id"] for i in listing(client, auth, ready["id"])["items"]}
    their_ids = {i["id"] for i in listing(client, other_auth, mallory_app["id"])["items"]}
    assert my_ids.isdisjoint(their_ids) and len(my_ids) == 2 and len(their_ids) == 1
    assert not any(i in theirs for i in my_ids) and not any(i in mine for i in their_ids)


def test_generation_requests_take_no_body_so_nothing_can_be_smuggled_in(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeDocumentAI
) -> None:
    response = client.post(
        f"{documents_url(ready['id'])}/cv",
        headers=auth,
        json={"language": "en", "salary_expectation": 1, "facts": ["I invented Kubernetes"]},
    )
    document = ok(response)
    assert document["language"] == "de"
    assert "Kubernetes" not in json.dumps(fake_ai.contexts[-1])


def test_schema_of_a_letter_output_matches_the_provider_contract() -> None:
    """The fake validates against the same models a real provider's output is."""
    parsed = AICoverLetter.model_validate(
        faithful_letter(
            {
                "output_language": {"code": "en"},
                "candidate_facts": {
                    "experience": [
                        {
                            "id": "exp1",
                            "facts": [{"id": "exp1.r1", "kind": "responsibility", "text": "x"}],
                        }
                    ],
                    "skills": [],
                },
                "target_job": {"title": "T", "company": "C", "responsibilities": []},
            }
        )
    )
    assert parsed.paragraphs[0].role == "opening"
