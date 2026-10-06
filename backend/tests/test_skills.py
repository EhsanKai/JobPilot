from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_add_list_update_delete_skill(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    created = client.post(
        "/api/v1/profile/skills",
        headers=headers,
        json={"name": "SAP ABAP", "proficiency": "advanced", "years_of_experience": 5},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "SAP ABAP"
    assert body["proficiency"] == "advanced"
    skill_id = body["id"]

    listed = client.get("/api/v1/profile/skills", headers=headers)
    assert len(listed.json()) == 1

    updated = client.put(
        f"/api/v1/profile/skills/{skill_id}",
        headers=headers,
        json={"proficiency": "expert", "years_of_experience": 6},
    )
    assert updated.status_code == 200
    assert updated.json()["proficiency"] == "expert"

    deleted = client.delete(f"/api/v1/profile/skills/{skill_id}", headers=headers)
    assert deleted.status_code == 204

    listed_after = client.get("/api/v1/profile/skills", headers=headers)
    assert listed_after.json() == []


def test_adding_same_skill_twice_updates_it_instead_of_duplicating(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    client.post("/api/v1/profile/skills", headers=headers, json={"name": "Python"})
    client.post(
        "/api/v1/profile/skills",
        headers=headers,
        json={"name": "Python", "proficiency": "expert"},
    )

    listed = client.get("/api/v1/profile/skills", headers=headers)
    assert len(listed.json()) == 1
    assert listed.json()[0]["proficiency"] == "expert"


def test_skill_name_is_shared_across_users_via_catalog(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    first = register_and_login(email="alice.skills@example.com")
    second = register_and_login(email="bob.skills@example.com")

    client.post(
        "/api/v1/profile/skills",
        headers=_auth_headers(first["access_token"]),
        json={"name": "SQL"},
    )
    response = client.post(
        "/api/v1/profile/skills",
        headers=_auth_headers(second["access_token"]),
        json={"name": "SQL"},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "SQL"


def test_update_nonexistent_skill_returns_404(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.put(
        "/api/v1/profile/skills/00000000-0000-0000-0000-000000000000",
        headers=headers,
        json={"proficiency": "expert"},
    )
    assert response.status_code == 404


def test_skill_years_of_experience_must_be_non_negative(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.post(
        "/api/v1/profile/skills",
        headers=headers,
        json={"name": "Python", "years_of_experience": -1},
    )
    assert response.status_code == 422


def test_skills_require_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/profile/skills")
    assert response.status_code == 401
