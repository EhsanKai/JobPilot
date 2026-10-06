"""Alembic migrations against a real, separate database.

    V0 data    -> upgrade head                       (V0 -> ... -> V0.4)
    V0.3 data  -> upgrade -> downgrade -> upgrade    (V0.4 roundtrip)
    V0.4 merged data -> downgrade                    (merged jobs split back, nothing lost)

The test suite itself builds its schema with `create_all`; these tests make
sure the migrations produce the same schema and preserve existing data. They
need permission to create a database and are skipped (with the reason)
when it is missing.
"""

import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.core.config import settings
from app.db.base import Base
from tests.conftest import TEST_DATABASE_URL

BACKEND = Path(__file__).resolve().parents[1]
V0 = "93304613c2dc"
V03 = "c3f9d2e4a8b1"
V04 = "d4a7e3b9c2f1"

USER_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
GH_JOB = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
LEVER_JOB = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
LEGACY_JOB = uuid.UUID("00000000-0000-4000-8000-0000000000b3")
APPLICATION_ID = uuid.UUID("00000000-0000-4000-8000-0000000000c1")


def create_scratch_database(admin: Engine, name: str, attempts: int = 5) -> None:
    """(Re)create an empty database. From template0: CREATE DATABASE fails
    while anything (e.g. autovacuum) is connected to its template, and
    template0 doesn't accept connections. Retried briefly anyway."""
    for attempt in range(attempts):
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
            return
        except OperationalError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)


@pytest.fixture
def migration_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Engine, None, None]:
    url = make_url(TEST_DATABASE_URL)
    name = f"{url.database}_migrations"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        create_scratch_database(admin, name)
    except SQLAlchemyError as exc:
        admin.dispose()
        pytest.skip(f"cannot create a scratch database for migration tests: {exc.orig}")
    target = url.set(database=name).render_as_string(hide_password=False)
    # alembic/env.py reads the URL from the app settings.
    monkeypatch.setattr(settings, "DATABASE_URL", target)
    engine = create_engine(target)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def alembic_config() -> Config:
    # No ini file: env.py would otherwise re-run logging.fileConfig and
    # disable the app's loggers for the rest of the test session.
    config = Config()
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return config


def rows(engine: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(r._mapping) for r in connection.execute(text(sql), params)]


def execute(engine: Engine, sql: str, **params: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text(sql), params)


def enum_values(engine: Engine) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in rows(
        engine,
        """
        SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
        ORDER BY t.typname, e.enumsortorder
        """,
    ):
        result.setdefault(row["typname"], []).append(row["enumlabel"])
    return result


def seed_user(engine: Engine) -> None:
    execute(
        engine,
        """
        INSERT INTO users (id, email, password_hash, first_name, last_name)
        VALUES (:id, 'jane@example.com', 'x', 'Jane', 'Doe')
        """,
        id=USER_ID,
    )


def seed_v03(engine: Engine) -> None:
    seed_user(engine)
    jobs = [
        (GH_JOB, "greenhouse", "4012345", "greenhouse:id:4012345", "acme",
         "https://job-boards.greenhouse.io/acme/jobs/4012345?utm_source=x",
         "https://job-boards.greenhouse.io/acme/jobs/4012345", "Senior SAP ABAP Developer",
         "Example GmbH", "Cologne, Germany", "FULL_TIME", "HYBRID", "SENIOR"),
        (LEVER_JOB, "lever", "abc-987", "lever:id:abc-987", "example",
         "https://jobs.lever.co/example/abc-987", "https://jobs.lever.co/example/abc-987",
         "Data Engineer", "Example GmbH", "Köln", "WORKING_STUDENT", None, None),
        (LEGACY_JOB, "manual", "dup", "legacy:" + str(LEGACY_JOB), None,
         "https://example.com/jobs/1", "https://example.com/jobs/1", "Legacy Job",
         "Legacy Corp", None, None, None, None),
    ]
    for (job_id, source, source_job_id, dedup_key, board, source_url, canonical_url, title,
         company, location, employment_type, work_model, seniority) in jobs:
        execute(
            engine,
            """
            INSERT INTO jobs (
                id, source, source_job_id, dedup_key, source_company_key, source_url,
                canonical_url, apply_url, title, company_name, location, locations,
                description, requirements, responsibilities, technologies,
                salary_min, salary_max, salary_currency, salary_period,
                employment_type, work_model, seniority, posted_at, source_updated_at,
                discovered_at, last_seen_at, raw_data, content_hash, normalizer_version,
                created_at, updated_at
            ) VALUES (
                :id, :source, :source_job_id, :dedup_key, :board, :source_url,
                :canonical_url, NULL, :title, :company, :location, '["Cologne"]'::jsonb,
                'We build SAP solutions.', '["ABAP"]'::jsonb, '["Build"]'::jsonb,
                '["SAP"]'::jsonb, 70000, 90000, 'EUR', 'YEAR',
                CAST(:employment_type AS employment_type),
                CAST(:work_model AS remote_preference),
                CAST(:seniority AS professional_seniority_level),
                '2026-09-20T08:00:00Z', '2026-09-28T10:00:00Z',
                '2026-10-01T10:00:00Z', '2026-10-03T10:00:00Z',
                '{"id": 1}'::jsonb, :content_hash, 'job_normalizer_v1',
                '2026-10-01T10:00:00Z', '2026-10-03T10:00:00Z'
            )
            """,
            id=job_id, source=source, source_job_id=source_job_id, dedup_key=dedup_key,
            board=board, source_url=source_url, canonical_url=canonical_url, title=title,
            company=company, location=location, employment_type=employment_type,
            work_model=work_model, seniority=seniority, content_hash=f"hash-{source}",
        )
    execute(
        engine,
        """
        INSERT INTO user_jobs (id, user_id, job_id, first_discovered_at, last_discovered_at,
                               saved_at)
        VALUES (gen_random_uuid(), :user, :job, '2026-10-01T10:00:00Z', '2026-10-03T10:00:00Z',
                '2026-10-02T12:00:00Z'),
               (gen_random_uuid(), :user, :lever, '2026-10-01T10:00:00Z',
                '2026-10-03T10:00:00Z', NULL)
        """,
        user=USER_ID, job=GH_JOB, lever=LEVER_JOB,
    )
    execute(
        engine,
        """
        INSERT INTO applications (id, user_id, job_id, status, created_at)
        VALUES (:id, :user, :job, 'SUBMITTED', '2026-10-02T13:00:00Z')
        """,
        id=APPLICATION_ID, user=USER_ID, job=GH_JOB,
    )


