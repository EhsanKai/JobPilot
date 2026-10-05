from fastapi.testclient import TestClient


def test_register_creates_user_and_returns_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "supersecret123",
            "first_name": "Alice",
            "last_name": "Smith",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["access_token"]
    assert body["user"]["email"] == "alice@example.com"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]


def test_register_rejects_duplicate_email(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    register_and_login(email="dupe@example.com")

    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "dupe@example.com",
            "password": "supersecret123",
            "first_name": "Dup",
            "last_name": "User",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_login_with_correct_credentials_succeeds(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    register_and_login(email="bob@example.com", password="correct-password")

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "correct-password"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_login_with_wrong_password_is_rejected(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    register_and_login(email="carol@example.com", password="correct-password")

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_protected_route_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_protected_route_rejects_invalid_token(client: TestClient) -> None:
    response = client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_protected_route_accepts_valid_token(client: TestClient, register_and_login) -> None:  # type: ignore[no-untyped-def]
    auth = register_and_login(email="dan@example.com")

    response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {auth['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "dan@example.com"
