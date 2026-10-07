"""V0.9 on documents: Humanize, the user's edits, regeneration safety, and the
CV photo -- through the real API. The AI is a deterministic fake that rewords
the fake letter faithfully, or (on purpose) unfaithfully."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.provider import AIRateLimitError
from app.models import (
    ApplicationPhoto,
    GeneratedDocument,
    GeneratedDocumentOrigin,
    GeneratedDocumentStatus,
    GeneratedDocumentType,
)
from app.models.mixins import ImmutableRecordError
from app.services.storage.base import FileStorage
from tests.application_helpers import API
from tests.document_helpers import documents_url, generate, listing
from tests.humanize_helpers import (
    FakeHumanizeAI,
    cv_edit,
    edit,
    faithful_humanized,
    humanize,
    letter_edit,
)
from tests.question_helpers import error, make_ready, ok

CV = "cv"
LETTER = "cover-letter"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture
def fake_ai() -> FakeHumanizeAI:
    return FakeHumanizeAI()


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


def letter_v1(client: TestClient, auth: dict[str, str], app: dict[str, Any]) -> dict[str, Any]:
    return ok(generate(client, auth, app["id"], LETTER), 201)


def cv_v1(client: TestClient, auth: dict[str, str], app: dict[str, Any]) -> dict[str, Any]:
    return ok(generate(client, auth, app["id"], CV), 201)


def fetch(
    client: TestClient, auth: dict[str, str], app: dict[str, Any], document: dict[str, Any]
) -> dict[str, Any]:
    return ok(client.get(f"{documents_url(app['id'])}/{document['id']}", headers=auth))


def rows(db: Session) -> list[GeneratedDocument]:
    db.expire_all()
    return list(db.scalars(select(GeneratedDocument).order_by(GeneratedDocument.created_at)).all())


# --- Humanize ---------------------------------------------------------------------------------


def test_humanize_creates_a_new_version_and_leaves_the_original_untouched(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    v1 = letter_v1(client, auth, ready)
    before = fetch(client, auth, ready, v1)
    v2 = ok(humanize(client, auth, ready["id"]), 201)

    assert v2["version_number"] == 2 and v2["is_current"] is True
    assert v2["origin"] == "humanized" and v2["base_document_id"] == v1["id"]
    assert v2["document_type"] == "cover_letter" and v2["status"] == "validated"
    assert v2["validation_status"] == "passed"
    # the original is exactly as it was, and no longer current
    after = fetch(client, auth, ready, v1)
    assert after["content"] == before["content"] == v1["content"]
    assert after["is_current"] is False and after["version_number"] == 1
    assert after["origin"] == "ai" and after["base_document_id"] is None
    # both are in the history
    items = listing(client, auth, ready["id"])["items"]
    assert [(i["version_number"], i["origin"]) for i in items] == [(2, "humanized"), (1, "ai")]


def test_humanized_text_differs_but_everything_else_is_carried_over(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    v1 = letter_v1(client, auth, ready)
    v2 = ok(humanize(client, auth, ready["id"]), 201)
    old, new = v1["content"], v2["content"]
    assert [p["text"] for p in new["paragraphs"]] != [p["text"] for p in old["paragraphs"]]
    assert [p["role"] for p in new["paragraphs"]] == [p["role"] for p in old["paragraphs"]]
    for before, after in zip(old["paragraphs"], new["paragraphs"], strict=True):
        assert after["source_fact_ids"] == before["source_fact_ids"]
        assert after["job_fact_ids"] == before["job_fact_ids"]
    for key in (
        "sender",
        "recipient",
        "subject",
        "salutation",
        "closing_formula",
        "signature",
        "language",
    ):
        assert new[key] == old[key]
    # the salary sentence is the server's, unchanged
    assert (
        new["salary_statement"]
        == old["salary_statement"]
        == ("Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr.")
    )
    assert new["recipient"] == {
        "company_name": "Example GmbH",
        "job_title": "Senior Project Manager",
    }
    assert v2["language"] == v1["language"] == "de"
    # facts the original cited remain resolvable
    assert v2["source_facts"] == v1["source_facts"]


def test_humanize_provenance_and_prompt(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    v1 = letter_v1(client, auth, ready)
    v2 = ok(humanize(client, auth, ready["id"]), 201)
    prompt_file = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "ai"
        / "prompts"
        / "cover_letter_humanize_v1.txt"
    )
    assert v2["prompt_version"] == "cover_letter_humanize_v1"
    assert v2["prompt_hash"] == hashlib.sha256(prompt_file.read_bytes()).hexdigest()
    assert (v2["ai_provider"], v2["ai_model"]) == ("fake", "fake-model-1")
    assert v2["resume_version_number"] == v1["resume_version_number"] == 1
    assert v2["application_context_snapshot_id"] == v1["application_context_snapshot_id"]
    assert v2["input_hash"] == v1["input_hash"]
    assert v2["is_outdated"] is False
    # V0.8 documents now record their prompt hash too
    assert v1["prompt_hash"] and len(v1["prompt_hash"]) == 64
    assert v1["prompt_version"] == "cover_letter_v1"


def test_the_ai_sees_the_letter_and_its_facts_only(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    letter_v1(client, auth, ready)
    ok(humanize(client, auth, ready["id"]), 201)
    shown = fake_ai.humanize_prompts[0]
    for secret in (
        "78000",
        "78.000",
        "alex.example@example.com",
        "+49 170",
        "Jane",
        "Gehaltsvorstellung",
    ):
        assert secret not in shown
    context = fake_ai.humanize_contexts[0]
    assert context["language"] == "de"
    assert context["target_job"] == {"title": "Senior Project Manager", "company": "Example GmbH"}
    assert [p["role"] for p in context["paragraphs"]][0] == "opening"
    cited = {i for p in context["paragraphs"] for i in p["source_fact_ids"]}
    assert {f["id"] for f in context["facts"]} == cited


def test_humanize_needs_a_letter(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    error(humanize(client, auth, ready["id"]), 409, "NO_COVER_LETTER")
    cv_v1(client, auth, ready)  # a CV is not a letter
    error(humanize(client, auth, ready["id"]), 409, "NO_COVER_LETTER")
    assert fake_ai.humanize_calls == 0


def test_humanizing_twice_keeps_every_version(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    v1 = letter_v1(client, auth, ready)
    v2 = ok(humanize(client, auth, ready["id"]), 201)
    # humanize the *original* again, explicitly
    v3 = ok(humanize(client, auth, ready["id"], base_document_id=v1["id"]), 201)
    assert (v3["version_number"], v3["base_document_id"]) == (3, v1["id"])
    versions = {
        i["version_number"]: i["is_current"] for i in listing(client, auth, ready["id"])["items"]
    }
    assert versions == {1: False, 2: False, 3: True}
    assert fetch(client, auth, ready, v2)["content"] == v2["content"]


def test_regenerating_the_letter_keeps_the_humanized_version(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    letter_v1(client, auth, ready)
    v2 = ok(humanize(client, auth, ready["id"]), 201)
    v3 = ok(generate(client, auth, ready["id"], LETTER), 201)
    assert v3["version_number"] == 3 and v3["origin"] == "ai"
    assert fetch(client, auth, ready, v2)["content"] == v2["content"]
    assert len(listing(client, auth, ready["id"])["items"]) == 3


# Truth validation: Humanize is not a bypass ---------------------------------------------------


def tamper_text(change: Any) -> Any:
    def apply(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        paragraphs = output["paragraphs"]
        paragraphs[1]["text"] = change(paragraphs[1]["text"])
        return output

    return apply


MUTATIONS: dict[str, tuple[Any, str]] = {
    "invented number": (
        tamper_text(lambda t: t + " Ich habe 12 Jahre Erfahrung."),
        "UNSUPPORTED_NUMBER",
    ),
    "invented tool": (
        tamper_text(lambda t: t + " Außerdem arbeite ich mit Kubernetes."),
        "UNSUPPORTED_SKILL",
    ),
    "invented employer": (
        tamper_text(lambda t: t + " Zuvor war ich bei Globex Inc. tätig."),
        "UNSUPPORTED_ORGANIZATION",
    ),
    "invented leadership": (
        tamper_text(lambda t: t + " Ich habe das Team geleitet."),
        "UNSUPPORTED_ROLE_CLAIM",
    ),
    "invented credential": (
        tamper_text(lambda t: t + " Ich besitze eine Zertifizierung."),
        "UNSUPPORTED_CREDENTIAL",
    ),
    "invented achievement": (
        tamper_text(lambda t: t + " Dafür erhielt ich einen Preis."),
        "UNSUPPORTED_ACHIEVEMENT",
    ),
    "invented motivation about the company": (
        tamper_text(lambda t: t + " Ich bewundere Ihre innovative Unternehmenskultur."),
        "COMPANY_KNOWLEDGE_CLAIM",
    ),
    "salary talk": (
        tamper_text(lambda t: t + " Mein Gehalt soll hoch sein."),
        "SALARY_MENTION_NOT_ALLOWED",
    ),
    "dropped skill": (
        tamper_text(lambda t: t.replace("SAP IS-U", "diese Software")),
        "FACT_DROPPED",
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_every_kind_of_unsupported_change_is_rejected_and_the_letter_is_untouched(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeHumanizeAI,
    db_session: Session,
    name: str,
) -> None:
    tamper, code = MUTATIONS[name]
    v1 = letter_v1(client, auth, ready)
    fake_ai.humanize_tamper = tamper
    body = error(humanize(client, auth, ready["id"]), 502, "DOCUMENT_REJECTED")
    assert code in {v["code"] for v in body["details"]["violations"]}, body["details"]
    # nothing was saved as a document; the letter is as it was and still current
    items = listing(client, auth, ready["id"])["items"]
    assert [(i["status"], i["version_number"], i["is_current"]) for i in items] == [
        ("rejected", None, False),
        ("validated", 1, True),
    ]
    assert fetch(client, auth, ready, v1)["content"] == v1["content"]
    rejected = next(r for r in rows(db_session) if r.status is GeneratedDocumentStatus.REJECTED)
    assert rejected.content is None and rejected.version_number is None
    assert rejected.origin is GeneratedDocumentOrigin.HUMANIZED and rejected.validation
    # and the user can simply try again
    fake_ai.humanize_tamper = None
    assert ok(humanize(client, auth, ready["id"]), 201)["version_number"] == 2


def test_dropping_a_number_of_the_original_is_rejected(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    v1 = letter_v1(client, auth, ready)
    body = letter_edit(v1["content"])
    body["paragraphs"][1]["text"] = "Ich schreibe seit 2019 Berichte für Kunden."
    ok(edit(client, auth, ready["id"], v1["id"], body), 201)
    fake_ai.humanize_tamper = lambda output, context: {
        **output,
        "paragraphs": [
            {**p, "text": "Ich schreibe seit Jahren Berichte für Kunden." if i == 1 else p["text"]}
            for i, p in enumerate(output["paragraphs"])
        ],
    }
    response = humanize(client, auth, ready["id"], acknowledge_edited_current=True)
    body = error(response, 502, "DOCUMENT_REJECTED")
    assert "FACT_DROPPED" in {v["code"] for v in body["details"]["violations"]}


def structural(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    output["paragraphs"].reverse()
    return output


def foreign_fact(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    output["paragraphs"][0]["source_fact_ids"] = ["edu1"]
    return output


def no_sources(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    for paragraph in output["paragraphs"]:
        paragraph["source_fact_ids"] = []
    return output


def wrong_language(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    output["language"] = "en"
    return output


def unchanged(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "language": context["language"],
        "paragraphs": [
            {"role": p["role"], "text": p["text"], "source_fact_ids": p["source_fact_ids"]}
            for p in context["paragraphs"]
        ],
    }


def dropped_identity(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    output["paragraphs"][0]["text"] = "gerne bewerbe ich mich auf diese Position."
    return output


def merged(output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    output["paragraphs"] = output["paragraphs"][:-1]
    return output


@pytest.mark.parametrize(
    ("tamper", "code"),
    [
        (structural, "INVALID_STRUCTURE"),
        (merged, "INVALID_STRUCTURE"),
        (foreign_fact, "FOREIGN_FACT"),
        (no_sources, "MISSING_SOURCE"),
        (wrong_language, "WRONG_LANGUAGE"),
        (unchanged, "UNCHANGED"),
        (dropped_identity, "IDENTITY_NOT_PRESERVED"),
    ],
)
def test_the_rewording_rules(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeHumanizeAI,
    tamper: Any,
    code: str,
) -> None:
    letter_v1(client, auth, ready)
    fake_ai.humanize_tamper = tamper
    body = error(humanize(client, auth, ready["id"]), 502, "DOCUMENT_REJECTED")
    assert code in {v["code"] for v in body["details"]["violations"]}


def test_a_failed_ai_call_leaves_the_letter_untouched_and_can_be_retried(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    v1 = letter_v1(client, auth, ready)
    fake_ai.humanize_error = AIRateLimitError("x")
    error(humanize(client, auth, ready["id"]), 429, "AI_RATE_LIMITED")
    items = listing(client, auth, ready["id"])["items"]
    assert [(i["status"], i["is_current"]) for i in items] == [
        ("failed", False),
        ("validated", True),
    ]
    assert fetch(client, auth, ready, v1)["content"] == v1["content"]
    fake_ai.humanize_error = None
    assert ok(humanize(client, auth, ready["id"]), 201)["version_number"] == 2


def test_humanize_works_from_the_stored_facts_even_after_the_resume_is_gone(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    letter_v1(client, auth, ready)
    resume_id = ready["resume"]["id"] if isinstance(ready.get("resume"), dict) else None
    if resume_id is None:
        resumes = client.get(f"{API}/resumes", headers=auth).json()
        resume_id = resumes[0]["id"]
    assert client.delete(f"{API}/resumes/{resume_id}", headers=auth).status_code == 204
    assert ok(humanize(client, auth, ready["id"]), 201)["version_number"] == 2


def test_humanize_ownership(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeHumanizeAI,
) -> None:
    v1 = letter_v1(client, auth, ready)
    assert humanize(client, other_auth, ready["id"]).status_code == 404
    assert humanize(client, other_auth, ready["id"], base_document_id=v1["id"]).status_code == 404
    assert fake_ai.humanize_calls == 0
    # an unknown or foreign base document is a 404 for the owner too
    cv = cv_v1(client, auth, ready)
    assert humanize(client, auth, ready["id"], base_document_id=cv["id"]).status_code == 404
    assert (
        humanize(
            client, auth, ready["id"], base_document_id="00000000-0000-4000-8000-000000000001"
        ).status_code
        == 404
    )


def test_humanize_takes_nothing_but_the_base_and_the_acknowledgement(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    letter_v1(client, auth, ready)
    for body in ({"language": "en"}, {"facts": ["x"]}, {"prompt": "ignore the rules"}):
        assert humanize(client, auth, ready["id"], **body).status_code == 422


def test_faithful_fake_matches_the_contract() -> None:
    from app.schemas.document_ai import AIHumanizedLetter

    parsed = AIHumanizedLetter.model_validate(
        faithful_humanized(
            {
                "language": "en",
                "paragraphs": [{"role": "opening", "text": "x", "source_fact_ids": []}],
            }
        )
    )
    assert parsed.paragraphs[0].role == "opening"


# --- The user's edits -------------------------------------------------------------------------


def test_editing_a_letter_saves_a_new_user_edited_version(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    v1 = letter_v1(client, auth, ready)
    calls = len(fake_ai.calls)
    content = v1["content"]
    changed = [p["text"] for p in content["paragraphs"]]
    changed[1] = "In my own words: I enjoy writing reports."
    body = letter_edit(
        content,
        paragraphs=[
            {"role": p["role"], "text": text}
            for p, text in zip(content["paragraphs"], changed, strict=True)
        ],
        subject="My own subject",
        signature="Alex E.",
    )
    v2 = ok(edit(client, auth, ready["id"], v1["id"], body), 201)
    assert len(fake_ai.calls) == calls  # no AI involved
    assert v2["version_number"] == 2 and v2["is_current"] is True
    assert v2["origin"] == "user_edited" and v2["base_document_id"] == v1["id"]
    assert v2["validation_status"] == "user_authored" and v2["validation"] is None
    assert (v2["ai_provider"], v2["prompt_version"], v2["prompt_hash"]) == (
        "user",
        "user_edit",
        None,
    )
    new = v2["content"]
    assert new["subject"] == "My own subject" and new["signature"] == "Alex E."
    assert new["paragraphs"][1] == {
        "role": content["paragraphs"][1]["role"],
        "text": changed[1],
        "source_fact_ids": [],
        "job_fact_ids": [],
        "origin": "user",
    }
    # untouched paragraphs keep their sources and stay the AI's
    assert new["paragraphs"][0] == content["paragraphs"][0]
    assert new["sender"] == content["sender"] and new["recipient"] == content["recipient"]
    # the facts it still cites resolve (the read model returns the cited ones)
    assert set(v2["source_facts"]) <= set(v1["source_facts"])
    assert {i for p in new["paragraphs"] for i in p["source_fact_ids"]} <= set(v2["source_facts"])
    # the original is exactly as it was
    assert fetch(client, auth, ready, v1)["content"] == content


def test_the_edited_text_is_stored_exactly_as_written(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    v1 = letter_v1(client, auth, ready)
    text = "Erster Satz mit  Umlauten: äöü ß.\n\nZweiter Absatz — mit Gedankenstrich."
    body = letter_edit(v1["content"])
    body["paragraphs"][1]["text"] = text
    v2 = ok(edit(client, auth, ready["id"], v1["id"], body), 201)
    assert v2["content"]["paragraphs"][1]["text"] == text


def test_the_user_can_drop_the_salary_sentence_and_add_paragraphs(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    v1 = letter_v1(client, auth, ready)
    body = letter_edit(v1["content"], salary_statement=None)
    body["paragraphs"].append({"role": "closing", "text": "P.S. Gern auch telefonisch."})
    v2 = ok(edit(client, auth, ready["id"], v1["id"], body), 201)
    assert v2["content"]["salary_statement"] is None
    assert len(v2["content"]["paragraphs"]) == len(v1["content"]["paragraphs"]) + 1


def test_editing_a_cv_changes_text_only(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    v1 = cv_v1(client, auth, ready)
    content = v1["content"]
    first = content["experience"][0]
    body = cv_edit(
        content,
        summary="A summary I wrote myself.",
        header={**cv_edit(content)["header"], "phone": "+49 1", "links": []},
        experience=[
            {
                "source_fact_id": first["source_fact_id"],
                "bullets": [first["bullets"][0]["text"], "A bullet of my own."],
            }
        ],
    )
    v2 = ok(edit(client, auth, ready["id"], v1["id"], body), 201)
    new = v2["content"]
    assert v2["origin"] == "user_edited" and v2["document_type"] == "tailored_cv"
    assert new["summary"] == {
        "text": "A summary I wrote myself.",
        "source_fact_ids": [],
        "origin": "user",
    }
    assert new["header"]["phone"] == "+49 1" and new["header"]["links"] == []
    bullets = new["experience"][0]["bullets"]
    assert bullets[0] == first["bullets"][0]  # kept, with its sources
    assert bullets[1] == {"text": "A bullet of my own.", "source_fact_ids": [], "origin": "user"}
    # employer, title, dates, skills ... are the resume's: untouched
    for key in ("company", "job_title", "start_date", "end_date"):
        assert new["experience"][0][key] == first[key]
    assert new["experience"][1] == content["experience"][1]
    assert new["skills"] == content["skills"] and new["education"] == content["education"]
    assert fetch(client, auth, ready, v1)["content"] == content


def test_an_edit_that_changes_nothing_is_not_saved(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    v1 = letter_v1(client, auth, ready)
    error(edit(client, auth, ready["id"], v1["id"], letter_edit(v1["content"])), 409, "NO_CHANGES")
    cv = cv_v1(client, auth, ready)
    error(edit(client, auth, ready["id"], cv["id"], cv_edit(cv["content"])), 409, "NO_CHANGES")
    assert len(listing(client, auth, ready["id"])["items"]) == 2


def test_invalid_edits_are_refused(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    letter = letter_v1(client, auth, ready)
    cv = cv_v1(client, auth, ready)
    base = letter_edit(letter["content"])
    for bad in (
        {**base, "paragraphs": []},
        {**base, "subject": "   "},
        {**base, "paragraphs": [{"role": "opening", "text": ""}]},
        {**base, "paragraphs": [{"role": "bogus", "text": "x"}]},
        {**base, "user_id": "x"},
        {**base, "kind": "unknown"},
    ):
        assert edit(client, auth, ready["id"], letter["id"], bad).status_code == 422, bad
    # the wrong kind for the document
    error(edit(client, auth, ready["id"], cv["id"], base), 422, "DOCUMENT_TYPE_MISMATCH")
    # a role that is not in the CV
    bad_cv = cv_edit(cv["content"], experience=[{"source_fact_id": "exp99", "bullets": ["x"]}])
    error(edit(client, auth, ready["id"], cv["id"], bad_cv), 422, "UNKNOWN_EXPERIENCE")
    twice = cv_edit(
        cv["content"],
        experience=[
            {"source_fact_id": "exp1", "bullets": ["x"]},
            {"source_fact_id": "exp1", "bullets": ["y"]},
        ],
    )
    assert edit(client, auth, ready["id"], cv["id"], twice).status_code == 422


def test_only_validated_versions_of_your_own_application_can_be_edited(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeHumanizeAI,
) -> None:
    v1 = letter_v1(client, auth, ready)
    body = letter_edit(v1["content"], subject="Mine")
    assert edit(client, other_auth, ready["id"], v1["id"], body).status_code == 404
    fake_ai.humanize_tamper = MUTATIONS["invented number"][0]
    humanize(client, auth, ready["id"])
    rejected = next(
        i for i in listing(client, auth, ready["id"])["items"] if i["status"] == "rejected"
    )
    assert edit(client, auth, ready["id"], rejected["id"], body).status_code == 404


def test_editing_an_older_version_makes_a_new_current_version(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    v1 = letter_v1(client, auth, ready)
    ok(humanize(client, auth, ready["id"]), 201)
    v3 = ok(
        edit(client, auth, ready["id"], v1["id"], letter_edit(v1["content"], subject="Mine")), 201
    )
    assert (v3["version_number"], v3["base_document_id"], v3["is_current"]) == (3, v1["id"], True)


def test_an_edited_version_is_write_once(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    v1 = letter_v1(client, auth, ready)
    ok(edit(client, auth, ready["id"], v1["id"], letter_edit(v1["content"], subject="Mine")), 201)
    edited = next(r for r in rows(db_session) if r.origin is GeneratedDocumentOrigin.USER_EDITED)
    edited.content = {**edited.content, "subject": "tampered"}  # type: ignore[dict-item]
    with pytest.raises(ImmutableRecordError):
        db_session.commit()
    db_session.rollback()


def test_an_edited_version_is_outdated_exactly_when_its_base_is(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    v1 = letter_v1(client, auth, ready)
    v2 = ok(
        edit(client, auth, ready["id"], v1["id"], letter_edit(v1["content"], subject="Mine")), 201
    )
    assert v2["is_outdated"] is False
    assert (
        client.patch(
            f"{API}/applications/{ready['id']}", headers=auth, json={"available_from": "2028-02-02"}
        ).status_code
        == 200
    )
    assert fetch(client, auth, ready, v2)["is_outdated"] is True


# --- Regeneration never silently replaces the user's edits ------------------------------------


@pytest.mark.parametrize("kind", [CV, LETTER])
def test_regenerating_over_an_edited_current_version_needs_confirmation(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    fake_ai: FakeHumanizeAI,
    kind: str,
) -> None:
    v1 = cv_v1(client, auth, ready) if kind == CV else letter_v1(client, auth, ready)
    body = (
        cv_edit(v1["content"], summary="Mine.")
        if kind == CV
        else letter_edit(v1["content"], subject="Mine")
    )
    edited = ok(edit(client, auth, ready["id"], v1["id"], body), 201)
    calls = len(fake_ai.calls)

    refused = error(
        generate(client, auth, ready["id"], kind), 409, "EDITED_DOCUMENT_CONFIRMATION_REQUIRED"
    )
    assert refused["details"] == {"current_version": 2}
    assert len(fake_ai.calls) == calls  # no AI call, nothing generated
    assert len(listing(client, auth, ready["id"])["items"]) == 2

    confirmed = client.post(
        f"{documents_url(ready['id'])}/{kind}",
        headers=auth,
        json={"acknowledge_edited_current": True},
    )
    v3 = ok(confirmed, 201)
    assert v3["version_number"] == 3 and v3["origin"] == "ai"
    # the user's edited version is untouched and still readable
    kept = fetch(client, auth, ready, edited)
    assert kept["content"] == edited["content"] and kept["origin"] == "user_edited"


def test_a_failed_regeneration_never_loses_the_edited_version(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    v1 = letter_v1(client, auth, ready)
    edited = ok(
        edit(client, auth, ready["id"], v1["id"], letter_edit(v1["content"], subject="Mine")), 201
    )
    fake_ai.letter_error = AIRateLimitError("x")
    response = client.post(
        f"{documents_url(ready['id'])}/{LETTER}",
        headers=auth,
        json={"acknowledge_edited_current": True},
    )
    error(response, 429, "AI_RATE_LIMITED")
    items = listing(client, auth, ready["id"])["items"]
    current = next(i for i in items if i["is_current"])
    assert current["id"] == edited["id"] and current["origin"] == "user_edited"


def test_humanizing_an_edited_letter_needs_confirmation_and_keeps_the_users_words(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    v1 = letter_v1(client, auth, ready)
    body = letter_edit(v1["content"])
    body["paragraphs"][1]["text"] = "Ich schreibe gerne Berichte und mag Wanderungen."
    edited = ok(edit(client, auth, ready["id"], v1["id"], body), 201)
    error(humanize(client, auth, ready["id"]), 409, "EDITED_DOCUMENT_CONFIRMATION_REQUIRED")
    assert fake_ai.humanize_calls == 0

    fake_ai.humanize_tamper = None
    v3 = ok(humanize(client, auth, ready["id"], acknowledge_edited_current=True), 201)
    assert (v3["version_number"], v3["origin"], v3["base_document_id"]) == (
        3,
        "humanized",
        edited["id"],
    )
    # the AI worked from the user's words, and the user's version is untouched
    assert fake_ai.humanize_contexts[0]["paragraphs"][1]["text"] == body["paragraphs"][1]["text"]
    assert fetch(client, auth, ready, edited)["content"] == edited["content"]


def test_humanize_may_not_add_to_the_users_own_words_either(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    v1 = letter_v1(client, auth, ready)
    body = letter_edit(v1["content"])
    body["paragraphs"][1]["text"] = "Ich schreibe gerne Berichte."
    ok(edit(client, auth, ready["id"], v1["id"], body), 201)
    fake_ai.humanize_tamper = MUTATIONS["invented tool"][0]
    error(
        humanize(client, auth, ready["id"], acknowledge_edited_current=True),
        502,
        "DOCUMENT_REJECTED",
    )


# --- The database -----------------------------------------------------------------------------


def test_the_database_ties_derived_versions_to_their_base(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    letter_v1(client, auth, ready)
    base = rows(db_session)[0]
    assert base.origin is GeneratedDocumentOrigin.AI and base.base_document_id is None
    clone = GeneratedDocument(
        application_id=base.application_id,
        user_id=base.user_id,
        document_type=GeneratedDocumentType.COVER_LETTER,
        status=GeneratedDocumentStatus.FAILED,
        origin=GeneratedDocumentOrigin.HUMANIZED,  # humanized, but no base
        language="de",
        application_context_snapshot_id=base.application_context_snapshot_id,
        input_hash=base.input_hash,
        prompt_version="x",
        ai_provider="x",
    )
    db_session.add(clone)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_v08_documents_keep_working_with_the_new_columns(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], db_session: Session
) -> None:
    v1 = cv_v1(client, auth, ready)
    row = rows(db_session)[0]
    assert row.origin is GeneratedDocumentOrigin.AI and row.prompt_hash
    assert v1["origin"] == "ai" and v1["validation_status"] == "passed"
    # content without the V0.9 `origin` markers (stored by V0.8) still validates
    legacy = {**v1["content"]}
    legacy["summary"] = {k: v for k, v in legacy["summary"].items() if k != "origin"}
    from app.schemas.document_content import TailoredCvContent

    assert TailoredCvContent.model_validate(legacy).summary.origin == "ai"


# --- The CV photo -----------------------------------------------------------------------------


def photo_url(app: dict[str, Any]) -> str:
    return f"{API}/applications/{app['id']}/cv-photo"


def put_photo(
    client: TestClient,
    auth: dict[str, str],
    app: dict[str, Any],
    data: bytes = PNG,
    name: str = "me.png",
    content_type: str = "image/png",
) -> Any:
    return client.put(photo_url(app), headers=auth, files={"file": (name, data, content_type)})


def test_a_photo_can_be_added_replaced_read_and_removed(
    client: TestClient,
    auth: dict[str, str],
    ready: dict[str, Any],
    file_storage: FileStorage,
    db_session: Session,
) -> None:
    assert client.get(photo_url(ready), headers=auth).status_code == 404
    meta = ok(put_photo(client, auth, ready))
    assert meta["content_type"] == "image/png" and meta["file_name"] == "me.png"
    assert meta["size_bytes"] == len(PNG)
    served = client.get(photo_url(ready), headers=auth)
    assert served.status_code == 200 and served.content == PNG  # exactly as uploaded
    assert served.headers["content-type"] == "image/png"
    assert served.headers["cache-control"] == "private, no-store"
    assert listing(client, auth, ready["id"])["photo"]["file_name"] == "me.png"

    first_reference = db_session.scalar(select(ApplicationPhoto.storage_reference))
    assert first_reference and file_storage.exists(reference=first_reference)
    replaced = ok(put_photo(client, auth, ready, JPEG, "better.jpg", "image/jpeg"))
    assert replaced["content_type"] == "image/jpeg"
    assert client.get(photo_url(ready), headers=auth).content == JPEG
    assert not file_storage.exists(reference=first_reference)  # the old file is gone
    db_session.expire_all()
    assert len(list(db_session.scalars(select(ApplicationPhoto)))) == 1

    assert client.delete(photo_url(ready), headers=auth).status_code == 204
    assert client.get(photo_url(ready), headers=auth).status_code == 404
    assert listing(client, auth, ready["id"])["photo"] is None
    assert client.delete(photo_url(ready), headers=auth).status_code == 204  # idempotent


def test_only_real_small_images_are_accepted(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any]
) -> None:
    for data, name, kind in (
        (b"GIF89a....", "x.gif", "image/gif"),
        (b"%PDF-1.4 ...", "x.png", "image/png"),  # the name and declared type lie
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "x.svg", "image/svg+xml"),
        (b"", "empty.png", "image/png"),
    ):
        response = put_photo(client, auth, ready, data, name, kind)
        assert response.status_code == 422, (name, response.text)
    too_big = PNG + b"\x00" * (2 * 1024 * 1024)
    assert put_photo(client, auth, ready, too_big).status_code == 422
    assert client.get(photo_url(ready), headers=auth).status_code == 404  # nothing was stored


def test_a_photo_belongs_to_the_users_application(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], ready: dict[str, Any]
) -> None:
    ok(put_photo(client, auth, ready))
    assert client.get(photo_url(ready), headers=other_auth).status_code == 404
    assert put_photo(client, other_auth, ready, JPEG).status_code == 404
    assert client.delete(photo_url(ready), headers=other_auth).status_code == 404
    assert client.get(photo_url(ready)).status_code == 401
    assert client.get(photo_url(ready), headers=auth).content == PNG  # unchanged


def test_no_ai_operation_touches_the_photo(
    client: TestClient, auth: dict[str, str], ready: dict[str, Any], fake_ai: FakeHumanizeAI
) -> None:
    ok(put_photo(client, auth, ready))
    cv = cv_v1(client, auth, ready)
    ok(generate(client, auth, ready["id"], CV), 201)  # regenerate
    ok(edit(client, auth, ready["id"], cv["id"], cv_edit(cv["content"], summary="Mine.")), 201)
    letter_v1(client, auth, ready)
    ok(humanize(client, auth, ready["id"]), 201)
    assert client.get(photo_url(ready), headers=auth).content == PNG
    # nothing about a photo is in any generated content or any prompt
    for document in listing(client, auth, ready["id"])["items"]:
        assert "photo" not in str(fetch(client, auth, ready, document)["content"]).lower()
    assert all("photo" not in call["prompt"].lower() for call in fake_ai.calls[1:])
