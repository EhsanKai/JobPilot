from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_preferences_creates_empty_record(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()

    response = client.get("/api/v1/preferences", headers=_auth_headers(auth["access_token"]))

    assert response.status_code == 200
    body = response.json()
    assert body["desired_job_titles"] == []
    assert body["work_models"] == []
    assert body["deal_breakers"] == []
    assert body["relocation"] is None


def test_update_preferences_persists_lists_and_scalars(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    payload = {
        "desired_job_titles": [
            {"title": "Backend Engineer", "priority": "primary"},
            {"title": "Platform Engineer", "priority": "secondary"},
        ],
        "preferred_locations": [{"location": "Berlin"}, {"location": "Remote", "radius_km": 50}],
        "employment_types": ["full_time", "contract"],
        "preferred_industries": ["Software"],
        "work_models": ["hybrid", "remote"],
        "minimum_salary": 60000,
        "desired_salary": 75000,
        "salary_currency": "EUR",
        "relocation": "maybe",
        "travel_willingness": "occasional",
        "deal_breakers": [
            {"type": "minimum_salary", "enabled": True},
            {"type": "remote_requirement", "enabled": False},
        ],
    }

    response = client.put("/api/v1/preferences", headers=headers, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert sorted(jt["title"] for jt in body["desired_job_titles"]) == [
        "Backend Engineer",
        "Platform Engineer",
    ]
    primary = next(jt for jt in body["desired_job_titles"] if jt["title"] == "Backend Engineer")
    assert primary["priority"] == "primary"
    assert sorted(loc["location"] for loc in body["preferred_locations"]) == ["Berlin", "Remote"]
    remote_loc = next(loc for loc in body["preferred_locations"] if loc["location"] == "Remote")
    assert remote_loc["radius_km"] == 50
    assert body["relocation"] == "maybe"
    assert sorted(body["work_models"]) == sorted(["hybrid", "remote"])
    assert len(body["deal_breakers"]) == 2

    follow_up = client.get("/api/v1/preferences", headers=headers)
    assert sorted(follow_up.json()["employment_types"]) == sorted(["full_time", "contract"])
    assert follow_up.json()["preferred_industries"] == ["Software"]


def test_update_preferences_replaces_previous_lists(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    client.put(
        "/api/v1/preferences",
        headers=headers,
        json={"desired_job_titles": [{"title": "Backend Engineer"}]},
    )
    response = client.put(
        "/api/v1/preferences",
        headers=headers,
        json={"desired_job_titles": [{"title": "Frontend Engineer"}]},
    )

    assert [jt["title"] for jt in response.json()["desired_job_titles"]] == ["Frontend Engineer"]


def test_update_preferences_rejects_desired_salary_below_minimum(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.put(
        "/api/v1/preferences",
        headers=headers,
        json={"minimum_salary": 70000, "desired_salary": 50000},
    )

    assert response.status_code == 422
    assert "desired_salary must be greater than or equal to minimum_salary" in (
        response.json()["error"]["message"]
    )


def test_update_preferences_rejects_invalid_employment_type(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.put(
        "/api/v1/preferences", headers=headers, json={"employment_types": ["not_a_type"]}
    )

    assert response.status_code == 422


def test_update_preferences_rejects_invalid_remote_preference(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.put(
        "/api/v1/preferences", headers=headers, json={"work_models": ["invalid_value"]}
    )

    assert response.status_code == 422


def test_deal_breakers_persist_and_can_be_cleared(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    client.put(
        "/api/v1/preferences",
        headers=headers,
        json={"deal_breakers": [{"type": "relocation", "enabled": True}]},
    )
    response = client.put("/api/v1/preferences", headers=headers, json={"deal_breakers": []})

    assert response.json()["deal_breakers"] == []


def test_preferences_require_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/preferences")
    assert response.status_code == 401


def test_add_and_delete_job_title_via_granular_endpoint(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    created = client.post(
        "/api/v1/preferences/job-titles",
        headers=headers,
        json={"title": "SAP ABAP Developer", "priority": "primary"},
    )
    assert created.status_code == 201
    job_title_id = created.json()["id"]

    listed = client.get("/api/v1/preferences/job-titles", headers=headers)
    assert len(listed.json()) == 1

    deleted = client.delete(
        f"/api/v1/preferences/job-titles/{job_title_id}", headers=headers
    )
    assert deleted.status_code == 204

    listed_after = client.get("/api/v1/preferences/job-titles", headers=headers)
    assert listed_after.json() == []


def test_delete_nonexistent_job_title_returns_404(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    response = client.delete(
        "/api/v1/preferences/job-titles/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert response.status_code == 404


def test_add_and_delete_location_via_granular_endpoint(
    client: TestClient, register_and_login
) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login()
    headers = _auth_headers(auth["access_token"])

    created = client.post(
        "/api/v1/preferences/locations", headers=headers, json={"location": "Cologne"}
    )
    assert created.status_code == 201
    location_id = created.json()["id"]

    deleted = client.delete(f"/api/v1/preferences/locations/{location_id}", headers=headers)
    assert deleted.status_code == 204

    listed_after = client.get("/api/v1/preferences/locations", headers=headers)
    assert listed_after.json() == []