V03_JOB_COLUMNS = (
    "id, source, source_job_id, dedup_key, source_company_key, source_url, canonical_url, "
    "title, company_name, location, employment_type, work_model, seniority, salary_period, "
    "salary_min, discovered_at, last_seen_at, raw_data, content_hash, normalizer_version, "
    "source_updated_at, posted_at, created_at"
)


def v03_snapshot(engine: Engine) -> dict[str, Any]:
    return {
        "jobs": rows(engine, f"SELECT {V03_JOB_COLUMNS} FROM jobs ORDER BY id"),
        "user_jobs": rows(
            engine,
            "SELECT user_id, job_id, first_discovered_at, last_discovered_at, saved_at "
            "FROM user_jobs ORDER BY job_id",
        ),
        "applications": rows(
            engine, "SELECT id, user_id, job_id, status, created_at FROM applications"
        ),
    }


def assert_v04_state(engine: Engine, before: dict[str, Any]) -> None:
    jobs = {r["id"]: r for r in rows(engine, "SELECT * FROM jobs")}
    postings = rows(engine, "SELECT * FROM job_source_postings")
    assert set(jobs) == {j["id"] for j in before["jobs"]}  # same ids, nothing lost
    assert len(postings) == len(jobs)
    by_job = {p["job_id"]: p for p in postings}
    for old in before["jobs"]:
        job, posting = jobs[old["id"]], by_job[old["id"]]
        assert job["preferred_source_posting_id"] == posting["id"]
        for column in ("source", "source_job_id", "dedup_key", "source_url", "raw_data",
                       "content_hash", "normalizer_version", "source_updated_at", "posted_at"):
            assert posting[column] == old[column], column
        assert posting["source_company_key"] == old["source_company_key"]
        assert posting["canonical_source_url"] == old["canonical_url"] == job["canonical_url"]
        assert posting["first_seen_at"] == old["discovered_at"] == job["discovered_at"]
        assert posting["last_seen_at"] == old["last_seen_at"] == job["last_seen_at"]
        assert (posting["title"], posting["company_name"], posting["location"]) == (
            old["title"], old["company_name"], old["location"]
        )
        for column in ("title", "company_name", "location", "employment_type", "work_model",
                       "seniority", "salary_period", "salary_min", "created_at"):
            assert job[column] == old[column], column
        # Keys are computed by the application, never invented by SQL.
        assert posting["identity_version"] is None and posting["normalized_data"] is None
        assert posting["is_active"] is True
    assert rows(
        engine,
        "SELECT user_id, job_id, first_discovered_at, last_discovered_at, saved_at "
        "FROM user_jobs ORDER BY job_id",
    ) == before["user_jobs"]
    assert rows(
        engine, "SELECT id, user_id, job_id, status, created_at FROM applications"
    ) == before["applications"]


