"""V0.4 cross-source deduplication, end to end.

Greenhouse and Lever adapters run over mocked HTTP (`FakeJobBoards`), so
these tests cover adapter -> normalizer -> identity resolution -> DB -> API
without network access. Both sources list "Senior SAP ABAP Developer" at
"Example GmbH" in Cologne/Köln with the same text, as one company publishing
one vacancy on two ATS boards would.
"""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from app.models.user import User
from app.models.user_job import UserJob
from app.schemas.jobs import JobSearchCriteria
from app.services.job_discovery.discovery_service import JobDiscoveryService
from app.services.job_discovery.normalizer import JobNormalizer
from app.services.job_discovery.persistence import lock_postings, upsert_job
from app.services.job_discovery.registry import JobSourceRegistry
from app.services.job_discovery.sources.greenhouse import API_HOST as GREENHOUSE_HOST
from tests.conftest import TestSessionLocal
from tests.job_helpers import (
    LEVER_HOST,
    OTHER_INTRO,
    OTHER_REQUIREMENTS,
    OTHER_RESPONSIBILITIES,
    FakeJobBoards,
    cross_source_registry,
    greenhouse_job,
    lever_posting,
    make_registry,
)

GH_ID = 7001
LEVER_ID = "c0ffee00-0000-4000-8000-000000000001"
GH_URL = f"https://job-boards.greenhouse.io/example/jobs/{GH_ID}"
LEVER_URL = f"https://jobs.lever.co/example/{LEVER_ID}"


@pytest.fixture
def job_registry(job_boards: FakeJobBoards) -> JobSourceRegistry:
    """Overrides conftest: both sources list the same SAP vacancy."""
    return cross_source_registry(
        job_boards,
        greenhouse_jobs=[greenhouse_job(GH_ID)],
        lever_postings=[lever_posting(LEVER_ID)],
    )


@pytest.fixture
def auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {register_and_login()['access_token']}"}


@pytest.fixture
def other_auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = register_and_login(email="mallory@example.com")["access_token"]
    return {"Authorization": f"Bearer {token}"}


