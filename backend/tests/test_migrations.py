"""Alembic migrations against a real, separate database.

    V0 data    -> upgrade head                       (V0 -> ... -> V0.6)
    V0.3 data  -> upgrade -> downgrade -> upgrade    (V0.4 roundtrip)
    V0.4 merged data -> downgrade                    (merged jobs split back, nothing lost)
    V0.4.1 data -> upgrade -> downgrade -> upgrade   (V0.5.0 roundtrip, create-only)
    V0.5 data  -> upgrade -> downgrade -> upgrade    (V0.6 roundtrip, additive)
    V0.6 data  -> upgrade -> downgrade -> upgrade    (V0.7 roundtrip, application workspace)
    V0 data    -> upgrade head                       (V0 application gets its UserJob + context)

The test suite itself builds its schema with `create_all`; these tests make
sure the migrations produce the same schema and preserve existing data. They
need permission to create a database and are skipped (with the reason)
when it is missing.
"""

import time
import uuid
from collections.abc import Generator
from datetime import date
from decimal import Decimal
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
from app.schemas.application_context import ApplicationContextDocument
from app.services.applications.context import hash_document
from tests.conftest import TEST_DATABASE_URL

BACKEND = Path(__file__).resolve().parents[1]
V0 = "93304613c2dc"
V03 = "c3f9d2e4a8b1"
V04 = "d4a7e3b9c2f1"  # also the V0.4.1 schema (V0.4.1 added no migration)
V05 = "a56b296b53a5"
V06 = "c7d2e9f4b1a6"
V07 = "e8b3c1a5d7f2"

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

    command.upgrade(config, V04)
    assert_v04_state(migration_db, before)
    assert enum_values(migration_db) == enums

    command.downgrade(config, V03)
    assert v03_snapshot(migration_db) == before
    assert enum_values(migration_db) == enums
    assert not rows(
        migration_db, "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'job_source_postings'"
    )

    command.upgrade(config, V04)
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
    command.upgrade(config, V04)

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

    command.downgrade(config, V03)
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

    command.upgrade(config, V04)
    assert len(rows(migration_db, "SELECT id FROM job_source_postings")) == 3


# --- V0.5.0: candidate & job intelligence data model (create-only) -------------------

V05_TABLES = {"candidate_profile_versions", "job_intelligence", "job_requirements", "job_matches"}
V05_ENUMS = {
    "candidate_profile_version_reason": [
        "INITIAL", "PROFILE_UPDATED", "PREFERENCES_UPDATED", "RESUME_CONFIRMED",
        "SCHEMA_UPGRADE", "TAXONOMY_UPGRADE",
    ],
    "job_requirement_kind": ["REQUIRED", "PREFERRED", "CONTEXTUAL"],
    "job_requirement_category": [
        "SKILL", "EXPERIENCE", "LANGUAGE", "EDUCATION", "CERTIFICATION", "SENIORITY",
        "WORK_MODEL", "EMPLOYMENT_TYPE", "SALARY", "AUTHORIZATION", "TRAVEL",
    ],
    "extraction_confidence": ["HIGH", "MEDIUM", "LOW"],
    "job_match_verdict": ["STRONG", "GOOD", "PARTIAL", "WEAK", "BLOCKED", "INSUFFICIENT_DATA"],
}
# Index name -> expected definition fragment (partial / ordered indexes in full).
V05_INDEXES = {
    "candidate_profile_versions_pkey": "(id)",
    "uq_candidate_profile_versions_user_version": "(user_id, version_number)",
    "uq_candidate_profile_versions_user_content_hash": "(user_id, content_hash)",
    "uq_candidate_profile_versions_id_user": "(id, user_id)",
    "job_intelligence_pkey": "(id)",
    "uq_job_intelligence_result": "(job_id, result_hash, extractor_version, taxonomy_version)",
    "uq_job_intelligence_id_job": "(id, job_id)",
    "uq_job_intelligence_current": "UNIQUE INDEX uq_job_intelligence_current ON "
                                   "public.job_intelligence USING btree (job_id) WHERE is_current",
    "ix_job_intelligence_source_posting_id": "(source_posting_id)",
    "job_requirements_pkey": "(id)",
    "ix_job_requirements_job_intelligence_id": "(job_intelligence_id)",
    "job_matches_pkey": "(id)",
    "uq_job_matches_inputs": "(user_id, job_id, candidate_profile_version_id, "
                             "job_intelligence_id, model_id)",
    "uq_job_matches_current": "UNIQUE INDEX uq_job_matches_current ON public.job_matches "
                              "USING btree (user_id, job_id) WHERE is_current",
    "ix_job_matches_user_current_score": "INDEX ix_job_matches_user_current_score ON "
                                         "public.job_matches USING btree (user_id, score DESC "
                                         "NULLS LAST) WHERE is_current",
    "ix_job_matches_job_id": "(job_id)",
    "ix_job_matches_candidate_profile_version_id": "(candidate_profile_version_id)",
    "ix_job_matches_job_intelligence_id": "(job_intelligence_id)",
}
V05_FOREIGN_KEYS = {
    ("candidate_profile_versions", "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"),
    ("job_intelligence", "FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE"),
    ("job_intelligence", "FOREIGN KEY (source_posting_id) REFERENCES job_source_postings(id) "
                         "ON DELETE SET NULL"),
    ("job_requirements", "FOREIGN KEY (job_intelligence_id) REFERENCES job_intelligence(id) "
                         "ON DELETE CASCADE"),
    ("job_matches", "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"),
    ("job_matches", "FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE"),
    ("job_matches", "FOREIGN KEY (candidate_profile_version_id, user_id) "
                    "REFERENCES candidate_profile_versions(id, user_id)"),
    ("job_matches", "FOREIGN KEY (job_intelligence_id, job_id) "
                    "REFERENCES job_intelligence(id, job_id)"),
}

