"""V0.10 file inputs: the assistant attaches a document only after the user chose it, only
one of *their own application's* documents, and exactly the version they chose. Real
Chromium on a local fake employer form; the AI is a deterministic fake.
"""

import io
import uuid
from typing import Any

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AutomationField
from app.services.automation import documents
from tests.application_helpers import API
from tests.automation_helpers import (
    FakeAutomationAI,
    Human,
    field_named,
    get_fields,
    make_application,
    post,
    session_url,
    started_session,
)
from tests.document_helpers import generate
from tests.fake_site import FakeApplicationSite
from tests.question_helpers import error, ok
from tests.test_automation_fields import Ctx, make_ctx


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


@pytest.fixture(autouse=True)
def _storage(monkeypatch: pytest.MonkeyPatch, file_storage: Any) -> None:
    monkeypatch.setattr(documents, "get_file_storage", lambda: file_storage)


@pytest.fixture
def ctx(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> Ctx:
    """An application with a confirmed resume and a tailored CV + cover letter."""
    context = make_ctx(client, auth, db_session, site, resume=True)
    for kind in ("cv", "cover-letter"):
        assert generate(client, auth, context.aid, kind).status_code == 201, kind
    return context


def _field(ctx: Ctx, label: str) -> dict[str, Any]:
    return ctx.field(label)


def _use(ctx: Ctx, label: str, choice: dict[str, Any]) -> dict[str, Any]:
    field = _field(ctx, label)
    response = ctx.client.put(
        f"{session_url(ctx.aid, ctx.sid)}/fields/{field['id']}/document",
        headers=ctx.auth,
        json={"document_type": choice["type"], "document_id": choice["id"]},
    )
    return ok(response)


def _choices(ctx: Ctx, label: str) -> list[dict[str, Any]]:
    response = ctx.client.get(
        f"{session_url(ctx.aid, ctx.sid)}/fields/{_field(ctx, label)['id']}/documents",
        headers=ctx.auth,
    )
    return ok(response)["items"]  # type: ignore[no-any-return]


def test_a_file_field_proposes_the_matching_document_and_uploads_nothing_yet(ctx: Ctx) -> None:
    cv = _field(ctx, "Resume / CV")
    assert cv["document"]["detected_type"] == "tailored_cv"
    assert cv["document"]["proposed"]["label"] == "Tailored CV v1"
    assert cv["document"]["proposed"]["is_current"] is True
    assert cv["document"]["selected"] is None and cv["document"]["confirmed"] is False
    assert cv["display_status"] == "needs_review"
    letter = _field(ctx, "Cover letter")
    assert letter["document"]["proposed"]["label"] == "Cover letter v1"
    ctx.fill()
    assert ctx.human.files("#cv") == [] and ctx.human.files("#cover") == []  # not confirmed


def test_use_this_file_attaches_the_chosen_document_and_verifies_it(ctx: Ctx) -> None:
    proposed = _field(ctx, "Resume / CV")["document"]["proposed"]
    used = _use(ctx, "Resume / CV", proposed)
    assert used["document"]["confirmed"] is True
    assert used["document"]["selected"]["id"] == proposed["id"]
    assert used["display_status"] == "ready"
    session = ctx.fill()
    files = ctx.human.files("#cv")
    assert len(files) == 1 and files[0][0].endswith(".docx") and files[0][1] > 2000
    cv = _field(ctx, "Resume / CV")
    assert cv["display_status"] == "filled" and cv["fill"]["status"] == "FILLED"
    assert session["counts"]["filled"] >= 1
    assert ctx.human.files("#cover") == []  # only what was confirmed


def test_the_attached_file_is_the_rendered_document(
    ctx: Ctx, db_session: Session, auth: dict[str, str], client: TestClient
) -> None:
    from sqlalchemy import select

    from app.models import Application, User

    user = db_session.scalar(select(User))
    application = db_session.scalar(select(Application))
    assert user is not None and application is not None
    choice = documents.list_choices(db_session, user, application)
    cover = next(c for c in choice if c.type == "cover_letter")
    name, mime, data = documents.render(db_session, user, application, cover)
    assert mime == documents.DOCX_MIME and name.endswith(".docx")
    text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    assert "Dear" in text or "Sehr geehrte" in text or len(text) > 50


def test_a_newer_version_never_replaces_the_one_the_user_chose(
    ctx: Ctx, client: TestClient, auth: dict[str, str]
) -> None:
    v1 = _field(ctx, "Resume / CV")["document"]["proposed"]
    _use(ctx, "Resume / CV", v1)
    assert generate(client, auth, ctx.aid, "cv").status_code == 201  # v2 is now current
    field = _field(ctx, "Resume / CV")
    assert field["document"]["selected"]["id"] == v1["id"]
    assert field["document"]["selected"]["label"] == "Tailored CV v1"
    assert field["document"]["newer_available"] is True
    assert field["document"]["proposed"]["label"] == "Tailored CV v2"
    ctx.fill()
    # The browser holds v1 -- the file the user chose.
    assert ctx.human.files("#cv")[0][0] == v1["file_name"]
    labels = [c["label"] for c in _choices(ctx, "Resume / CV")]
    assert "Tailored CV v1" in labels and "Tailored CV v2" in labels


def test_the_user_can_choose_another_file(
    ctx: Ctx, client: TestClient, auth: dict[str, str]
) -> None:
    assert generate(client, auth, ctx.aid, "cv").status_code == 201
    choices = _choices(ctx, "Resume / CV")
    older = next(c for c in choices if c["label"] == "Tailored CV v1")
    used = _use(ctx, "Resume / CV", older)
    assert used["document"]["selected"]["label"] == "Tailored CV v1"
    other = next(c for c in choices if c["type"] == "resume_file")
    again = _use(ctx, "Resume / CV", other)
    assert again["document"]["selected"]["type"] == "resume_file"
    assert again["document"]["selected"]["label"].startswith("Your uploaded resume")
    ctx.fill()
    assert ctx.human.files("#cv")[0][0] == other["file_name"]


def test_an_unclear_field_gets_no_proposal_but_the_user_can_still_choose(ctx: Ctx) -> None:
    other = _field(ctx, "Other documents")
    assert other["document"]["detected_type"] is None and other["document"]["proposed"] is None
    assert other["display_status"] == "action_required"
    assert other["mapping"]["status"] == "NEEDS_REVIEW"
    ctx.fill()
    assert ctx.human.files("#other") == []
    # Left to the user outright: JobPilot never guesses which document it is.
    assert _field(ctx, "Other documents")["fill"]["status"] == "USER_ACTION_REQUIRED"
    assert _field(ctx, "Other documents")["fill"]["error_code"] == "FILE_UNCLEAR"
    choice = next(c for c in _choices(ctx, "Other documents") if c["label"] == "Cover letter v1")
    used = _use(ctx, "Other documents", choice)
    assert used["mapping"]["status"] == "USER_CONFIRMED"
    ctx.fill()
    assert len(ctx.human.files("#other")) == 1


def test_a_field_without_any_matching_document_asks_for_one(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    context = make_ctx(client, auth, db_session, site, resume=True)  # nothing generated yet
    cv = context.field("Resume / CV")
    assert cv["document"]["proposed"] is None
    assert cv["display_status"] == "needs_input"
    assert "No tailored CV exists yet" in cv["display_message"]


def test_a_document_of_someone_else_or_another_application_cannot_be_chosen(
    ctx: Ctx,
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    db_session: Session,
    site: FakeApplicationSite,
) -> None:
    field = _field(ctx, "Resume / CV")
    url = f"{session_url(ctx.aid, ctx.sid)}/fields/{field['id']}/document"
    # Another user's document.
    foreign = make_application(
        client, other_auth, db_session, site, email="mallory@example.com", resume=True
    )
    assert generate(client, other_auth, foreign["id"], "cv").status_code == 201
    foreign_id = next(
        d["id"]
        for d in client.get(
            f"{API}/applications/{foreign['id']}/documents", headers=other_auth
        ).json()["items"]
    )
    for body in (
        {"document_type": "tailored_cv", "document_id": foreign_id},
        {"document_type": "tailored_cv", "document_id": str(uuid.uuid4())},
        {"document_type": "resume_file", "document_id": str(uuid.uuid4())},
        {"document_type": "cover_letter", "document_id": foreign_id},  # right id, wrong type
    ):
        error(client.put(url, headers=auth, json=body), 404, "NOT_FOUND")
    # A path (or anything but an id and a type) is not accepted at all.
    for body in (
        {"document_type": "tailored_cv", "document_id": "/etc/passwd"},
        {"path": "/etc/passwd"},
        {"document_type": "tailored_cv", "document_id": foreign_id, "file_path": "/etc/passwd"},
    ):
        assert client.put(url, headers=auth, json=body).status_code == 422
    assert _field(ctx, "Resume / CV")["document"]["selected"] is None


def test_the_other_users_cannot_list_or_choose_documents_of_my_fields(
    ctx: Ctx, client: TestClient, other_auth: dict[str, str]
) -> None:
    field = _field(ctx, "Resume / CV")
    url = f"{session_url(ctx.aid, ctx.sid)}/fields/{field['id']}"
    assert client.get(f"{url}/documents", headers=other_auth).status_code == 404
    assert (
        client.put(
            f"{url}/document",
            headers=other_auth,
            json={"document_type": "tailored_cv", "document_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    assert client.delete(f"{url}/document", headers=other_auth).status_code == 404


def test_responses_never_contain_a_file_path(
    ctx: Ctx, client: TestClient, auth: dict[str, str]
) -> None:
    choices = _choices(ctx, "Resume / CV")
    text = client.get(f"{session_url(ctx.aid, ctx.sid)}/fields", headers=auth).text + str(choices)
    for forbidden in ("storage_reference", "/tmp", "/home", "\\\\", "resumes/"):
        assert forbidden not in text


def test_a_form_that_does_not_take_the_format_is_left_to_the_user(
    client: TestClient, auth: dict[str, str], db_session: Session, site: FakeApplicationSite
) -> None:
    context = make_ctx(client, auth, db_session, site, path="/pdf-only", resume=True)
    assert generate(client, auth, context.aid, "cv").status_code == 201
    cv = context.field("Resume / CV")
    assert cv["document"]["format_accepted"] is False  # JobPilot attaches DOCX
    _use(context, "Resume / CV", cv["document"]["proposed"])
    context.fill()
    refreshed = context.field("Resume / CV")
    assert refreshed["display_status"] == "action_required"
    assert refreshed["fill"]["error_code"] == "FILE_TYPE_NOT_ACCEPTED"
    assert context.human.files("#cv") == []
    # The user's own PDF resume is accepted.
    resume = next(c for c in _choices(context, "Resume / CV") if c["type"] == "resume_file")
    _use(context, "Resume / CV", resume)
    context.fill()
    assert context.human.files("#cv")[0][0] == resume["file_name"]


def test_a_chosen_file_that_the_user_replaced_in_the_browser_is_left_alone(ctx: Ctx) -> None:
    proposed = _field(ctx, "Resume / CV")["document"]["proposed"]
    _use(ctx, "Resume / CV", proposed)
    ctx.fill()
    ctx.human._page_do(  # noqa: SLF001
        lambda page: page.set_input_files(
            "#cv",
            files=[{"name": "my-own.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 x"}],
        )
    )
    ctx.fill()
    assert ctx.human.files("#cv")[0][0] == "my-own.pdf"
    assert _field(ctx, "Resume / CV")["display_status"] == "user_modified"


def test_clearing_a_choice_stops_the_upload(
    ctx: Ctx, client: TestClient, auth: dict[str, str]
) -> None:
    proposed = _field(ctx, "Resume / CV")["document"]["proposed"]
    _use(ctx, "Resume / CV", proposed)
    field = _field(ctx, "Resume / CV")
    cleared = ok(
        client.delete(
            f"{session_url(ctx.aid, ctx.sid)}/fields/{field['id']}/document", headers=auth
        )
    )
    assert cleared["document"]["selected"] is None and cleared["document"]["confirmed"] is False
    ctx.fill()
    assert ctx.human.files("#cv") == []


def test_a_document_that_disappeared_fails_clearly(
    ctx: Ctx, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposed = _field(ctx, "Resume / CV")["document"]["proposed"]
    _use(ctx, "Resume / CV", proposed)
    row = db_session.get(AutomationField, uuid.UUID(_field(ctx, "Resume / CV")["id"]))
    assert row is not None
    row.document_id = uuid.uuid4()  # the pinned document is gone
    db_session.commit()
    ctx.fill()
    cv = _field(ctx, "Resume / CV")
    assert (
        cv["fill"]["status"] == "FILL_FAILED" and cv["fill"]["error_code"] == "DOCUMENT_UNAVAILABLE"
    )
    assert ctx.human.files("#cv") == []


def test_accept_lists() -> None:
    accepted = documents.file_accepted
    docx = documents.DOCX_MIME
    assert accepted(None, "cv.docx", docx) and accepted("", "cv.docx", docx)
    assert accepted(".pdf,.doc,.docx", "cv.docx", docx)
    assert accepted(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "x", docx
    )
    assert accepted("application/*", "x.bin", "application/pdf")
    assert accepted("*/*", "x", "text/plain")
    assert not accepted(".pdf", "cv.docx", docx)
    assert not accepted("image/*", "cv.docx", docx)
    assert accepted(".PDF", "CV.pdf", "application/pdf")
    # Unused import guard for helpers shared with the other test modules.
    assert Human and field_named and get_fields and post and started_session
