import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://jobpilot:jobpilot@localhost:5432/jobpilot_test"
)
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("STORAGE_PROVIDER", "local")

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.storage.base import FileStorage  # noqa: E402
from app.services.storage.factory import get_file_storage  # noqa: E402
from app.services.storage.local import LocalFileStorage  # noqa: E402

TEST_DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> Generator[None, None, None]:
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables() -> Generator[None, None, None]:
    yield
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def file_storage(tmp_path) -> FileStorage:  # type: ignore[no-untyped-def]
    return LocalFileStorage(str(tmp_path / "resumes"))


@pytest.fixture
def client(db_session: Session, file_storage: FileStorage) -> Generator[TestClient, None, None]:
    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def _override_get_file_storage() -> FileStorage:
        return file_storage

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_file_storage] = _override_get_file_storage
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def register_and_login(client: TestClient):  # type: ignore[no-untyped-def]
    def _register(
        email: str = "jane@example.com",
        password: str = "supersecret123",
        first_name: str = "Jane",
        last_name: str = "Doe",
    ) -> dict:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "first_name": first_name,
                "last_name": last_name,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _register