PROFILE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000d1")
RESUME_ID = uuid.UUID("00000000-0000-4000-8000-0000000000d2")


def seed_v04_profile_data(engine: Engine) -> None:
    """V0.1/V0.2/V0.3 rows that live alongside the jobs from `seed_v03`."""
    statements = [
        "INSERT INTO user_profiles (id, user_id, phone, location, linkedin_url) "
        "VALUES (gen_random_uuid(), :user, '+49 221 000', 'Köln', 'https://linkedin.com/in/j')",
        "INSERT INTO professional_profiles (id, user_id, current_job_title, years_of_experience, "
        "seniority_level) VALUES (gen_random_uuid(), :user, 'SAP Developer', 8, 'SENIOR')",
        "INSERT INTO job_preferences (id, user_id, minimum_salary, desired_salary, "
        "salary_currency, relocation, travel_willingness) "
        "VALUES (:prefs, :user, 70000, 85000, 'EUR', 'NO', 'OCCASIONAL')",
        "INSERT INTO job_preference_job_titles (id, job_preferences_id, title, priority) "
        "VALUES (gen_random_uuid(), :prefs, 'SAP ABAP Developer', 'PRIMARY')",
        "INSERT INTO job_preference_work_models (id, job_preferences_id, work_model) "
        "VALUES (gen_random_uuid(), :prefs, 'HYBRID')",
        "INSERT INTO deal_breakers (id, user_id, type, value, enabled) "
        "VALUES (gen_random_uuid(), :user, 'MINIMUM_SALARY', NULL, true)",
        "INSERT INTO skills (id, name) VALUES (:skill, 'SAP ABAP')",
        "INSERT INTO user_skills (id, user_id, skill_id, proficiency, years_of_experience) "
        "VALUES (gen_random_uuid(), :user, :skill, 'EXPERT', 8)",
        "INSERT INTO resumes (id, user_id, version_number, file_name, storage_reference, "
        "mime_type, file_size, file_hash, status) VALUES (:resume, :user, 1, 'cv.pdf', "
        "'local/cv.pdf', 'application/pdf', 1024, :hash, 'CONFIRMED')",
        "INSERT INTO resume_profiles (id, resume_id, user_id, status, parser_version, "
        "schema_version, raw_data, reviewed_data, warnings) VALUES (:profile, :resume, :user, "
        "'CONFIRMED', 'resume_parser_v1', 1, '{\"skills\": []}'::jsonb, "
        "'{\"skills\": []}'::jsonb, '[]'::jsonb)",
        "INSERT INTO discovery_runs (id, user_id, status, criteria) "
        "VALUES (gen_random_uuid(), :user, 'COMPLETED', '{\"keywords\": [\"SAP\"]}'::jsonb)",
    ]
    params = {
        "user": USER_ID, "prefs": uuid.uuid4(), "skill": uuid.uuid4(), "resume": RESUME_ID,
        "profile": PROFILE_ID, "hash": "a" * 64,
    }
    for statement in statements:
        execute(engine, statement, **params)


def user_tables(engine: Engine) -> set[str]:
    return {
        r["table_name"]
        for r in rows(
            engine,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version'",
        )
    }


