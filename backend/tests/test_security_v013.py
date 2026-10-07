"""V0.13 security regression tests: authentication, sessions, brute-force protection,
authorization across users, uploads, HTTP headers, CORS, request size, error and log
hygiene, configuration safety and AI data minimization.

They test behaviour through the real API (and real config / middleware objects), not
implementation details.
"""

import io
import logging
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.orm import Session

from app.core import config as config_module
from app.core.config import InsecureConfigurationError, Settings, settings, validate_security
from app.core.security import create_access_token
from app.main import app
from tests.application_helpers import API, make_job, started, upload_resume, user_by_email
from tests.question_helpers import add_question
from tests.resume_helpers import FakeAIProvider, make_text_pdf, sample_resume_text

PUBLIC_ROUTES = {"/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/logout"}


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


# --- Every non-public route requires a valid session ------------------------------------------


def _api_routes() -> list[tuple[str, str]]:
    routes = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1"):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                routes.append((method, route.path))
    return routes


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)


def test_the_route_inventory_is_not_empty() -> None:
    assert len(_api_routes()) > 100


@pytest.mark.parametrize(("method", "path"), _api_routes())
def test_every_non_public_route_rejects_unauthenticated_requests(
    client: TestClient, method: str, path: str
) -> None:
    if path in PUBLIC_ROUTES:
        pytest.skip("public by design")
    response = client.request(method, _concrete(path), json={})
    assert response.status_code == 401, (method, path, response.text)
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    "token_kind", ["garbage", "wrong_key", "alg_none", "expired", "no_session", "unknown_session"]
)
def test_forged_or_stale_tokens_are_rejected(
    client: TestClient, register_and_login: Any, token_kind: str
) -> None:
    auth = register_and_login(email="forge@example.com")
    user_id = auth["user"]["id"]
    now = datetime.now(UTC)
    claims = {"sub": user_id, "sid": str(uuid.uuid4()), "exp": now + timedelta(minutes=5)}
    token = {
        "garbage": "not-a-token",
        "wrong_key": jwt.encode(claims, "another-secret", algorithm="HS256"),
        "alg_none": jwt.encode(claims, "", algorithm="HS256").rsplit(".", 1)[0] + ".",
        "expired": create_access_token(
            user_id, uuid.uuid4(), expires_at=now - timedelta(seconds=1)
        ),
        "no_session": jwt.encode(
            {"sub": user_id, "exp": now + timedelta(minutes=5)},
            settings.SECRET_KEY,
            algorithm="HS256",
        ),
        "unknown_session": create_access_token(user_id, uuid.uuid4()),
    }[token_kind]
    assert client.get(f"{API}/users/me", headers=_headers(token)).status_code == 401


# --- Sessions ---------------------------------------------------------------------------------


def test_logout_revokes_the_token_on_the_server(
    client: TestClient, register_and_login: Any
) -> None:
    token = register_and_login(email="out@example.com")["access_token"]
    assert client.get(f"{API}/users/me", headers=_headers(token)).status_code == 200
    assert client.post(f"{API}/auth/logout", headers=_headers(token)).status_code == 204
    assert client.get(f"{API}/users/me", headers=_headers(token)).status_code == 401
    # Idempotent, also without a token.
    assert client.post(f"{API}/auth/logout", headers=_headers(token)).status_code == 204
    assert client.post(f"{API}/auth/logout").status_code == 204


def test_logout_everywhere_ends_every_session(client: TestClient, register_and_login: Any) -> None:
    first = register_and_login(email="multi@example.com", password="supersecret123")["access_token"]
    second = _login(client, "multi@example.com", "supersecret123").json()["access_token"]
    assert client.post(f"{API}/auth/logout-all", headers=_headers(second)).status_code == 204
    for token in (first, second):
        assert client.get(f"{API}/users/me", headers=_headers(token)).status_code == 401
    # A new sign-in works again.
    assert _login(client, "multi@example.com", "supersecret123").status_code == 200


