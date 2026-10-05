from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_preferences_creates_empty_record(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()

    response = client.get("/api/v1/preferences", headers=_auth_headers(auth["access_token"]))

    assert response.status_code == 200
    body = response.json()
    assert body["desired_job_titles"] == []
    assert body["relocation_willingness"] is False


def test_update_preferences_persists_lists_and_scalars(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    payload = {
        "desired_job_titles": ["Backend Engineer", "Platform Engineer"],
        "preferred_locations": ["Berlin", "Remote"],
        "employment_types": ["full_time", "contract"],
        "industries": ["Software"],
        "languages": [{"language": "English", "proficiency": "native"}],
        "minimum_salary": 60000,
        "desired_salary": 75000,
        "salary_currency": "EUR",
        "seniority": "senior",
        "remote_preference": "hybrid",
        "relocation_willingness": True,
        "travel_willingness": "occasional",
    }

    response = client.put("/api/v1/preferences", headers=headers, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert sorted(body["desired_job_titles"]) == sorted(payload["desired_job_titles"])
    assert sorted(body["preferred_locations"]) == sorted(payload["preferred_locations"])
    assert body["seniority"] == "senior"
    assert body["relocation_willingness"] is True

    follow_up = client.get("/api/v1/preferences", headers=headers)
    assert sorted(follow_up.json()["employment_types"]) == sorted(["full_time", "contract"])


def test_update_preferences_replaces_previous_lists(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    client.put(
        "/api/v1/preferences",
        headers=headers,
        json={"desired_job_titles": ["Backend Engineer"]},
    )
    response = client.put(
        "/api/v1/preferences",
        headers=headers,
        json={"desired_job_titles": ["Frontend Engineer"]},
    )

    assert response.json()["desired_job_titles"] == ["Frontend Engineer"]


def test_preferences_require_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/preferences")
    assert response.status_code == 401