def data_fingerprints(engine: Engine, tables: set[str]) -> dict[str, tuple[int, str | None]]:
    """Row count and an order-independent hash of every row, per table."""
    result = {}
    for table in sorted(tables):
        row = rows(
            engine,
            f'SELECT count(*) AS n, md5(string_agg(t::text, \'|\' ORDER BY t::text)) AS h '
            f'FROM "{table}" AS t',
        )[0]
        result[table] = (row["n"], row["h"])
    return result


def schema_fingerprint(engine: Engine, tables: set[str]) -> dict[str, Any]:
    """Columns, constraints and indexes of `tables`, as Postgres reports them."""
    params = {"tables": sorted(tables)}
    return {
        "columns": rows(
            engine,
            "SELECT table_name, column_name, ordinal_position, data_type, udt_name, "
            "character_maximum_length, numeric_precision, numeric_scale, is_nullable, "
            "column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ANY(:tables) "
            "ORDER BY table_name, ordinal_position",
            **params,
        ),
        "constraints": rows(
            engine,
            "SELECT c.conrelid::regclass::text AS table_name, c.conname, c.contype, "
            "pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c "
            "WHERE c.conrelid::regclass::text = ANY(:tables) ORDER BY 1, 2",
            **params,
        ),
        "indexes": rows(
            engine,
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = ANY(:tables) ORDER BY 1, 2",
            **params,
        ),
    }


def assert_v05_schema(engine: Engine) -> None:
    assert V05_TABLES <= user_tables(engine)
    enums = enum_values(engine)
    for name, values in V05_ENUMS.items():
        assert enums[name] == values, name
    indexes = {
        r["indexname"]: r["indexdef"]
        for r in rows(
            engine,
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = ANY(:tables)",
            tables=sorted(V05_TABLES),
        )
    }
    assert set(indexes) == set(V05_INDEXES)
    for name, fragment in V05_INDEXES.items():
        assert fragment in indexes[name], (name, indexes[name])
    foreign_keys = {
        (r["table_name"], r["definition"])
        for r in rows(
            engine,
            "SELECT conrelid::regclass::text AS table_name, pg_get_constraintdef(oid) "
            "AS definition FROM pg_constraint WHERE contype = 'f' "
            "AND conrelid::regclass::text = ANY(:tables)",
            tables=sorted(V05_TABLES),
        )
    }
    assert foreign_keys == V05_FOREIGN_KEYS
    # Reuses the existing salary_period type rather than creating another.
    assert rows(
        engine,
        "SELECT udt_name FROM information_schema.columns "
        "WHERE table_name = 'job_requirements' AND column_name = 'salary_period'",
    ) == [{"udt_name": "salary_period"}]


def seed_v05_rows(engine: Engine) -> None:
    """One row per V0.5 table, so downgrade() is exercised with data present."""
    version, intelligence = uuid.uuid4(), uuid.uuid4()
    execute(
        engine,
        "INSERT INTO candidate_profile_versions (id, user_id, version_number, content_hash, "
        "document, schema_version, sources, reason) VALUES (:id, :user, 1, :hash, "
        "'{}'::jsonb, 'candidate_profile_v1', '[]'::jsonb, 'INITIAL')",
        id=version, user=USER_ID, hash="b" * 64,
    )
    execute(
        engine,
        "INSERT INTO job_intelligence (id, job_id, extractor_version, taxonomy_version, "
        "input_hash, result_hash, facts) VALUES (:id, :job, 'extractor_v1', 'taxonomy_v1', "
        ":hash, :hash, '{}'::jsonb)",
        id=intelligence, job=GH_JOB, hash="c" * 64,
    )
    execute(
        engine,
        "INSERT INTO job_requirements (id, job_intelligence_id, kind, category, skill_key, "
        "source_field, extraction_method, confidence) VALUES (gen_random_uuid(), :ji, "
        "'REQUIRED', 'SKILL', 'sap_abap', 'requirements', 'vocabulary', 'HIGH')",
        ji=intelligence,
    )
    execute(
        engine,
        "INSERT INTO job_matches (id, user_id, job_id, candidate_profile_version_id, "
        "job_intelligence_id, model_id, model_hash, coverage, verdict, criteria, caps) "
        "VALUES (gen_random_uuid(), :user, :job, :version, :ji, 'match_v1', :hash, 0.5, "
        "'GOOD', '[]'::jsonb, '[]'::jsonb)",
        user=USER_ID, job=GH_JOB, version=version, ji=intelligence, hash="d" * 64,
    )