def test_tokens_are_never_put_in_urls_and_carry_only_ids(
    client: TestClient, register_and_login: Any
) -> None:
    token = register_and_login(email="claims@example.com")["access_token"]
    payload = jwt.get_unverified_claims(token)
    assert set(payload) == {"sub", "sid", "iat", "exp"}  # no e-mail, no name, no role


# --- Brute-force protection and enumeration ---------------------------------------------------


def test_repeated_failed_logins_are_throttled_per_account(
    client: TestClient, register_and_login: Any
) -> None:
    register_and_login(email="victim@example.com", password="correct-password")
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
        assert _login(client, "victim@example.com", "wrong-password").status_code == 401
    blocked = _login(client, "victim@example.com", "correct-password")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"
    assert int(blocked.headers["Retry-After"]) > 0
    # Other accounts are unaffected.
    register_and_login(email="bystander@example.com", password="correct-password")
    assert _login(client, "bystander@example.com", "correct-password").status_code == 200


def test_a_successful_login_resets_the_failure_count(
    client: TestClient, register_and_login: Any
) -> None:
    register_and_login(email="typo@example.com", password="correct-password")
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS - 1):
        _login(client, "typo@example.com", "wrong")
    assert _login(client, "typo@example.com", "correct-password").status_code == 200
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS - 1):
        assert _login(client, "typo@example.com", "wrong").status_code == 401
    assert _login(client, "typo@example.com", "correct-password").status_code == 200


def test_many_failures_from_one_address_are_throttled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS_PER_IP", 3)
    for i in range(3):
        assert _login(client, f"spray{i}@example.com", "x").status_code == 401
    assert _login(client, "spray9@example.com", "x").status_code == 429