def discover(
    client: TestClient, auth: dict[str, str], criteria: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = {"criteria": criteria} if criteria is not None else None
    response = client.post("/api/v1/jobs/discover", json=body, headers=auth)
    assert response.status_code == 202, response.text
    run = client.get(f"/api/v1/jobs/discover/runs/{response.json()['id']}", headers=auth)
    assert run.status_code == 200, run.text
    return run.json()


def list_jobs(client: TestClient, auth: dict[str, str], **params: Any) -> dict[str, Any]:
    response = client.get("/api/v1/jobs", params=params, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def get_job(client: TestClient, auth: dict[str, str], job_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/jobs/{job_id}", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def count(db: Session, model: Any) -> int:
    db.expire_all()
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def by_source(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["source"]: s for s in summary["sources"]}


# --- One vacancy, two sources ------------------------------------------------------------


def test_same_job_on_two_sources_is_one_canonical_job_with_two_postings(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    run = discover(client, auth)
    assert run["status"] == "completed"
    summary = run["summary"]
    assert by_source(summary)["greenhouse"]["created"] == 1
    assert by_source(summary)["lever"]["merged"] == 1
    assert by_source(summary)["lever"]["created"] == 0
    assert summary["discovered"] == 1  # distinct canonical jobs
    assert (summary["created"], summary["merged"], summary["possible_matches"]) == (1, 1, 0)

    assert count(db_session, Job) == 1
    assert count(db_session, JobSourcePosting) == 2
    job = db_session.execute(select(Job)).scalar_one()
    postings = {p.source: p for p in db_session.execute(select(JobSourcePosting)).scalars()}
    assert {p.job_id for p in postings.values()} == {job.id}
    assert postings["greenhouse"].canonical_source_url == GH_URL
    assert postings["lever"].canonical_source_url == LEVER_URL
    assert postings["lever"].location == "Köln"  # what Lever said, as written
    assert postings["lever"].apply_url == f"{LEVER_URL}/apply"

    # Canonical, normalized and queryable without knowing either source.
    assert job.title == "Senior SAP ABAP Developer"
    assert job.company_name == "Example GmbH"
    assert job.normalized_company_name == "example"
    assert job.normalized_title == "senior sap abap developer"
    assert job.normalized_location == "de:cologne"
    assert job.city == "Cologne"
    assert job.employment_type is not None and job.employment_type.value == "full_time"
    # Preferred posting: equally reliable ATS boards, both can take
    # applications -> source priority (Greenhouse first).
    assert job.preferred_source_posting_id == postings["greenhouse"].id
    assert job.canonical_url == GH_URL


def test_jobs_list_shows_one_card_with_all_sources(
    client: TestClient, auth: dict[str, str]
) -> None:
    discover(client, auth)
    jobs = list_jobs(client, auth)
    assert jobs["total"] == 1
    item = jobs["items"][0]
    assert item["sources"] == ["greenhouse", "lever"]
    assert item["source"] == "greenhouse"
    assert item["source_count"] == 2
    assert item["is_active"] is True


@pytest.mark.parametrize(
    ("sources", "expected"),
    [(["greenhouse"], 1), (["lever"], 1), (["greenhouse", "lever"], 1), (["other"], 0)],
)
def test_source_filter_finds_multi_source_jobs_under_each_source(
    client: TestClient, auth: dict[str, str], sources: list[str], expected: int
) -> None:
    discover(client, auth)
    assert list_jobs(client, auth, source=sources)["total"] == expected


def test_job_detail_lists_every_source_with_its_original_url(
    client: TestClient, auth: dict[str, str]
) -> None:
    discover(client, auth)
    job_id = list_jobs(client, auth)["items"][0]["id"]
    detail = get_job(client, auth, job_id)

    postings = detail["source_postings"]
    assert [p["source"] for p in postings] == ["greenhouse", "lever"]
    assert [p["original_url"] for p in postings] == [GH_URL, LEVER_URL]
    assert [p["is_preferred"] for p in postings] == [True, False]
    assert detail["preferred_source_posting_id"] == postings[0]["id"]
    assert postings[1]["location"] == "Köln"
    assert postings[1]["apply_url"] == f"{LEVER_URL}/apply"
    for posting in postings:
        assert posting["status"] == "active"
        assert posting["is_active"] is True
        assert posting["seen_in_latest_run"] is True
        assert posting["first_seen_at"] and posting["last_seen_at"]
    assert detail["city"] == "Cologne"
    assert detail["source_count"] == 2

    # Raw payloads and matching internals are never exposed.
    text = str(detail)
    for secret in ("raw_data", "normalized_data", "content_hash", "dedup_key", "url_key",
                   "normalized_title", "identity_version"):
        assert secret not in text

    sources = client.get(f"/api/v1/jobs/{job_id}/sources", headers=auth)
    assert sources.status_code == 200
    assert sources.json() == postings


def test_sources_endpoint_requires_auth_and_ownership(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str]
) -> None:
    discover(client, auth)
    job_id = list_jobs(client, auth)["items"][0]["id"]
    assert client.get(f"/api/v1/jobs/{job_id}/sources").status_code == 401
    assert client.get(f"/api/v1/jobs/{job_id}/sources", headers=other_auth).status_code == 404
    assert client.get(f"/api/v1/jobs/{uuid.uuid4()}/sources", headers=auth).status_code == 404


def test_repeated_discovery_creates_no_duplicates(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    discover(client, auth)
    first = db_session.execute(select(Job.id)).scalar_one()
    summary = discover(client, auth)["summary"]
    assert (summary["created"], summary["merged"], summary["unchanged"]) == (0, 0, 2)
    assert summary["cross_source_matches"] == 2  # both postings belong to a multi-source job
    summary = discover(client, auth)["summary"]
    assert (summary["created"], summary["merged"]) == (0, 0)
    assert count(db_session, Job) == 1
    assert count(db_session, JobSourcePosting) == 2
    assert db_session.execute(select(Job.id)).scalar_one() == first


def test_summary_counters_are_consistent(client: TestClient, auth: dict[str, str]) -> None:
    summary = discover(client, auth)["summary"]
    for source in summary["sources"]:
        stored = source["created"] + source["merged"] + source["updated"] + source["unchanged"]
        assert source["skipped"] == (
            source["invalid"] + source["duplicates"] + source["filtered_out"]
            + source["beyond_limit"]
        )
        assert source["fetched"] == source["skipped"] + stored
        assert source["error_count"] == len(source["errors"])


# --- False-positive protection -------------------------------------------------------------


def test_same_title_different_city_stays_two_jobs(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    job_boards.lever_site("example", postings=[lever_posting(LEVER_ID, location="München")])
    summary = discover(client, auth)["summary"]
    assert (summary["created"], summary["merged"]) == (2, 0)
    assert count(db_session, Job) == 2
    assert {j.city for j in db_session.execute(select(Job)).scalars()} == {"Cologne", "Munich"}


def test_same_title_and_city_different_company_stays_two_jobs(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session,
    job_registry: JobSourceRegistry,
) -> None:
    from tests.job_helpers import lever_source

    job_registry._sources["lever"] = lever_source(job_boards, "example:Other GmbH")
    summary = discover(client, auth)["summary"]
    assert (summary["created"], summary["merged"]) == (2, 0)
    assert count(db_session, Job) == 2


def test_same_company_title_city_but_different_description_stays_two_jobs(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    other = lever_posting(
        LEVER_ID,
        intro=OTHER_INTRO,
        responsibilities=OTHER_RESPONSIBILITIES,
        requirements=OTHER_REQUIREMENTS,
    )
    job_boards.lever_site("example", postings=[other])
    summary = discover(client, auth)["summary"]
    assert (summary["created"], summary["merged"]) == (2, 0)
    assert count(db_session, Job) == 2


def test_a_possible_match_is_reported_but_never_merged(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    # Same company/title/city and text, but Lever says it's a working-student
    # role: a conflicting fact, so not merged automatically.
    job_boards.lever_site(
        "example", postings=[lever_posting(LEVER_ID, commitment="Working Student")]
    )
    summary = discover(client, auth)["summary"]
    assert by_source(summary)["lever"]["possible_matches"] == 1
    assert (summary["created"], summary["merged"]) == (2, 0)
    assert count(db_session, Job) == 2


def test_two_ids_on_the_same_board_are_two_jobs_even_with_identical_text(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    job_boards.greenhouse_board(
        "example", jobs={"jobs": [greenhouse_job(GH_ID), greenhouse_job(GH_ID + 1)]},
        board={"name": "Example GmbH"},
    )
    summary = discover(client, auth)["summary"]
    # The Lever posting matches both Greenhouse jobs equally: ambiguous, so
    # it is not merged into either.
    assert by_source(summary)["greenhouse"]["created"] == 2
    assert by_source(summary)["lever"]["created"] == 1
    assert by_source(summary)["lever"]["possible_matches"] == 1
    assert count(db_session, Job) == 3


# --- User state and future applications survive merging -----------------------------------


def test_saved_job_keeps_its_id_and_saved_state_when_a_second_source_matches(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    discover(client, auth, {"sources": ["greenhouse"]})
    job_id = list_jobs(client, auth)["items"][0]["id"]
    saved = client.put(f"/api/v1/jobs/{job_id}/save", headers=auth).json()
    assert saved["saved"] is True

    summary = discover(client, auth, {"sources": ["lever"]})["summary"]
    assert summary["merged"] == 1

    jobs = list_jobs(client, auth)
    assert jobs["total"] == 1
    detail = get_job(client, auth, job_id)  # same id
    assert detail["saved"] is True
    assert detail["saved_at"] == saved["saved_at"]
    assert [p["source"] for p in detail["source_postings"]] == ["greenhouse", "lever"]
    db_session.expire_all()
    user_job = db_session.execute(select(UserJob)).scalar_one()
    assert str(user_job.job_id) == job_id


def test_existing_application_still_points_at_the_canonical_job_after_merging(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    discover(client, auth, {"sources": ["greenhouse"]})
    job = db_session.execute(select(Job)).scalar_one()
    user = db_session.execute(select(User)).scalar_one()
    # Since V0.7 an application belongs to the user's UserJob for the job.
    user_job = db_session.execute(select(UserJob)).scalar_one()
    application = Application(user_id=user.id, user_job_id=user_job.id, job_id=job.id)
    db_session.add(application)
    db_session.commit()
    created_at = application.created_at

    discover(client, auth, {"sources": ["lever"]})
    db_session.expire_all()
    application = db_session.get(Application, application.id)
    assert application is not None
    assert application.job_id == job.id
    assert application.user_job_id == user_job.id
    assert application.created_at == created_at
    assert application.status.value == "preparing"
    assert count(db_session, Job) == 1
    assert count(db_session, JobSourcePosting) == 2


def test_other_users_state_is_never_exposed(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str]
) -> None:
    discover(client, auth)
    discover(client, other_auth)
    job_id = list_jobs(client, auth)["items"][0]["id"]
    client.put(f"/api/v1/jobs/{job_id}/save", headers=auth)
    # Same canonical job for both users, saved only for the first.
    other = get_job(client, other_auth, job_id)
    assert other["saved"] is False and other["saved_at"] is None
    assert get_job(client, auth, job_id)["saved"] is True


# --- Changes and freshness ------------------------------------------------------------------


def test_changed_posting_refreshes_the_canonical_job_but_not_user_state(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    discover(client, auth)
    job_id = list_jobs(client, auth)["items"][0]["id"]
    saved_at = client.put(f"/api/v1/jobs/{job_id}/save", headers=auth).json()["saved_at"]
    db_session.expire_all()
    old_hash = db_session.execute(
        select(JobSourcePosting.content_hash).where(JobSourcePosting.source == "greenhouse")
    ).scalar_one()

    changed = greenhouse_job(GH_ID, requirements=("At least eight years of ABAP experience",))
    job_boards.greenhouse_board("example", jobs={"jobs": [changed]}, board={"name": "Example"})
    summary = discover(client, auth)["summary"]
    assert by_source(summary)["greenhouse"]["updated"] == 1
    assert by_source(summary)["lever"]["unchanged"] == 1

    db_session.expire_all()
    new_hash = db_session.execute(
        select(JobSourcePosting.content_hash).where(JobSourcePosting.source == "greenhouse")
    ).scalar_one()
    assert new_hash != old_hash
    detail = get_job(client, auth, job_id)
    assert detail["requirements"] == ["At least eight years of ABAP experience"]
    assert detail["saved_at"] == saved_at


def test_posting_no_longer_listed_becomes_inactive_and_preference_moves(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    discover(client, auth)
    job_id = list_jobs(client, auth)["items"][0]["id"]

    # Greenhouse stops listing it (complete board read, posting missing).
    job_boards.greenhouse_board("example", jobs={"jobs": []}, board={"name": "Example GmbH"})
    discover(client, auth)
    detail = get_job(client, auth, job_id)
    postings = {p["source"]: p for p in detail["source_postings"]}
    assert postings["greenhouse"]["status"] == "inactive"
    assert postings["greenhouse"]["seen_in_latest_run"] is False
    assert postings["lever"]["status"] == "active"
    assert postings["lever"]["is_preferred"] is True
    assert detail["canonical_url"] == LEVER_URL
    assert detail["apply_url"] == f"{LEVER_URL}/apply"
    assert detail["is_active"] is True
    assert detail["source_postings"][0]["source"] == "lever"  # preferred first

    # Neither source lists it any more: kept, marked inactive, never deleted.
    job_boards.lever_site("example", postings=[])
    discover(client, auth)
    detail = get_job(client, auth, job_id)
    assert detail["is_active"] is False
    assert {p["status"] for p in detail["source_postings"]} == {"inactive"}
    assert count(db_session, Job) == 1 and count(db_session, JobSourcePosting) == 2

    # Listed again: active again.
    job_boards.greenhouse_board(
        "example", jobs={"jobs": [greenhouse_job(GH_ID)]}, board={"name": "Example GmbH"}
    )
    discover(client, auth)
    detail = get_job(client, auth, job_id)
    assert detail["is_active"] is True
    assert detail["source_postings"][0]["source"] == "greenhouse"


def test_failed_or_filtered_runs_never_mark_postings_inactive(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    discover(client, auth)
    # A failing board says nothing about which postings it lists.
    job_boards.set(GREENHOUSE_HOST, "/v1/boards/example/jobs", httpx.Response(503))
    job_boards.set(LEVER_HOST, "/v0/postings/example", httpx.Response(500))
    assert discover(client, auth)["status"] == "failed"
    # Postings filtered out by criteria are still listed by their source.
    job_boards.greenhouse_board(
        "example", jobs={"jobs": [greenhouse_job(GH_ID)]}, board={"name": "Example GmbH"}
    )
    job_boards.lever_site("example", postings=[lever_posting(LEVER_ID)])
    discover(client, auth, {"keywords": ["no such keyword"]})
    db_session.expire_all()
    assert all(p.is_active for p in db_session.execute(select(JobSourcePosting)).scalars())


def test_stale_postings_are_reported_not_deleted(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    discover(client, auth)
    db_session.execute(
        JobSourcePosting.__table__.update().values(
            last_seen_at=datetime.now(UTC) - timedelta(days=30)
        )
    )
    db_session.commit()
    job_id = list_jobs(client, auth)["items"][0]["id"]
    detail = get_job(client, auth, job_id)
    assert {p["status"] for p in detail["source_postings"]} == {"stale"}
    assert detail["is_active"] is True  # stale is not "gone"


# --- Migrated V0.3 data ------------------------------------------------------------------


def test_migrated_v03_job_is_backfilled_and_matched_by_a_new_source(
    client: TestClient, auth: dict[str, str], db_session: Session, register_and_login: Any
) -> None:
    """A V0.3 job as the migration leaves it: one posting with its V0.3
    identity, no comparison keys and no snapshot."""
    gh_raw = greenhouse_job(GH_ID)
    from app.services.job_discovery.sources.ats import Board
    from tests.job_helpers import greenhouse_source

    raw = greenhouse_source(FakeJobBoards()).to_raw_job(
        gh_raw, board=Board("example", "Example GmbH"), company_name="Example GmbH"
    )
    normalized = JobNormalizer().normalize(raw)
    when = datetime(2026, 10, 1, tzinfo=UTC)
    legacy_job = Job(
        id=uuid.uuid4(),
        canonical_url=normalized.canonical_url,
        discovered_at=when,
        last_seen_at=when,
        **{
            name: getattr(normalized, name)
            for name in ("title", "company_name", "location", "locations", "description",
                         "requirements", "responsibilities", "technologies", "employment_type")
        },
    )
    db_session.add(legacy_job)
    db_session.flush()
    legacy_posting = JobSourcePosting(
        job_id=legacy_job.id,
        source="greenhouse",
        source_job_id=str(GH_ID),
        dedup_key=f"greenhouse:id:{GH_ID}",
        source_company_key="example",
        source_url=GH_URL,
        canonical_source_url=GH_URL,
        title=legacy_job.title,
        company_name=legacy_job.company_name,
        location=legacy_job.location,
        content_hash="v03-hash",
        normalizer_version="job_normalizer_v1",
        first_seen_at=when,
        last_seen_at=when,
    )
    db_session.add(legacy_posting)
    db_session.flush()
    legacy_job.preferred_source_posting_id = legacy_posting.id
    db_session.commit()
    assert legacy_posting.identity_version is None and legacy_posting.normalized_data is None

    summary = discover(client, auth, {"sources": ["lever"]})["summary"]
    assert summary["merged"] == 1
    db_session.expire_all()
    assert count(db_session, Job) == 1
    posting = db_session.get(JobSourcePosting, legacy_posting.id)
    assert posting is not None and posting.identity_version is not None
    assert posting.normalized_company_name == "example"
    job = db_session.get(Job, legacy_job.id)
    assert job is not None
    assert job.discovered_at == when  # never moves later
    assert job.normalized_title == "senior sap abap developer"

    # The legacy posting is refreshed (and re-hashed) when its source returns it.
    summary = discover(client, auth, {"sources": ["greenhouse"]})["summary"]
    assert (summary["updated"], summary["created"]) == (1, 0)
    db_session.expire_all()
    posting = db_session.get(JobSourcePosting, legacy_posting.id)
    assert posting is not None and posting.normalized_data is not None
    assert posting.content_hash != "v03-hash"


# --- Concurrency -------------------------------------------------------------------------


def _user(db: Session, email: str) -> User:
    return db.execute(select(User).where(User.email == email)).scalar_one()


def _run_concurrently(*targets: Any) -> list[BaseException]:
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(targets))

    def wrap(target: Any) -> None:
        try:
            barrier.wait(timeout=10)
            target()
        except BaseException as exc:  # noqa: BLE001 -- reported to the test
            errors.append(exc)

    threads = [threading.Thread(target=wrap, args=(t,)) for t in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return errors


@pytest.mark.parametrize("split_sources", [False, True])
def test_concurrent_discovery_never_duplicates_postings_or_jobs(
    client: TestClient,
    register_and_login: Any,
    job_boards: FakeJobBoards,
    db_session: Session,
    split_sources: bool,
) -> None:
    """Two runs at the same time -- either both reading both sources, or one
    reading Greenhouse while the other reads Lever -- over several rounds of
    fresh vacancies."""
    register_and_login(email="a@example.com")
    register_and_login(email="b@example.com")
    rounds = 6
    for round_number in range(rounds):
        gh_id = 9000 + round_number
        lever_id = f"round-{round_number}"
        title = f"Senior SAP ABAP Developer {round_number}"
        job_boards.greenhouse_board(
            "example", jobs={"jobs": [greenhouse_job(gh_id, title=title)]},
            board={"name": "Example GmbH"},
        )
        job_boards.lever_site("example", postings=[lever_posting(lever_id, title=title)])
        registry = make_registry(
            job_boards, greenhouse="example:Example GmbH", lever="example:Example GmbH"
        )

        def run(
            email: str, sources: list[str] | None, registry: JobSourceRegistry = registry
        ) -> None:
            with TestSessionLocal() as db:
                JobDiscoveryService(registry).discover(
                    db, _user(db, email), JobSearchCriteria(sources=sources)
                )

        errors = _run_concurrently(
            lambda: run("a@example.com", ["greenhouse"] if split_sources else None),
            lambda: run("b@example.com", ["lever"] if split_sources else None),
        )
        assert errors == []

    db_session.expire_all()
    assert count(db_session, Job) == rounds
    assert count(db_session, JobSourcePosting) == 2 * rounds
    for job in db_session.execute(select(Job)).scalars():
        sources = {p.source for p in job.source_postings}
        assert sources == {"greenhouse", "lever"}, job.title
    duplicates = db_session.execute(
        select(JobSourcePosting.dedup_key)
        .group_by(JobSourcePosting.dedup_key)
        .having(func.count() > 1)
    ).all()
    assert duplicates == []


def test_identity_lock_serializes_resolution(
    job_boards: FakeJobBoards, db_session: Session
) -> None:
    """While one transaction holds a vacancy's identity lock (and hasn't
    committed its new job yet), another run resolving the same vacancy waits
    -- and then merges instead of creating a second job."""
    from app.services.job_discovery.sources.ats import Board
    from tests.job_helpers import greenhouse_source, lever_source

    board = Board("example", "Example GmbH")
    gh = JobNormalizer().normalize(
        greenhouse_source(job_boards).to_raw_job(
            greenhouse_job(GH_ID), board=board, company_name="Example GmbH"
        )
    )
    lever = JobNormalizer().normalize(
        lever_source(job_boards).to_raw_job(lever_posting(LEVER_ID), board=board)
    )
    now = datetime.now(UTC)
    holder = TestSessionLocal()
    lock_postings(holder, [gh])
    created = upsert_job(holder, gh, now=now)

    result: dict[str, Any] = {}

    def second_run() -> None:
        with TestSessionLocal() as db:
            lock_postings(db, [lever])
            result["outcome"] = upsert_job(db, lever, now=now)
            db.commit()

    thread = threading.Thread(target=second_run)
    thread.start()
    time.sleep(0.5)
    assert thread.is_alive(), "the second run should be waiting for the identity lock"
    holder.commit()
    holder.close()
    thread.join(timeout=30)
    assert not thread.is_alive()

    assert result["outcome"].outcome.value == "merged"
    assert result["outcome"].job_id == created.job_id
    assert count(db_session, Job) == 1
    assert count(db_session, JobSourcePosting) == 2