def test_v041_data_survives_v05_upgrade_downgrade_upgrade(migration_db: Engine) -> None:
    config = alembic_config()
    command.upgrade(config, V03)
    seed_v03(migration_db)
    command.upgrade(config, V04)
    seed_v04_profile_data(migration_db)

    v04_tables = user_tables(migration_db)
    assert not v04_tables & V05_TABLES
    data_before = data_fingerprints(migration_db, v04_tables)
    schema_before = schema_fingerprint(migration_db, v04_tables)
    enums_before = enum_values(migration_db)
    # The fingerprints cover real data, not just empty tables.
    assert len([table for table, (count, _) in data_before.items() if count]) >= 14

    # V0.4.1 -> V0.5.0
    command.upgrade(config, V05)
    assert user_tables(migration_db) == v04_tables | V05_TABLES
    assert data_fingerprints(migration_db, v04_tables) == data_before
    assert schema_fingerprint(migration_db, v04_tables) == schema_before
    assert_v05_schema(migration_db)
    enums = enum_values(migration_db)
    assert {k: v for k, v in enums.items() if k not in V05_ENUMS} == enums_before

    # V0.5.0 again (it was head when this test was written; V0.6 has its
    # own roundtrip test below)
    command.upgrade(config, V05)
    assert_v05_schema(migration_db)
    seed_v05_rows(migration_db)

    # V0.5.0 -> V0.4.1: removes exactly the V0.5 objects
    command.downgrade(config, V04)
    assert user_tables(migration_db) == v04_tables
    assert data_fingerprints(migration_db, v04_tables) == data_before
    assert schema_fingerprint(migration_db, v04_tables) == schema_before
    assert enum_values(migration_db) == enums_before

    # V0.4.1 -> V0.5.0 again
    command.upgrade(config, V05)
    assert data_fingerprints(migration_db, v04_tables) == data_before
    assert schema_fingerprint(migration_db, v04_tables) == schema_before
    assert_v05_schema(migration_db)
    for table in V05_TABLES:
        assert rows(migration_db, f'SELECT count(*) AS n FROM "{table}"') == [{"n": 0}]


# --- V0.6: job matching & explainability (additive) -----------------------------------

V06_MATCH_COLUMNS = {
    "job_score": "numeric",
    "preference_score": "numeric",
    "job_coverage": "numeric",
    "preference_coverage": "numeric",
    "taxonomy_version": "varchar",
    "evidence": "jsonb",
}
V06_CHECKS = {
    "ck_job_matches_job_score",
    "ck_job_matches_preference_score",
    "ck_job_matches_job_coverage",
    "ck_job_matches_preference_coverage",
}


def seed_v06_rows(engine: Engine) -> None:
    """A V0.6 result computed from a fact sheet with an UNSPECIFIED requirement,
    with an AI explanation."""
    version = rows(engine, "SELECT id FROM candidate_profile_versions LIMIT 1")[0]["id"]
    intelligence, match = uuid.uuid4(), uuid.uuid4()
    execute(
        engine,
        "UPDATE job_intelligence SET is_current = false WHERE job_id = :job", job=GH_JOB,
    )
    execute(
        engine,
        "INSERT INTO job_intelligence (id, job_id, extractor_version, taxonomy_version, "
        "input_hash, result_hash, facts) VALUES (:id, :job, 'job_extractor_v1', 'taxonomy_v1', "
        ":hash, :hash, '{}'::jsonb)",
        id=intelligence, job=GH_JOB, hash="e" * 64,
    )
    execute(
        engine,
        "INSERT INTO job_requirements (id, job_intelligence_id, kind, category, skill_key, "
        "source_field, extraction_method, confidence) VALUES (gen_random_uuid(), :ji, "
        "'UNSPECIFIED', 'SKILL', 'python', 'description', 'skill_taxonomy', 'HIGH')",
        ji=intelligence,
    )
    execute(
        engine,
        "UPDATE job_matches SET is_current = false WHERE user_id = :user", user=USER_ID,
    )
    execute(
        engine,
        "INSERT INTO job_matches (id, user_id, job_id, candidate_profile_version_id, "
        "job_intelligence_id, model_id, model_hash, score, coverage, verdict, criteria, caps, "
        "job_score, preference_score, job_coverage, preference_coverage, taxonomy_version, "
        "evidence) VALUES (:id, :user, :job, :version, :ji, 'matching_v1', :hash, 83, 0.8, "
        "'STRONG', '[]'::jsonb, '[]'::jsonb, 87, 74, 0.9, 0.6, 'taxonomy_v1', "
        "'{\"components\": []}'::jsonb)",
        id=match, user=USER_ID, job=GH_JOB, version=version, ji=intelligence, hash="f" * 64,
    )
    execute(
        engine,
        "INSERT INTO job_match_explanations (id, job_match_id, prompt_version, ai_provider, "
        "content) VALUES (gen_random_uuid(), :match, 'matching_explanation_v1', 'fake', "
        "'{\"summary\": \"s\"}'::jsonb)",
        match=match,
    )