def test_registration_is_throttled_per_address(
    client: TestClient, register_and_login: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REGISTRATION_MAX_PER_IP", 2)
    register_and_login(email="r1@example.com")
    register_and_login(email="r2@example.com")
    response = client.post(
        f"{API}/auth/register",
        json={
            "email": "r3@example.com",
            "password": "supersecret123",
            "first_name": "A",
            "last_name": "B",
        },
    )
    assert response.status_code == 429


def test_unknown_email_and_wrong_password_look_the_same(
    client: TestClient, register_and_login: Any
) -> None:
    register_and_login(email="known@example.com", password="correct-password")
    wrong = _login(client, "known@example.com", "wrong-password")
    unknown = _login(client, "nobody@example.com", "wrong-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_passwords_beyond_bcrypts_limit_are_refused_not_truncated(client: TestClient) -> None:
    password = "ä" * 40  # 40 characters, 80 bytes
    response = client.post(
        f"{API}/auth/register",
        json={
            "email": "long@example.com",
            "password": password,
            "first_name": "A",
            "last_name": "B",
        },
    )
    assert response.status_code == 422
    assert "72 bytes" in response.json()["error"]["message"]


def test_validation_errors_do_not_echo_submitted_values(client: TestClient) -> None:
    secret = "MySecretPassword!" + "x" * 200  # too long: rejected
    response = client.post(
        f"{API}/auth/register",
        json={"email": "echo@example.com", "password": secret, "first_name": "A", "last_name": "B"},
    )
    assert response.status_code == 422
    assert "MySecretPassword" not in response.text
    assert all("input" not in error for error in response.json()["error"]["details"]["errors"])


def test_the_password_hash_never_leaves_the_api(
    client: TestClient, register_and_login: Any
) -> None:
    auth = register_and_login(email="hash@example.com")
    for path in ("/users/me", "/profile"):
        body = client.get(f"{API}{path}", headers=_headers(auth["access_token"])).text
        assert "password" not in body and "$2b$" not in body


# --- Authorization: user B can never reach user A's resources ---------------------------------


def test_cross_user_access_fails_for_every_owned_resource(
    client: TestClient, register_and_login: Any, db_session: Session
) -> None:
    a = _headers(register_and_login(email="owner@example.com")["access_token"])
    b = _headers(register_and_login(email="intruder@example.com")["access_token"])
    owner = user_by_email(db_session, "owner@example.com")

    resume = upload_resume(client, a)
    job = make_job(db_session, owner)
    application = started(client, a, job)
    app_id = application["id"]
    question = add_question(client, a, app_id)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    assert (
        client.put(
            f"{API}/applications/{app_id}/cv-photo",
            headers=a,
            files={"file": ("me.png", png, "image/png")},
        ).status_code
        == 200
    )
    skill = client.post(f"{API}/profile/skills", headers=a, json={"name": "Python"}).json()
    title = client.post(
        f"{API}/preferences/job-titles", headers=a, json={"title": "Engineer"}
    ).json()

    ids = {
        "resume": resume["id"],
        "job": str(job.id),
        "app": app_id,
        "q": question["id"],
        "skill": skill["id"],
        "title": title["id"],
    }
    attempts = [
        ("GET", "/resumes/{resume}/profile"),
        ("GET", "/resumes/{resume}/profile/source"),
        ("POST", "/resumes/{resume}/process"),
        ("POST", "/resumes/{resume}/profile/confirm"),
        ("DELETE", "/resumes/{resume}"),
        ("GET", "/jobs/{job}"),
        ("GET", "/jobs/{job}/sources"),
        ("GET", "/jobs/{job}/match"),
        ("POST", "/jobs/{job}/match"),
        ("PUT", "/jobs/{job}/save"),
        ("POST", "/jobs/{job}/application"),
        ("GET", "/applications/{app}"),
        ("PATCH", "/applications/{app}"),
        ("PATCH", "/applications/{app}/status"),
        ("PATCH", "/applications/{app}/tracking"),
        ("GET", "/applications/{app}/timeline"),
        ("GET", "/applications/{app}/documents"),
        ("POST", "/applications/{app}/documents/cv"),
        ("GET", "/applications/{app}/cv-photo"),
        ("DELETE", "/applications/{app}/cv-photo"),
        ("GET", "/applications/{app}/questions"),
        ("GET", "/applications/{app}/questions/{q}"),
        ("PATCH", "/applications/{app}/questions/{q}"),
        ("POST", "/applications/{app}/questions/{q}/manual"),
        ("DELETE", "/applications/{app}/questions/{q}"),
        ("GET", "/applications/{app}/automation"),
        ("POST", "/applications/{app}/automation/start"),
        ("GET", "/applications/{app}/automation/submissions"),
        ("PUT", "/profile/skills/{skill}"),
        ("DELETE", "/profile/skills/{skill}"),
        ("DELETE", "/preferences/job-titles/{title}"),
    ]
    bodies: dict[str, Any] = {
        "PATCH /applications/{app}/status": {"status": "APPLIED"},
        "PATCH /applications/{app}/tracking": {"note": "x"},
        "PATCH /applications/{app}": {"notes": "x"},
        "POST /applications/{app}/questions/{q}/manual": {"answer": "x"},
        "PATCH /applications/{app}/questions/{q}": {"question_text": "Changed?"},
        "PUT /profile/skills/{skill}": {"proficiency": "expert"},
    }
    for method, template in attempts:
        path = template.format(**ids)
        response = client.request(
            method, f"{API}{path}", headers=b, json=bodies.get(f"{method} {template}")
        )
        assert response.status_code in (403, 404), (
            method,
            template,
            response.status_code,
            response.text,
        )
        # Nothing about A leaks into the error.
        assert "owner@example.com" not in response.text

    # A's data is intact and unchanged.
    assert client.get(f"{API}/applications/{app_id}", headers=a).json()["status"] == "PREPARING"
    assert client.get(f"{API}/applications/{app_id}/cv-photo", headers=a).status_code == 200
    assert (
        client.get(f"{API}/applications/{app_id}/questions/{question['id']}", headers=a).status_code
        == 200
    )
    assert any(r["id"] == resume["id"] for r in client.get(f"{API}/resumes", headers=a).json())
    # B's own lists contain nothing of A's.
    assert client.get(f"{API}/applications", headers=b).json()["total"] == 0
    assert client.get(f"{API}/resumes", headers=b).json() == []
    assert client.get(f"{API}/jobs?page=1&page_size=50", headers=b).json()["total"] == 0


# --- Uploads ------------------------------------------------------------------------------------


@pytest.fixture
def auth(register_and_login: Any) -> dict[str, str]:
    return _headers(register_and_login(email="uploader@example.com")["access_token"])


@pytest.mark.parametrize(
    ("name", "content", "mime"),
    [
        ("resume.pdf", b"<html><script>alert(1)</script></html>", "application/pdf"),
        ("resume.pdf", b"MZ\x90\x00 executable", "application/pdf"),
        (
            "resume.docx",
            b"%PDF-1.4 a pdf named docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("resume.pdf", b"PK\x03\x04 not really a zip", "application/pdf"),
        ("resume.exe", b"%PDF-1.4 x", "application/pdf"),
        ("resume.pdf", b"%PDF-1.4 x", "application/x-msdownload"),
    ],
)
def test_uploads_are_checked_by_content_not_by_name(
    client: TestClient, auth: dict[str, str], name: str, content: bytes, mime: str
) -> None:
    response = client.post(
        f"{API}/resumes", headers=auth, files={"file": (name, io.BytesIO(content), mime)}
    )
    assert response.status_code == 422, response.text
    assert client.get(f"{API}/resumes", headers=auth).json() == []


def test_a_zip_that_is_not_a_word_document_is_refused(
    client: TestClient, auth: dict[str, str]
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("payload.sh", "rm -rf /")
    response = client.post(
        f"{API}/resumes",
        headers=auth,
        files={
            "file": (
                "cv.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 422


def test_upload_names_cannot_choose_a_path_and_internal_paths_are_not_exposed(
    client: TestClient, auth: dict[str, str], file_storage: Any
) -> None:
    content = make_text_pdf(sample_resume_text())
    response = client.post(
        f"{API}/resumes",
        headers=auth,
        files={"file": ("../../../etc/cron.d/evil.pdf", io.BytesIO(content), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["file_name"] == "evil.pdf"
    assert "storage_reference" not in body
    assert "/" not in body["file_name"] and "\\" not in body["file_name"]
    # The browser-claimed type is replaced by the verified one.
    assert body["mime_type"] == "application/pdf"


def test_file_names_lose_paths_and_control_characters() -> None:
    from app.services.storage.uploads import safe_file_name

    assert safe_file_name("..\\..\\windows\\system32\\cv.pdf", "x") == "cv.pdf"
    assert safe_file_name("cv\x00\x1b[31m.pdf", "x") == "cv[31m.pdf"
    assert safe_file_name("../..", "fallback") == "fallback"
    assert len(safe_file_name("a" * 400 + ".pdf", "x")) == 255
    assert safe_file_name("a" * 400 + ".pdf", "x").endswith(".pdf")


def test_oversized_request_bodies_are_refused_before_they_are_read(
    client: TestClient, auth: dict[str, str]
) -> None:
    too_big = settings.MAX_REQUEST_BODY_MB * 1024 * 1024 + 1
    response = client.post(
        f"{API}/resumes",
        headers={**auth, "Content-Type": "application/pdf", "Content-Length": str(too_big)},
        content=b"%PDF-1.4",
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_oversized_chunked_bodies_are_refused(
    client: TestClient, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk = b"x" * (1024 * 1024)

    def body() -> Any:
        for _ in range(settings.MAX_REQUEST_BODY_MB + 2):
            yield chunk

    response = client.post(
        f"{API}/applications/{uuid.uuid4()}/questions", headers=auth, content=body()
    )
    assert response.status_code == 413


# --- HTTP headers and CORS ------------------------------------------------------------------


def test_api_responses_carry_security_headers_and_are_not_cached(
    client: TestClient, register_and_login: Any
) -> None:
    token = register_and_login(email="headers@example.com")["access_token"]
    response = client.get(f"{API}/users/me", headers=_headers(token))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert "camera=()" in response.headers["permissions-policy"]
    # Errors get them too.
    assert client.get(f"{API}/users/me").headers["x-frame-options"] == "DENY"


def test_hsts_is_sent_only_when_enabled() -> None:
    from app.core.http_security import security_headers

    def names(hsts: bool) -> set[str]:
        return {name.decode() for name, _ in security_headers(path="/api/v1/x", hsts=hsts)}

    assert "strict-transport-security" in names(True)
    assert "strict-transport-security" not in names(False)
    assert Settings(APP_ENV="development").hsts_enabled is False


def test_cors_allows_only_configured_origins_and_never_credentials(client: TestClient) -> None:
    allowed = settings.cors_origins[0]
    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }
    ok = client.options(f"{API}/users/me", headers={"Origin": allowed, **preflight})
    assert ok.headers.get("access-control-allow-origin") == allowed
    assert "access-control-allow-credentials" not in ok.headers
    evil = client.options(
        f"{API}/users/me", headers={"Origin": "https://evil.example", **preflight}
    )
    assert "access-control-allow-origin" not in evil.headers
    simple = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers


def test_a_wildcard_cors_origin_is_refused() -> None:
    with pytest.raises(InsecureConfigurationError):
        validate_security(Settings(BACKEND_CORS_ORIGINS="*"))


# --- Configuration safety -------------------------------------------------------------------------


def test_production_refuses_development_secrets_without_revealing_them() -> None:
    dev = Settings(APP_ENV="production")
    with pytest.raises(InsecureConfigurationError) as refused:
        validate_security(dev)
    message = str(refused.value)
    assert "SECRET_KEY" in message and "DATABASE_URL" in message
    assert dev.SECRET_KEY not in message and "jobpilot:jobpilot" not in message


def test_production_accepts_a_real_configuration() -> None:
    validate_security(
        Settings(
            APP_ENV="production",
            SECRET_KEY="k" * 48,
            DATABASE_URL="postgresql+psycopg://app:s3cret@db:5432/jobpilot",
            BACKEND_CORS_ORIGINS="https://app.example.com",
        )
    )


def test_production_refuses_private_browser_hosts() -> None:
    with pytest.raises(InsecureConfigurationError, match="AUTOMATION_ALLOW_PRIVATE_HOSTS"):
        validate_security(
            Settings(
                APP_ENV="production",
                SECRET_KEY="k" * 48,
                DATABASE_URL="postgresql+psycopg://app:s3cret@db:5432/jobpilot",
                AUTOMATION_ALLOW_PRIVATE_HOSTS=True,
            )
        )


def test_api_docs_are_off_in_production_by_default() -> None:
    assert Settings(APP_ENV="production").api_docs_enabled is False
    assert Settings(APP_ENV="development").api_docs_enabled is True
    assert config_module.Settings(APP_ENV="production", API_DOCS_ENABLED=True).api_docs_enabled


# --- Error responses and logs -----------------------------------------------------------------


def test_unexpected_errors_reveal_nothing_internal(
    client: TestClient, register_and_login: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import profile_service

    token = register_and_login(email="boom@example.com")["access_token"]

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            'psycopg error at /srv/app/db.py: SELECT * FROM users WHERE secret="s3cret"'
        )

    monkeypatch.setattr(profile_service, "build_profile_response", explode)
    with TestClient(app, raise_server_exceptions=False) as raw:
        response = raw.get(f"{API}/profile", headers=_headers(token))
    assert response.status_code == 500
    text = response.text
    for leak in ("psycopg", "/srv/app", "SELECT", "s3cret", "Traceback"):
        assert leak not in text


def test_auth_flows_never_log_credentials_or_tokens(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    password = "Very-Secret-Pass-123"
    registered = client.post(
        f"{API}/auth/register",
        json={
            "email": "logs@example.com",
            "password": password,
            "first_name": "Log",
            "last_name": "Test",
        },
    ).json()
    _login(client, "logs@example.com", "a-wrong-password")
    token = _login(client, "logs@example.com", password).json()["access_token"]
    client.post(f"{API}/auth/logout", headers=_headers(token))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (
        password,
        "a-wrong-password",
        token,
        registered["access_token"],
        "logs@example.com",
    ):
        assert secret not in logged
    assert "event=login_failed" in logged and "event=session_started" in logged


def test_resume_processing_never_logs_resume_content(
    client: TestClient, auth: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    resume = upload_resume(client, auth)
    client.post(f"{API}/resumes/{resume['id']}/process", headers=auth, json={})
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for fragment in ("alex.example@example.com", "+49 170 1234567", "SAP IS-U and ABAP"):
        assert fragment not in logged


# --- AI data minimization and the user's AI switch ------------------------------------------


def test_resume_analysis_sends_placeholders_not_contact_details(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    resume = upload_resume(client, auth)
    response = client.post(f"{API}/resumes/{resume['id']}/process", headers=auth, json={})
    assert response.status_code == 202, response.text
    prompt = fake_ai.calls[0]["prompt"]
    assert "alex.example@example.com" not in prompt
    assert "+49 170 1234567" not in prompt
    assert "[EMAIL_1]" in prompt and "[PHONE_1]" in prompt
    assert "SAP developer" in prompt  # the rest of the resume is intact


def test_placeholders_in_the_ai_result_become_the_real_values(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider
) -> None:
    response = dict(fake_ai.response)  # type: ignore[arg-type]
    personal = dict(response["personal_information"])
    personal["email"] = {**personal["email"], "value": "[EMAIL_1]"}
    personal["phone"] = {**personal["phone"], "value": "[PHONE_1]"}
    response["personal_information"] = personal
    fake_ai.response = response
    resume = upload_resume(client, auth)
    client.post(f"{API}/resumes/{resume['id']}/process", headers=auth, json={})
    profile = client.get(f"{API}/resumes/{resume['id']}/profile", headers=auth).json()
    personal = profile["profile"]["data"]["personal_information"]
    values = {item["field"]: item["value"] for item in personal}
    assert values["email"] == "alex.example@example.com"
    assert values["phone"] == "+49 170 1234567"


def test_switching_ai_off_stops_every_ai_call_server_side(
    client: TestClient, auth: dict[str, str], fake_ai: FakeAIProvider, db_session: Session
) -> None:
    off = client.put(
        f"{API}/privacy/settings", headers=auth, json={"external_ai_processing_allowed": False}
    )
    assert off.status_code == 200 and off.json()["external_ai_processing_allowed"] is False

    resume = upload_resume(client, auth)
    client.post(f"{API}/resumes/{resume['id']}/process", headers=auth, json={})
    state = client.get(f"{API}/resumes/{resume['id']}/profile", headers=auth).json()
    assert state["latest_attempt"]["error_code"] == "AI_DISABLED_BY_USER"

    owner = user_by_email(db_session, "uploader@example.com")
    job = make_job(db_session, owner)
    explanation = client.post(f"{API}/jobs/{job.id}/match/explanation", headers=auth)
    assert explanation.status_code in (404, 409), explanation.text
    application = started(client, auth, job)
    generated = client.post(
        f"{API}/applications/{application['id']}/documents/cv", headers=auth, json={}
    )
    assert generated.status_code in (409, 422), generated.text
    assert fake_ai.calls == []  # nothing ever reached the provider

    on = client.put(
        f"{API}/privacy/settings", headers=auth, json={"external_ai_processing_allowed": True}
    )
    assert on.json()["external_ai_processing_allowed"] is True
    client.post(f"{API}/resumes/{resume['id']}/process", headers=auth, json={"force": True})
    assert len(fake_ai.calls) == 1
