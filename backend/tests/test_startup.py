"""Release-blocking regression (V0.13): a fresh database must be usable after startup.

The manual test environment failed with `relation "users" does not exist`: the backend
connected to an empty PostgreSQL database that nobody had migrated. Since V0.13 the
container runs `python -m app.db.migrate` before the API starts. These tests take a
completely empty database through the same steps as `docker compose up`:

    empty database -> (login fails exactly like the reported bug)
    -> migrate -> readiness OK -> seed -> login as the manual-test user -> dashboard data

and check the startup step's failure behaviour. The Docker-level version of this test is
`./start-dev.sh smoke` (docs/docker-development.md).
"""

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import app.main as main_module
from app.core.config import settings
from app.db import migrate
from app.db.session import get_db, get_session_factory
from app.main import app
from app.schemas.auth import RegisterRequest
from app.services.storage.factory import get_file_storage
from app.services.storage.local import LocalFileStorage
from scripts import seed_manual_test_data as seed
from tests.conftest import TEST_DATABASE_URL
from tests.test_migrations import create_scratch_database


@pytest.fixture
def fresh_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Engine, None, None]:
    url = make_url(TEST_DATABASE_URL)
    name = f"{url.database}_fresh"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        create_scratch_database(admin, name)
    except SQLAlchemyError as exc:
        admin.dispose()
        pytest.skip(f"cannot create a scratch database: {exc.orig}")
    target = url.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setattr(settings, "DATABASE_URL", target)  # alembic/env.py reads it
    engine = create_engine(target)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def api_on(fresh_db: Engine, tmp_path: Any) -> Generator[TestClient, None, None]:
    sessions = sessionmaker(bind=fresh_db, autocommit=False, autoflush=False)

    def db() -> Generator[Session, None, None]:
        with sessions() as session:
            yield session

    storage = LocalFileStorage(str(tmp_path / "storage"))
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_session_factory] = lambda: sessions
    app.dependency_overrides[get_file_storage] = lambda: storage
    original_engine = main_module.engine
    main_module.engine = fresh_db  # the readiness check looks at this database
    try:
        with TestClient(app) as client:
            yield client
    finally:
        main_module.engine = original_engine
        app.dependency_overrides.clear()


def _login(client: TestClient) -> Any:
    return client.post(
        "/api/v1/auth/login", json={"email": seed.TEST_EMAIL, "password": seed.TEST_PASSWORD}
    )


def test_the_manual_test_account_passes_real_email_validation() -> None:
    request = RegisterRequest(
        email=seed.TEST_EMAIL, password=seed.TEST_PASSWORD, first_name="Max", last_name="Mustermann"
    )
    assert request.email == "test.user@example.com"
    # The address the environment used before is a special-use name: still rejected.
    with pytest.raises(ValidationError, match="special-use or reserved"):
        RegisterRequest(
            email="test.user@example.local", password="x" * 12, first_name="A", last_name="B"
        )


def test_an_unmigrated_database_fails_like_the_reported_bug(fresh_db: Engine) -> None:
    with fresh_db.connect() as connection, pytest.raises(ProgrammingError) as missing:
        connection.execute(text("SELECT id FROM users"))
    assert 'relation "users" does not exist' in str(missing.value)


def test_readiness_reports_an_unmigrated_database(api_on: TestClient) -> None:
    assert api_on.get("/health").status_code == 200  # the process is alive ...
    ready = api_on.get("/health/ready")
    assert ready.status_code == 503  # ... but not ready to serve
    assert ready.json() == {"status": "unavailable", "database": "not_migrated"}


def test_startup_migrates_a_fresh_database_and_the_manual_flow_works(
    api_on: TestClient, fresh_db: Engine, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. The container's startup step.
    assert migrate.main(["--wait-seconds", "10"]) == 0
    assert "users" in inspect(fresh_db).get_table_names()
    assert migrate.schema_is_current(fresh_db)
    assert migrate.main(["--check", "--wait-seconds", "5"]) == 0
    assert migrate.main(["--wait-seconds", "5"]) == 0  # idempotent on a migrated database
    assert api_on.get("/health/ready").json() == {"status": "ok", "database": "ok"}

    # 2. The seed, against the same database and storage.
    sessions = sessionmaker(bind=fresh_db, autocommit=False, autoflush=False)
    monkeypatch.setattr(seed, "SessionLocal", sessions)
    monkeypatch.setattr(seed, "engine", fresh_db)
    storage = LocalFileStorage(str(tmp_path / "seed-storage"))
    monkeypatch.setattr(seed, "get_file_storage", lambda: storage)
    assert seed.main([]) == 0
    assert seed.main([]) == 0  # idempotent: nothing changes
    with fresh_db.connect() as connection:
        users = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
    assert users == 1

    # 3. Login with the documented credentials, then the dashboard's data.
    response = _login(api_on)
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert api_on.get("/api/v1/users/me", headers=headers).json()["email"] == seed.TEST_EMAIL
    summary = api_on.get("/api/v1/applications/summary", headers=headers).json()
    assert summary["total"] == 5
    assert summary["counts"]["INTERVIEW"] == 1 and summary["counts"]["REJECTED"] == 1
    jobs = api_on.get("/api/v1/jobs?page=1&page_size=50", headers=headers).json()
    assert jobs["total"] == len(seed.JOBS)
    first = api_on.get("/api/v1/applications", headers=headers).json()["items"][0]["id"]
    timeline = api_on.get(f"/api/v1/applications/{first}/timeline", headers=headers)
    assert timeline.status_code == 200

    # 4. --reset recreates the test user through the real account deletion.
    assert seed.main(["--reset"]) == 0
    assert api_on.get("/api/v1/users/me", headers=headers).status_code == 401  # old session gone
    assert _login(api_on).status_code == 200


def test_the_seed_refuses_an_unmigrated_database(
    fresh_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seed, "engine", fresh_db)
    assert seed.main([]) == 1


def test_the_seed_refuses_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert seed.main([]) == 2


def test_an_unreachable_database_fails_startup_clearly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        settings, "DATABASE_URL", "postgresql+psycopg://jobpilot:secret-pw@127.0.0.1:1/none"
    )
    assert migrate.main(["--wait-seconds", "1"]) == 1
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "event=migration_failed" in logged
    assert "secret-pw" not in logged


def test_the_schema_check_fails_on_an_unmigrated_database(fresh_db: Engine) -> None:
    assert migrate.main(["--check", "--wait-seconds", "5"]) == 1


def test_a_failing_migration_stops_startup(
    fresh_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("migration script error")

    monkeypatch.setattr(migrate.command, "upgrade", broken)
    assert migrate.main(["--wait-seconds", "5"]) == 1
    assert not migrate.schema_is_current(fresh_db)