def test_v05_data_survives_v06_upgrade_downgrade_upgrade(migration_db: Engine) -> None:
    config = alembic_config()
    command.upgrade(config, V03)
    seed_v03(migration_db)
    command.upgrade(config, V04)
    seed_v04_profile_data(migration_db)
    command.upgrade(config, V05)
    seed_v05_rows(migration_db)

    v05_tables = user_tables(migration_db)
    data_before = data_fingerprints(migration_db, v05_tables)
    schema_before = schema_fingerprint(migration_db, v05_tables)
    enums_before = enum_values(migration_db)

    # V0.5 -> V0.6: additive only.
    command.upgrade(config, V06)
    assert user_tables(migration_db) == v05_tables | {"job_match_explanations"}
    unchanged = v05_tables - {"job_matches"}
    assert data_fingerprints(migration_db, unchanged) == {
        t: v for t, v in data_before.items() if t in unchanged
    }
    columns = {
        r["column_name"]: r["udt_name"]
        for r in rows(
            migration_db,
            "SELECT column_name, udt_name FROM information_schema.columns "
            "WHERE table_name = 'job_matches'",
        )
    }
    for name, udt in V06_MATCH_COLUMNS.items():
        assert columns[name] == udt, name
    checks = {
        r["conname"]
        for r in rows(
            migration_db,
            "SELECT conname FROM pg_constraint WHERE conrelid = 'job_matches'::regclass",
        )
    }
    assert V06_CHECKS <= checks
    enums = enum_values(migration_db)
    assert enums["job_requirement_kind"] == ["REQUIRED", "PREFERRED", "CONTEXTUAL", "UNSPECIFIED"]
    assert {k: v for k, v in enums.items() if k != "job_requirement_kind"} == {
        k: v for k, v in enums_before.items() if k != "job_requirement_kind"
    }
    # The pre-existing V0.5 result is untouched; its new fields are empty.
    assert rows(
        migration_db,
        "SELECT model_id, job_score, preference_score, taxonomy_version, evidence "
        "FROM job_matches",
    ) == [{"model_id": "match_v1", "job_score": None, "preference_score": None,
           "taxonomy_version": None, "evidence": {}}]
    seed_v06_rows(migration_db)

    # V0.6 -> V0.5: exactly the V0.5 schema and data again.
    command.downgrade(config, V05)
    assert user_tables(migration_db) == v05_tables
    assert schema_fingerprint(migration_db, v05_tables) == schema_before
    assert enum_values(migration_db) == enums_before
    # The V0.6-derived fact sheet that used UNSPECIFIED (and its match) is
    # gone; every V0.5 row is still there.
    remaining = rows(migration_db, "SELECT model_id FROM job_matches")
    assert remaining == [{"model_id": "match_v1"}]
    assert rows(migration_db, "SELECT count(*) AS n FROM job_intelligence") == [{"n": 1}]
    other_tables = v05_tables - {"job_matches", "job_intelligence", "job_requirements"}
    assert data_fingerprints(migration_db, other_tables) == {
        t: v for t, v in data_before.items() if t in other_tables
    }

    # V0.5 -> V0.6 again.
    command.upgrade(config, "head")
    assert "job_match_explanations" in user_tables(migration_db)
    assert enum_values(migration_db)["job_requirement_kind"][-1] == "UNSPECIFIED"


# --- V0.7: application workspace (evolves the V0 `applications` table) ------------------------

