import io

from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _pdf_file(name: str = "resume.pdf") -> tuple[str, io.BytesIO, str]:
    return (name, io.BytesIO(b"%PDF-1.4 fake pdf content"), "application/pdf")


def test_upload_resume_succeeds_for_valid_pdf(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    name, content, mime = _pdf_file()
    response = client.post(
        "/api/v1/resumes", headers=headers, files={"file": (name, content, mime)}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["file_name"] == "resume.pdf"
    assert body["status"] == "uploaded"


def test_upload_rejects_unsupported_file_type(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.post(
        "/api/v1/resumes",
        headers=headers,
        files={"file": ("resume.txt", io.BytesIO(b"plain text"), "text/plain")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_upload_rejects_file_too_large(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    oversized = io.BytesIO(b"0" * (11 * 1024 * 1024))
    response = client.post(
        "/api/v1/resumes",
        headers=headers,
        files={"file": ("resume.pdf", oversized, "application/pdf")},
    )

    assert response.status_code == 422


def test_uploading_new_resume_replaces_previous_one(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    client.post(
        "/api/v1/resumes", headers=headers, files={"file": _pdf_file("first.pdf")}
    )
    second = client.post(
        "/api/v1/resumes", headers=headers, files={"file": _pdf_file("second.pdf")}
    )

    current = client.get("/api/v1/resumes/current", headers=headers)
    assert current.json()["file_name"] == "second.pdf"
    assert current.json()["id"] == second.json()["id"]


def test_delete_resume_removes_it(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    upload = client.post(
        "/api/v1/resumes", headers=headers, files={"file": _pdf_file()}
    )
    resume_id = upload.json()["id"]

    delete_response = client.delete(f"/api/v1/resumes/{resume_id}", headers=headers)
    assert delete_response.status_code == 204

    current = client.get("/api/v1/resumes/current", headers=headers)
    assert current.json() is None


def test_resume_upload_requires_authentication(client: TestClient) -> None:
    response = client.post("/api/v1/resumes", files={"file": _pdf_file()})
    assert response.status_code == 401
