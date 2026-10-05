from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_profile_creates_empty_profile_for_new_user(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()

    response = client.get("/api/v1/profile", headers=_auth_headers(auth["access_token"]))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == auth["user"]["email"]
    assert body["profile"]["phone"] is None


def test_update_profile_persists_changes(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.put(
        "/api/v1/profile",
        headers=headers,
        json={
            "phone": "+49 123 4567",
            "location": "Berlin, Germany",
            "linkedin_url": "https://linkedin.com/in/jane",
            "current_position": "Software Engineer",
            "years_of_experience": 5,
            "first_name": "Janet",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Janet"
    assert body["profile"]["phone"] == "+49 123 4567"
    assert body["profile"]["years_of_experience"] == 5

    # Changes persist across requests.
    follow_up = client.get("/api/v1/profile", headers=headers)
    assert follow_up.json()["profile"]["location"] == "Berlin, Germany"


def test_update_profile_requires_authentication(client: TestClient) -> None:
    response = client.put("/api/v1/profile", json={"phone": "123"})
    assert response.status_code == 401
