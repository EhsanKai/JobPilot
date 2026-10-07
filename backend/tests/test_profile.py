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
    assert body["phone"] is None
    assert body["current_job_title"] is None
    assert body["industries"] == []


def test_update_profile_persists_personal_and_professional_fields(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.put(
        "/api/v1/profile",
        headers=headers,
        json={
            "phone": "+49 123 4567",
            "location": "Berlin, Germany",
            "linkedin_url": "https://linkedin.com/in/jane",
            "current_job_title": "Software Engineer",
            "years_of_experience": 5,
            "seniority_level": "senior",
            "industries": ["Software", "Fintech"],
            "first_name": "Janet",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Janet"
    assert body["phone"] == "+49 123 4567"
    assert body["current_job_title"] == "Software Engineer"
    assert body["years_of_experience"] == 5
    assert body["seniority_level"] == "senior"
    assert sorted(body["industries"]) == ["Fintech", "Software"]

    # Changes persist across requests.
    follow_up = client.get("/api/v1/profile", headers=headers)
    assert follow_up.json()["location"] == "Berlin, Germany"


def test_update_profile_rejects_invalid_linkedin_url(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.put(
        "/api/v1/profile", headers=headers, json={"linkedin_url": "not-a-url"}
    )

    assert response.status_code == 422
    assert "linkedin_url must be a valid absolute URL" in response.json()["error"]["message"]


def test_update_profile_rejects_negative_years_of_experience(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.put(
        "/api/v1/profile", headers=headers, json={"years_of_experience": -1}
    )

    assert response.status_code == 422


def test_update_profile_replaces_industries(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    client.put("/api/v1/profile", headers=headers, json={"industries": ["Software"]})
    response = client.put("/api/v1/profile", headers=headers, json={"industries": ["Healthcare"]})

    assert response.json()["industries"] == ["Healthcare"]


def test_update_profile_requires_authentication(client: TestClient) -> None:
    response = client.put("/api/v1/profile", json={"phone": "123"})
    assert response.status_code == 401


def test_profile_completeness_increases_as_fields_are_filled(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    empty = client.get("/api/v1/profile/completeness", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["percentage"] == 0

    client.put(
        "/api/v1/profile",
        headers=headers,
        json={
            "phone": "+49 123",
            "location": "Berlin",
            "linkedin_url": "https://linkedin.com/in/jane",
            "current_job_title": "Engineer",
            "years_of_experience": 5,
            "seniority_level": "senior",
        },
    )

    improved = client.get("/api/v1/profile/completeness", headers=headers)
    assert improved.json()["percentage"] > empty.json()["percentage"]
    assert len(improved.json()["categories"]) == 8


def test_profile_completeness_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/profile/completeness")
    assert response.status_code == 401
