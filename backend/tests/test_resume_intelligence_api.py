import io
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.provider import (
    AIAuthenticationError,
    AIContextLimitError,
    AIEmptyResponseError,
    AINotConfiguredError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.main import app
from app.models.resume import Resume, ResumeStatus
from app.models.resume_profile import ResumeProfile
from app.services.storage.base import FileStorage
from tests.conftest import TestSessionLocal
from tests.resume_helpers import (
    DOCX_MIME,
    FakeAIProvider,
    expected_parsed_resume,
    make_blank_pdf,
    make_docx,
    make_text_pdf,
    sample_resume_text,
)

API = "/api/v1/resumes"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return _headers(register_and_login()["access_token"])


def _upload(
    client: TestClient,
    headers: dict[str, str],
    content: bytes | None = None,
    name: str = "resume.pdf",
    mime: str = "application/pdf",
) -> dict[str, Any]:
    content = content if content is not None else make_text_pdf(sample_resume_text())
    response = client.post(API, headers=headers, files={"file": (name, io.BytesIO(content), mime)})
    assert response.status_code == 201, response.text
    return response.json()


def _process(
    client: TestClient, headers: dict[str, str], resume_id: str, force: bool = False
) -> Any:
    return client.post(f"{API}/{resume_id}/process", headers=headers, json={"force": force})


def _profile(client: TestClient, headers: dict[str, str], resume_id: str) -> dict[str, Any]:
    response = client.get(f"{API}/{resume_id}/profile", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _analyzed(client: TestClient, headers: dict[str, str]) -> tuple[str, dict[str, Any]]:
    resume = _upload(client, headers)
    assert _process(client, headers, resume["id"]).status_code == 202
    return resume["id"], _profile(client, headers, resume["id"])


# --- Processing ----------------------------------------------------------------


def test_processing_a_pdf_produces_a_reviewable_profile(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    resume = _upload(client, auth)
    assert resume["status"] == "uploaded"

    started = _process(client, auth, resume["id"])
    assert started.status_code == 202
    assert started.json()["reused"] is False
    assert started.json()["resume"]["status"] == "extracting"

    body = _profile(client, auth, resume["id"])
    assert body["resume"]["status"] == "needs_review"
    assert body["is_processing"] is False
    profile = body["profile"]
    assert profile["status"] == "needs_review"
    assert profile["parser_version"] == "resume_parser_v1"
    assert profile["ai_provider"] == "fake"
    assert profile["extraction_method"] == "pypdf"
    assert profile["source_file_hash"]
    assert profile["warnings"] == []

    data = profile["data"]
    assert len(data["work_experience"]) == 2
    assert len(data["education"]) == 1
    assert len(data["skills"]) == 5
    assert len(data["languages"]) == 3
    assert len(data["certifications"]) == 1
    assert len(data["projects"]) == 1
    assert len(data["additional_information"]) == 1
    assert data["summary"]["value"].startswith("SAP developer")
    assert {p["field"] for p in data["personal_information"]} >= {"full_name", "email"}

    skill = next(s for s in data["skills"] if s["name"] == "SAP IS-U")
    assert skill["confidence"] == "high"
    assert skill["review_status"] == "pending"
    assert skill["evidence"][0]["verified"] is True
    assert "SAP IS-U applications" in skill["evidence"][0]["source_text"]

    summary = profile["summary"]
    assert summary["sections"]["skills"]["total"] == 5
    assert summary["pending_items"] == summary["total_items"]
    # The parser saw the extracted text, not the file bytes.
    assert "Example Energy GmbH" in fake_ai.calls[0]["prompt"]


def test_processing_a_docx_works(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    resume = _upload(
        client, auth, make_docx(sample_resume_text()), name="resume.docx", mime=DOCX_MIME
    )
    assert _process(client, auth, resume["id"]).status_code == 202

    body = _profile(client, auth, resume["id"])
    assert body["resume"]["status"] == "needs_review"
    assert body["profile"]["extraction_method"] == "python-docx"


def test_status_transitions_through_the_pipeline(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    observed: list[ResumeStatus] = []

    def record_status() -> None:
        with TestSessionLocal() as session:
            observed.append(session.execute(select(Resume.status)).scalar_one())

    fake_ai.on_call = record_status
    resume = _upload(client, auth)
    _process(client, auth, resume["id"])

    assert observed == [ResumeStatus.PARSING]
    assert _profile(client, auth, resume["id"])["resume"]["status"] == "needs_review"


def test_get_profile_before_processing_returns_empty_state(
    client: TestClient, auth: dict[str, str]
) -> None:
    resume = _upload(client, auth)
    body = _profile(client, auth, resume["id"])
    assert body["profile"] is None
    assert body["latest_attempt"] is None
    assert body["resume"]["status"] == "uploaded"


def test_scanned_pdf_reports_ocr_required_without_calling_ai(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    resume = _upload(client, auth, make_blank_pdf())
    _process(client, auth, resume["id"])

    body = _profile(client, auth, resume["id"])
    assert body["resume"]["status"] == "ocr_required"
    assert body["profile"] is None
    attempt = body["latest_attempt"]
    assert attempt["status"] == "ocr_required"
    assert attempt["error_code"] == "OCR_REQUIRED"
    assert attempt["error_message"] == (
        "This PDF does not contain extractable text. "
        "OCR support will be added in a future version."
    )
    assert fake_ai.calls == []


def test_unreadable_file_fails_with_extraction_error(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    # A PDF header (so the V0.13 upload type check accepts it), but no readable PDF.
    resume = _upload(client, auth, b"%PDF-1.4\nnot really a pdf")
    _process(client, auth, resume["id"])

    body = _profile(client, auth, resume["id"])
    assert body["resume"]["status"] == "failed"
    assert body["latest_attempt"]["error_code"] == "EXTRACTION_FAILED"
    assert fake_ai.calls == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AITimeoutError("timeout"), "AI_TIMEOUT"),
        (AIAuthenticationError("401"), "AI_AUTH_FAILED"),
        (AIRateLimitError("429"), "AI_RATE_LIMITED"),
        (AIProviderUnavailableError("503"), "AI_UNAVAILABLE"),
        (AIContextLimitError("too long"), "AI_CONTEXT_LIMIT"),
        (AIEmptyResponseError("empty"), "AI_EMPTY_RESPONSE"),
        (AINotConfiguredError("no key"), "AI_NOT_CONFIGURED"),
        (RuntimeError("boom with secret resume content"), "PROCESSING_ERROR"),
    ],
)
def test_provider_failures_are_handled_gracefully(
    client: TestClient,
    auth: dict[str, str],
    fake_ai: FakeAIProvider,
    error: Exception,
    code: str,
) -> None:
    fake_ai.response = error
    resume = _upload(client, auth)
    _process(client, auth, resume["id"])

    body = _profile(client, auth, resume["id"])
    assert body["resume"]["status"] == "failed"
    assert body["is_processing"] is False
    assert body["profile"] is None
    attempt = body["latest_attempt"]
    assert attempt["status"] == "failed"
    assert attempt["error_code"] == code
    assert attempt["error_message"]
    assert "secret" not in attempt["error_message"]


def test_invalid_ai_output_is_never_stored(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider, db_session: Session
) -> None:
    bad = expected_parsed_resume()
    bad["skills"][0]["proficiency"] = "godlike"
    fake_ai.response = bad
    resume = _upload(client, auth)
    _process(client, auth, resume["id"])

    body = _profile(client, auth, resume["id"])
    assert body["latest_attempt"]["error_code"] == "AI_INVALID_RESPONSE"
    stored = db_session.execute(select(ResumeProfile)).scalar_one()
    assert stored.raw_data is None
    assert stored.reviewed_data is None


def test_empty_extraction_result_fails_instead_of_storing_empty_profile(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    fake_ai.response = {}
    resume = _upload(client, auth)
    _process(client, auth, resume["id"])

    body = _profile(client, auth, resume["id"])
    assert body["resume"]["status"] == "failed"
    assert body["latest_attempt"]["error_code"] == "NO_RESUME_CONTENT"


def test_too_long_resume_is_rejected_not_truncated(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from app.core import config

    monkeypatch.setattr(config.settings, "RESUME_MAX_TEXT_CHARS", 100)
    resume = _upload(client, auth)
    _process(client, auth, resume["id"])

    assert _profile(client, auth, resume["id"])["latest_attempt"]["error_code"] == (
        "RESUME_TOO_LONG"
    )
    assert fake_ai.calls == []


def test_processing_does_not_log_resume_content(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider, caplog
) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    resume = _upload(client, auth)
    _process(client, auth, resume["id"])
    fake_ai.response = RuntimeError("alex.example@example.com leaked?")
    _process(client, auth, resume["id"], force=True)

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "Resume processing succeeded" in logs
    assert "Resume processing crashed" in logs
    for sensitive in ("alex.example@example.com", "Example Energy", "Alex Example", "1234567"):
        assert sensitive not in logs


# --- Reprocessing + cost control ---------------------------------------------------


def test_same_file_and_parser_version_is_not_reprocessed_automatically(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    resume_id, first = _analyzed(client, auth)

    again = _process(client, auth, resume_id)

    assert again.status_code == 200
    assert again.json()["reused"] is True
    assert again.json()["profile_id"] == first["profile"]["id"]
    assert len(fake_ai.calls) == 1


def test_explicit_reprocess_creates_a_new_result_and_keeps_the_original_file(
    client: TestClient,
    auth: dict[str, str],
    fake_ai: FakeAIProvider,
    file_storage: FileStorage,
    db_session: Session,
) -> None:
    original = make_text_pdf(sample_resume_text())
    resume = _upload(client, auth, original)
    _process(client, auth, resume["id"])
    first = _profile(client, auth, resume["id"])["profile"]

    response = _process(client, auth, resume["id"], force=True)

    assert response.status_code == 202
    second = _profile(client, auth, resume["id"])["profile"]
    assert second["id"] != first["id"]
    assert second["status"] == "needs_review"
    assert len(fake_ai.calls) == 2
    statuses = {
        str(p.id): p.status.value for p in db_session.execute(select(ResumeProfile)).scalars()
    }
    assert statuses == {first["id"]: "superseded", second["id"]: "needs_review"}
    stored = db_session.execute(select(Resume)).scalar_one()
    assert file_storage.read(reference=stored.storage_reference) == original


def test_failed_reprocess_keeps_previous_result(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    resume_id, first = _analyzed(client, auth)
    client.post(f"{API}/{resume_id}/profile/accept-pending", headers=auth)
    client.post(f"{API}/{resume_id}/profile/confirm", headers=auth)

    fake_ai.response = AIRateLimitError("429")
    _process(client, auth, resume_id, force=True)

    body = _profile(client, auth, resume_id)
    assert body["latest_attempt"]["error_code"] == "AI_RATE_LIMITED"
    assert body["profile"]["id"] == first["profile"]["id"]
    assert body["profile"]["status"] == "confirmed"
    assert body["resume"]["status"] == "confirmed"


def test_identical_file_uploaded_again_reuses_previous_analysis(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    content = make_text_pdf(sample_resume_text())
    first_id, first = _analyzed(client, auth)
    second = _upload(client, auth, content, name="resume-copy.pdf")
    assert second["version_number"] == 2

    response = _process(client, auth, second["id"])

    assert response.status_code == 200
    assert response.json()["reused"] is True
    assert len(fake_ai.calls) == 1
    profile = _profile(client, auth, second["id"])["profile"]
    assert profile["reused_from_profile_id"] == first["profile"]["id"]
    assert profile["summary"]["pending_items"] == profile["summary"]["total_items"]
    # The first version keeps its own result.
    assert _profile(client, auth, first_id)["profile"]["id"] == first["profile"]["id"]


def test_concurrent_processing_is_rejected_until_stale(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    resume = _upload(client, auth)
    row = db_session.get(Resume, resume["id"])
    assert row is not None
    row.status = ResumeStatus.PARSING
    row.processing_started_at = datetime.now(UTC)
    db_session.commit()

    response = _process(client, auth, resume["id"])
    assert response.status_code == 409
    assert _profile(client, auth, resume["id"])["is_processing"] is True

    row.processing_started_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()
    assert _process(client, auth, resume["id"]).status_code == 202


# --- Review -----------------------------------------------------------------------


def _item(profile: dict[str, Any], section: str, **match: Any) -> dict[str, Any]:
    items = profile["data"][section]
    items = items if isinstance(items, list) else [items]
    return next(i for i in items if all(i.get(k) == v for k, v in match.items()))


def _review(
    client: TestClient, headers: dict[str, str], resume_id: str, item_id: str, **payload: Any
) -> Any:
    return client.patch(f"{API}/{resume_id}/profile/items/{item_id}", headers=headers, json=payload)


def test_accept_reject_and_edit_items(client: TestClient, auth: dict[str, str]) -> None:
    resume_id, body = _analyzed(client, auth)
    profile = body["profile"]
    isu = _item(profile, "skills", name="SAP IS-U")
    git = _item(profile, "skills", name="Git")
    role = _item(profile, "work_experience", company="Sample Consulting AG")

    accepted = _review(client, auth, resume_id, isu["id"], action="accept")
    assert accepted.status_code == 200
    assert _item(accepted.json(), "skills", name="SAP IS-U")["review_status"] == "accepted"

    rejected = _review(client, auth, resume_id, git["id"], action="reject")
    assert _item(rejected.json(), "skills", name="Git")["review_status"] == "rejected"
    assert rejected.json()["summary"]["sections"]["skills"]["rejected"] == 1

    edited = _review(
        client,
        auth,
        resume_id,
        role["id"],
        action="edit",
        changes={"job_title": "SAP ABAP Consultant", "end_date": "2022-11"},
    )
    assert edited.status_code == 200, edited.text
    item = _item(edited.json(), "work_experience", company="Sample Consulting AG")
    assert item["job_title"] == "SAP ABAP Consultant"
    assert item["end_date"] == "2022-11"
    assert item["review_status"] == "edited"
    assert sorted(item["user_edited_fields"]) == ["end_date", "job_title"]
    # The parser's original output is preserved separately.
    raw_role = next(
        r for r in edited.json()["raw_data"]["work_experience"] if r["id"] == role["id"]
    )
    assert raw_role["job_title"] == "SAP ABAP Developer"
    assert raw_role["review_status"] == "pending"

    reset = _review(client, auth, resume_id, role["id"], action="reset")
    item = _item(reset.json(), "work_experience", company="Sample Consulting AG")
    assert item["job_title"] == "SAP ABAP Developer"
    assert item["review_status"] == "pending"


def test_edit_personal_information_and_summary(client: TestClient, auth: dict[str, str]) -> None:
    resume_id, body = _analyzed(client, auth)
    location = _item(body["profile"], "personal_information", field="location")

    response = _review(
        client, auth, resume_id, location["id"], action="edit", changes={"value": "Potsdam"}
    )

    assert _item(response.json(), "personal_information", field="location")["value"] == "Potsdam"


@pytest.mark.parametrize(
    "changes",
    [
        {"start_date": "sometime in 2020"},
        {"unknown_field": "x"},
        {"confidence": "high"},  # confidence/evidence are not user-editable
        {},
    ],
)
def test_invalid_edits_are_rejected(
    client: TestClient, auth: dict[str, str], changes: dict[str, Any]
) -> None:
    resume_id, body = _analyzed(client, auth)
    role = body["profile"]["data"]["work_experience"][0]

    response = _review(client, auth, resume_id, role["id"], action="edit", changes=changes)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_review_unknown_item_returns_404(client: TestClient, auth: dict[str, str]) -> None:
    resume_id, _ = _analyzed(client, auth)
    response = _review(client, auth, resume_id, "does-not-exist", action="accept")
    assert response.status_code == 404


def test_confirm_requires_every_item_to_be_reviewed(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    resume_id, body = _analyzed(client, auth)

    blocked = client.post(f"{API}/{resume_id}/profile/confirm", headers=auth)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["pending_items"] == body["profile"]["summary"][
        "total_items"
    ]

    accepted = client.post(f"{API}/{resume_id}/profile/accept-pending", headers=auth)
    assert accepted.json()["summary"]["pending_items"] == 0

    confirmed = client.post(f"{API}/{resume_id}/profile/confirm", headers=auth)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmed_at"] is not None
    assert _profile(client, auth, resume_id)["resume"]["status"] == "confirmed"


def test_confirmed_profile_is_locked_until_reopened(
    client: TestClient, auth: dict[str, str]
) -> None:
    resume_id, body = _analyzed(client, auth)
    skill = body["profile"]["data"]["skills"][0]
    client.post(f"{API}/{resume_id}/profile/accept-pending", headers=auth)
    client.post(f"{API}/{resume_id}/profile/confirm", headers=auth)

    locked = _review(client, auth, resume_id, skill["id"], action="reject")
    assert locked.status_code == 409

    reopened = client.post(f"{API}/{resume_id}/profile/reopen", headers=auth)
    assert reopened.json()["status"] == "needs_review"
    assert reopened.json()["confirmed_at"] is None
    assert _review(client, auth, resume_id, skill["id"], action="reject").status_code == 200


def test_review_before_analysis_returns_404(client: TestClient, auth: dict[str, str]) -> None:
    resume = _upload(client, auth)
    response = client.post(f"{API}/{resume['id']}/profile/confirm", headers=auth)
    assert response.status_code == 404


def test_conflicts_with_user_profile_are_exposed(
    client: TestClient, auth: dict[str, str]
) -> None:
    client.put(
        "/api/v1/profile",
        headers=auth,
        json={"current_job_title": "Java Developer", "years_of_experience": 12},
    )
    resume_id, body = _analyzed(client, auth)

    conflicts = {c["field"]: c for c in body["profile"]["conflicts"]}
    assert conflicts["current_job_title"]["profile_value"] == "Java Developer"
    assert conflicts["current_job_title"]["resume_value"] == "SAP IS-U Developer"
    assert conflicts["years_of_experience"]["potential_conflict"] is True
    assert conflicts["years_of_experience"]["profile_value"] == "12"
    # Account name "Jane Doe" vs resume "Alex Example".
    assert "full_name" in conflicts
    # The user's own profile is untouched by analysis.
    profile = client.get("/api/v1/profile", headers=auth).json()
    assert profile["current_job_title"] == "Java Developer"
    assert profile["years_of_experience"] == 12


def test_source_text_is_available_for_evidence_inspection(
    client: TestClient, auth: dict[str, str]
) -> None:
    resume_id, _ = _analyzed(client, auth)
    response = client.get(f"{API}/{resume_id}/profile/source", headers=auth)
    assert response.status_code == 200
    assert "Developed and maintained SAP IS-U applications" in response.json()["text"]


# --- Authorization -------------------------------------------------------------------


def test_resume_intelligence_requires_authentication(
    client: TestClient, auth: dict[str, str]
) -> None:
    resume = _upload(client, auth)
    rid = resume["id"]
    assert client.post(f"{API}/{rid}/process").status_code == 401
    assert client.get(f"{API}/{rid}/profile").status_code == 401
    assert client.get(f"{API}/{rid}/profile/source").status_code == 401
    assert client.post(f"{API}/{rid}/profile/confirm").status_code == 401
    assert client.patch(f"{API}/{rid}/profile/items/x", json={"action": "accept"}).status_code == (
        401
    )


def test_users_cannot_access_each_others_resumes_or_profiles(
    client: TestClient, register_and_login, fake_ai: FakeAIProvider
) -> None:  # type: ignore[no-untyped-def]
    owner = _headers(register_and_login(email="owner@example.com")["access_token"])
    other = _headers(register_and_login(email="other@example.com")["access_token"])
    resume_id, body = _analyzed(client, owner)
    item_id = body["profile"]["data"]["skills"][0]["id"]

    assert client.get(f"{API}/{resume_id}/profile", headers=other).status_code == 404
    assert client.get(f"{API}/{resume_id}/profile/source", headers=other).status_code == 404
    assert _process(client, other, resume_id, force=True).status_code == 404
    assert _review(client, other, resume_id, item_id, action="reject").status_code == 404
    assert client.post(f"{API}/{resume_id}/profile/accept-pending", headers=other).status_code == (
        404
    )
    assert client.post(f"{API}/{resume_id}/profile/confirm", headers=other).status_code == 404
    assert client.post(f"{API}/{resume_id}/profile/reopen", headers=other).status_code == 404
    assert client.delete(f"{API}/{resume_id}", headers=other).status_code == 404
    assert client.get(API, headers=other).json() == []
    # Nothing the other user tried had any effect.
    assert len(fake_ai.calls) == 1
    owner_view = _profile(client, owner, resume_id)["profile"]
    assert owner_view["summary"]["pending_items"] == owner_view["summary"]["total_items"]


def test_original_files_are_not_publicly_served(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    resume = _upload(client, auth)
    stored = db_session.get(Resume, resume["id"])
    assert stored is not None
    for path in (
        f"/storage/resumes/{stored.storage_reference}",
        f"/{stored.storage_reference}",
        f"/api/v1/resumes/{resume['id']}/file",
    ):
        assert client.get(path).status_code in (404, 405)
    # No static-file mount exposes the storage directory.
    assert not any(type(route).__name__ == "Mount" for route in app.routes)


def test_deleting_a_resume_removes_its_profiles(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    resume_id, _ = _analyzed(client, auth)
    assert client.delete(f"{API}/{resume_id}", headers=auth).status_code == 204
    db_session.expire_all()
    assert db_session.execute(select(ResumeProfile)).first() is None
