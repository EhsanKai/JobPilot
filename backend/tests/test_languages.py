from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_add_list_delete_language(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    created = client.post(
        "/api/v1/profile/languages",
        headers=headers,
        json={"name": "German", "proficiency": "professional"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "German"
    language_id = body["id"]

    listed = client.get("/api/v1/profile/languages", headers=headers)
    assert len(listed.json()) == 1

    deleted = client.delete(f"/api/v1/profile/languages/{language_id}", headers=headers)
    assert deleted.status_code == 204

    listed_after = client.get("/api/v1/profile/languages", headers=headers)
    assert listed_after.json() == []


def test_adding_same_language_twice_is_rejected(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    client.post(
        "/api/v1/profile/languages",
        headers=headers,
        json={"name": "English", "proficiency": "fluent"},
    )
    response = client.post(
        "/api/v1/profile/languages",
        headers=headers,
        json={"name": "English", "proficiency": "native"},
    )

    assert response.status_code == 409


def test_add_language_requires_valid_proficiency(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.post(
        "/api/v1/profile/languages",
        headers=headers,
        json={"name": "French", "proficiency": "not_a_level"},
    )
    assert response.status_code == 422


def test_delete_nonexistent_language_returns_404(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.delete(
        "/api/v1/profile/languages/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert response.status_code == 404


def test_languages_require_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/profile/languages")
    assert response.status_code == 401