V06_APPLICATION_STATUS = [
    "DRAFT", "IN_PROGRESS", "SUBMITTED", "INTERVIEWING", "OFFER", "REJECTED", "WITHDRAWN",
]
V07_ENUMS = {
    "application_status": [*V06_APPLICATION_STATUS, "PREPARING"],
    "notice_period_unit": ["DAYS", "WEEKS", "MONTHS"],
    "work_authorization": ["AUTHORIZED", "REQUIRES_SPONSORSHIP", "UNKNOWN"],
}
V07_CONSTRAINTS = {
    "fk_applications_user_job": "FOREIGN KEY (user_job_id, user_id) "
                                "REFERENCES user_jobs(id, user_id) ON DELETE CASCADE",
    "uq_applications_user_job_id": "UNIQUE (user_job_id)",
    "uq_user_jobs_id_user": "UNIQUE (id, user_id)",
    "applications_resume_id_fkey": "FOREIGN KEY (resume_id) REFERENCES resumes(id) "
                                   "ON DELETE SET NULL",
    "applications_source_posting_id_fkey": "FOREIGN KEY (source_posting_id) "
                                           "REFERENCES job_source_postings(id) ON DELETE SET NULL",
    "application_context_snapshots_application_id_fkey": "FOREIGN KEY (application_id) "
                                                         "REFERENCES applications(id) "
                                                         "ON DELETE CASCADE",
    "uq_application_context_snapshots_application_id": "UNIQUE (application_id)",
}
V07_CHECKS = {
    "ck_applications_salary_expectation",
    "ck_applications_notice_period_pair",
    "ck_applications_notice_period_value",
    "ck_applications_application_language",
}
V07_INDEXES = {
    "applications": {
        "applications_pkey", "uq_applications_user_job_id", "ix_applications_source_posting_id",
        "ix_applications_resume_id", "ix_applications_user_id_updated_at",
    },
    "application_context_snapshots": {
        "application_context_snapshots_pkey",
        "uq_application_context_snapshots_application_id",
    },
}
V07_NEW_APPLICATION_COLUMNS = (
    "user_job_id", "source_posting_id", "resume_id", "mention_salary_in_letter",
    "notice_period_value", "notice_period_unit", "work_authorization", "application_language",
    "notes",
)
V07_ONLY_APPLICATION = uuid.UUID("00000000-0000-4000-8000-0000000000c2")


def build_v06_database(engine: Engine, config: Config) -> None:
    command.upgrade(config, V03)
    seed_v03(engine)
    command.upgrade(config, V04)
    seed_v04_profile_data(engine)
    command.upgrade(config, V05)
    seed_v05_rows(engine)
    command.upgrade(config, V06)
    seed_v06_rows(engine)
    # The V0 placeholder row (seed_v03) with the fields V0.7 keeps.
    execute(
        engine,
        "UPDATE applications SET salary_expectation = 72000, salary_currency = 'EUR', "
        "start_date = '2026-12-01' WHERE id = :id",
        id=APPLICATION_ID,
    )