def test_v03_data_survives_upgrade_downgrade_upgrade(migration_db: Engine) -> None:
    config = alembic_config()
    command.upgrade(config, V03)
    seed_v03(migration_db)
    before = v03_snapshot(migration_db)
    enums = enum_values(migration_db)

    command.upgrade(config, "head")
    assert_v04_state(migration_db, before)
    assert enum_values(migration_db) == enums

    command.downgrade(config, "-1")
    assert v03_snapshot(migration_db) == before
    assert enum_values(migration_db) == enums
    assert not rows(
        migration_db, "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'job_source_postings'"
    )

    command.upgrade(config, "head")
    assert_v04_state(migration_db, before)
    assert enum_values(migration_db) == enums


def test_migrated_schema_matches_the_models(migration_db: Engine) -> None:
    command.upgrade(alembic_config(), "head")
    with migration_db.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_v0_database_upgrades_to_head_without_data_loss(migration_db: Engine) -> None:
    config = alembic_config()
    command.upgrade(config, V0)
    seed_user(migration_db)
    execute(
        migration_db,
        """
        INSERT INTO jobs (id, source, external_id, title, company, location, url, description,
                          raw_data)
        VALUES (:id, 'greenhouse', '42', 'Backend Engineer', 'Acme GmbH', 'Berlin',
                'https://job-boards.greenhouse.io/acme/jobs/42', 'Build things.',
                '{"id": 42}'::json)
        """,
        id=GH_JOB,
    )
    execute(
        migration_db,
        "INSERT INTO applications (id, user_id, job_id, status) "
        "VALUES (:id, :user, :job, 'DRAFT')",
        id=APPLICATION_ID, user=USER_ID, job=GH_JOB,
    )

    command.upgrade(config, "head")
    job = rows(migration_db, "SELECT * FROM jobs")[0]
    posting = rows(migration_db, "SELECT * FROM job_source_postings")[0]
    assert job["id"] == GH_JOB
    assert (job["title"], job["company_name"], job["description"]) == (
        "Backend Engineer", "Acme GmbH", "Build things."
    )
    assert job["preferred_source_posting_id"] == posting["id"]
    assert (posting["source"], posting["source_job_id"], posting["dedup_key"]) == (
        "greenhouse", "42", "greenhouse:id:42"
    )
    assert posting["source_url"] == "https://job-boards.greenhouse.io/acme/jobs/42"
    assert posting["raw_data"] == {"id": 42}
    application = rows(migration_db, "SELECT job_id, status FROM applications")[0]
    assert application == {"job_id": GH_JOB, "status": "DRAFT"}


def test_downgrade_splits_merged_jobs_without_losing_postings(migration_db: Engine) -> None:
    config = alembic_config()
    command.upgrade(config, V03)
    seed_v03(migration_db)
    command.upgrade(config, "head")

    # V0.4 merged the Lever posting into the Greenhouse job (and the now
    # posting-less Lever job is gone -- as if discovered by V0.4 directly).
    lever = rows(
        migration_db, "SELECT id FROM job_source_postings WHERE job_id = :j", j=LEVER_JOB
    )[0]["id"]
    execute(
        migration_db,
        "UPDATE job_source_postings SET job_id = :gh, normalized_data = :data WHERE id = :p",
        gh=GH_JOB, p=lever,
        data='{"description": "Lever text.", "locations": ["Köln"], '
             '"requirements": [], "responsibilities": [], "technologies": []}',
    )
    execute(migration_db, "DELETE FROM user_jobs WHERE job_id = :j", j=LEVER_JOB)
    execute(migration_db, "DELETE FROM jobs WHERE id = :j", j=LEVER_JOB)

    command.downgrade(config, "-1")
    jobs = {r["source"]: r for r in rows(migration_db, f"SELECT {V03_JOB_COLUMNS}, "
                                         "description FROM jobs")}
    assert set(jobs) == {"greenhouse", "lever", "manual"}
    # The canonical job keeps its id, preferred (Greenhouse) identity, saved
    # state and application.
    assert jobs["greenhouse"]["id"] == GH_JOB
    assert jobs["greenhouse"]["dedup_key"] == "greenhouse:id:4012345"
    split = jobs["lever"]
    assert split["id"] not in (GH_JOB, LEVER_JOB)
    assert split["dedup_key"] == "lever:id:abc-987"
    assert split["source_url"] == "https://jobs.lever.co/example/abc-987"
    assert split["location"] == "Köln"
    assert split["description"] == "Lever text."
    user_jobs = {r["job_id"]: r for r in rows(migration_db, "SELECT * FROM user_jobs")}
    assert user_jobs[GH_JOB]["saved_at"] is not None
    assert user_jobs[split["id"]]["saved_at"] is None  # visible, but not saved
    assert rows(migration_db, "SELECT job_id FROM applications") == [{"job_id": GH_JOB}]

    command.upgrade(config, "head")
    assert len(rows(migration_db, "SELECT id FROM job_source_postings")) == 3