def assert_v07_schema(engine: Engine) -> None:
    assert "application_context_snapshots" in user_tables(engine)
    enums = enum_values(engine)
    for name, values in V07_ENUMS.items():
        assert enums[name] == values, name
    constraints = {
        r["conname"]: r["definition"]
        for r in rows(
            engine,
            "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            "WHERE conrelid IN ('applications'::regclass, 'user_jobs'::regclass, "
            "'application_context_snapshots'::regclass)",
        )
    }
    for name, definition in V07_CONSTRAINTS.items():
        assert constraints.get(name) == definition, name
    assert V07_CHECKS <= set(constraints)
    for table, expected in V07_INDEXES.items():
        actual = {
            r["indexname"] for r in rows(engine, "SELECT indexname FROM pg_indexes "
                                         "WHERE tablename = :t", t=table)
        }
        assert actual == expected, table
    nullable = {
        r["column_name"]: r["is_nullable"]
        for r in rows(
            engine,
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'applications'",
        )
    }
    assert nullable["user_job_id"] == "NO"
    assert {"available_from", *V07_NEW_APPLICATION_COLUMNS} <= set(nullable)
    assert "start_date" not in nullable
    # The V0 placeholder columns that V0.7 reuses are still there.
    assert {"salary_expectation", "salary_currency", "user_id", "job_id", "status"} <= set(nullable)


def assert_legacy_application_migrated(engine: Engine) -> None:
    """The V0 placeholder row (seed_v03 + build_v06_database), as V0.7 leaves it."""
    application = rows(engine, "SELECT * FROM applications WHERE id = :id", id=APPLICATION_ID)[0]
    # What V0 stored is untouched (the status too: no legacy value is rewritten).
    assert application["user_id"] == USER_ID and application["job_id"] == GH_JOB
    assert application["status"] == "SUBMITTED"
    assert application["salary_expectation"] == Decimal("72000.00")
    assert application["salary_currency"] == "EUR"
    assert application["available_from"] == date(2026, 12, 1)  # V0's start_date
    # Attached to the user's existing UserJob for that job.
    user_job = rows(
        engine, "SELECT id FROM user_jobs WHERE user_id = :u AND job_id = :j", u=USER_ID, j=GH_JOB
    )[0]["id"]
    assert application["user_job_id"] == user_job
    # Nothing is invented for the new fields.
    for column in V07_NEW_APPLICATION_COLUMNS[1:]:
        assert application[column] is None, column

    snapshot = rows(
        engine,
        "SELECT * FROM application_context_snapshots WHERE application_id = :id",
        id=APPLICATION_ID,
    )[0]
    document = ApplicationContextDocument.model_validate(snapshot["document"])
    assert snapshot["schema_version"] == document.schema_version == "application_context_v1"
    assert snapshot["content_hash"] == hash_document(snapshot["document"])
    # Rebuilt from the job as it is now; the document says so.
    assert document.origin == "migrated"
    assert document.job.job_id == GH_JOB
    assert (document.job.title, document.job.company_name) == (
        "Senior SAP ABAP Developer", "Example GmbH",
    )
    assert (document.job.salary_min, document.job.salary_max) == (
        Decimal("70000.00"), Decimal("90000.00"),
    )
    job = document.job
    assert job.employment_type is not None and job.employment_type.value == "full_time"
    assert job.work_model is not None and job.work_model.value == "hybrid"
    assert job.seniority is not None and job.seniority.value == "senior"
    source = document.source
    assert (source.source, source.source_job_id, source.source_company_key) == (
        "greenhouse", "4012345", "acme",
    )
    assert source.original_url == "https://job-boards.greenhouse.io/acme/jobs/4012345?utm_source=x"
    assert source.canonical_url == "https://job-boards.greenhouse.io/acme/jobs/4012345"
    assert source.application_url == source.canonical_url  # an ATS hosts its own form
    assert source.was_preferred is True


def test_v06_data_survives_v07_upgrade_downgrade_upgrade(migration_db: Engine) -> None:
    config = alembic_config()
    build_v06_database(migration_db, config)

    v06_tables = user_tables(migration_db)
    assert "application_context_snapshots" not in v06_tables
    data_before = data_fingerprints(migration_db, v06_tables)
    schema_before = schema_fingerprint(migration_db, v06_tables)
    enums_before = enum_values(migration_db)
    assert data_before["applications"][0] == 1 and data_before["user_jobs"][0] == 2

    # V0.6 -> V0.7: additive (plus the one rename).
    command.upgrade(config, V07)
    assert user_tables(migration_db) == v06_tables | {"application_context_snapshots"}
    unchanged = v06_tables - {"applications"}
    assert data_fingerprints(migration_db, unchanged) == {
        t: v for t, v in data_before.items() if t in unchanged
    }
    assert_v07_schema(migration_db)
    assert_legacy_application_migrated(migration_db)
    enums = enum_values(migration_db)
    assert {k: v for k, v in enums.items() if k not in V07_ENUMS} == {
        k: v for k, v in enums_before.items() if k not in V07_ENUMS
    }

    # An application V0.7 itself created (for the other user job).
    execute(
        migration_db,
        "INSERT INTO applications (id, user_id, job_id, user_job_id, status, notes, "
        "application_language, notice_period_value, notice_period_unit, "
        "mention_salary_in_letter, available_from, salary_expectation, salary_currency) "
        "SELECT :id, :user, :job, uj.id, 'PREPARING', 'private note', 'de', 3, 'MONTHS', true, "
        "'2027-01-01', 78000, 'EUR' FROM user_jobs uj "
        "WHERE uj.user_id = :user AND uj.job_id = :job",
        id=V07_ONLY_APPLICATION, user=USER_ID, job=LEVER_JOB,
    )

    # V0.7 -> V0.6: exactly the V0.6 schema again; V0.6 data and the rows
    # (V0.7's included, as DRAFT) are kept.
    command.downgrade(config, V06)
    assert user_tables(migration_db) == v06_tables
    assert schema_fingerprint(migration_db, v06_tables) == schema_before
    assert enum_values(migration_db) == enums_before
    assert data_fingerprints(migration_db, unchanged) == {
        t: v for t, v in data_before.items() if t in unchanged
    }
    legacy = rows(
        migration_db,
        "SELECT user_id, job_id, status, salary_expectation, salary_currency, start_date "
        "FROM applications WHERE id = :id",
        id=APPLICATION_ID,
    )
    assert legacy == [{
        "user_id": USER_ID, "job_id": GH_JOB, "status": "SUBMITTED",
        "salary_expectation": Decimal("72000.00"), "salary_currency": "EUR",
        "start_date": date(2026, 12, 1),
    }]
    survivor = rows(
        migration_db,
        "SELECT job_id, status, salary_expectation, start_date FROM applications WHERE id = :id",
        id=V07_ONLY_APPLICATION,
    )
    assert survivor == [{
        "job_id": LEVER_JOB, "status": "DRAFT", "salary_expectation": Decimal("78000.00"),
        "start_date": date(2027, 1, 1),
    }]

    # V0.6 -> V0.7 again: both rows are attached and get a (migrated) context.
    command.upgrade(config, "head")
    assert_v07_schema(migration_db)
    assert_legacy_application_migrated(migration_db)
    assert rows(
        migration_db, "SELECT count(*) AS n FROM application_context_snapshots"
    ) == [{"n": 2}]
    assert rows(
        migration_db, "SELECT status FROM applications WHERE id = :id", id=V07_ONLY_APPLICATION
    ) == [{"status": "DRAFT"}]
    # Nothing was invented along the way.
    assert data_fingerprints(migration_db, unchanged) == {
        t: v for t, v in data_before.items() if t in unchanged
    }


def test_a_v0_application_without_a_user_job_gets_one(migration_db: Engine) -> None:
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
        "INSERT INTO applications (id, user_id, job_id, status, salary_expectation, "
        "salary_currency, start_date) VALUES (:id, :user, :job, 'DRAFT', 65000, 'EUR', "
        "'2026-11-15')",
        id=APPLICATION_ID, user=USER_ID, job=GH_JOB,
    )

    command.upgrade(config, "head")

    # There was no user_jobs table at V0: the migration creates the UserJob the
    # application hangs off, without inventing a discovery or a save.
    user_job = rows(migration_db, "SELECT * FROM user_jobs")
    assert len(user_job) == 1
    assert (user_job[0]["user_id"], user_job[0]["job_id"]) == (USER_ID, GH_JOB)
    assert user_job[0]["first_discovered_at"] is None
    assert user_job[0]["last_discovered_at"] is None
    assert user_job[0]["saved_at"] is None
    application = rows(migration_db, "SELECT * FROM applications")[0]
    assert application["user_job_id"] == user_job[0]["id"]
    assert (application["status"], application["salary_expectation"]) == (
        "DRAFT", Decimal("65000.00"),
    )
    assert application["available_from"] == date(2026, 11, 15)
    document = rows(
        migration_db, "SELECT document FROM application_context_snapshots"
    )[0]["document"]
    context = ApplicationContextDocument.model_validate(document)
    assert context.origin == "migrated"
    assert (context.job.title, context.job.company_name) == ("Backend Engineer", "Acme GmbH")
    assert context.source.source == "greenhouse" and context.source.source_job_id == "42"


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        pytest.param(
            "INSERT INTO applications (id, user_id, job_id, status) "
            "VALUES (gen_random_uuid(), :user, NULL, 'DRAFT')",
            "no job_id",
            id="application-without-a-job",
        ),
        pytest.param(
            "INSERT INTO applications (id, user_id, job_id, status) "
            "VALUES (gen_random_uuid(), :user, :job, 'DRAFT'), "
            "(gen_random_uuid(), :user, :job, 'DRAFT')",
            "one application per user and job",
            id="two-applications-for-one-job",
        ),
        pytest.param(
            "INSERT INTO applications (id, user_id, job_id, status, salary_expectation) "
            "VALUES (gen_random_uuid(), :user, :job, 'DRAFT', -1)",
            "negative",
            id="negative-salary",
        ),
    ],
)
def test_v07_stops_on_rows_it_cannot_attach_and_changes_nothing(
    migration_db: Engine, setup: str, message: str
) -> None:
    config = alembic_config()
    command.upgrade(config, V0)
    seed_user(migration_db)
    execute(
        migration_db,
        "INSERT INTO jobs (id, source, external_id, title, company, url) "
        "VALUES (:id, 'greenhouse', '42', 'Backend Engineer', 'Acme GmbH', "
        "'https://job-boards.greenhouse.io/acme/jobs/42')",
        id=GH_JOB,
    )
    command.upgrade(config, V06)
    execute(migration_db, setup, user=USER_ID, job=GH_JOB)
    tables = user_tables(migration_db)
    data_before = data_fingerprints(migration_db, tables)
    schema_before = schema_fingerprint(migration_db, tables)
    enums_before = enum_values(migration_db)

    with pytest.raises(RuntimeError, match=message):
        command.upgrade(config, V07)

    # Stopped before touching anything: no rows lost or merged, no schema change.
    assert rows(migration_db, "SELECT version_num FROM alembic_version") == [{"version_num": V06}]
    assert user_tables(migration_db) == tables
    assert data_fingerprints(migration_db, tables) == data_before
    assert schema_fingerprint(migration_db, tables) == schema_before
    assert enum_values(migration_db) == enums_before
