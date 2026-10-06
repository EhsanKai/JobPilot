"""V0.4.1 adapter hardening: bounded retries and error isolation.

All over mocked HTTP. These tests pin behavior the live validation harness
relies on; they don't claim anything about the real providers' responses.
"""

from typing import Any

import httpx
import psycopg.errors
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from app.services.job_discovery import discovery_service
from app.services.job_discovery.sources.greenhouse import API_HOST as GREENHOUSE_HOST
from app.services.job_discovery.sources.http import MAX_RETRY_AFTER_SECONDS
from tests.job_helpers import (
    LEVER_HOST,
    FakeJobBoards,
    greenhouse_source,
    lever_source,
    load_fixture,
)
from tests.test_job_sources import search

JOBS_PATH = "/v1/boards/acme/jobs"


def sequence(*responses: httpx.Response | Exception) -> Any:
    """A responder returning `responses` in order, then repeating the last."""
    remaining = list(responses)

    def respond(request: httpx.Request) -> httpx.Response:
        item = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(item, Exception):
            raise item
        return item

    return respond


def ok_jobs() -> httpx.Response:
    return httpx.Response(200, json=load_fixture("greenhouse_jobs.json"))


def jobs_requests(boards: FakeJobBoards) -> int:
    return sum(r.url.path == JOBS_PATH for r in boards.requests)


# --- Retries -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transient",
    [
        httpx.Response(503),
        httpx.Response(502),
        httpx.Response(500),
        httpx.Response(504),
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("reset"),
    ],
)
def test_transient_failures_are_retried_and_succeed(transient: Any) -> None:
    boards = FakeJobBoards()
    boards.greenhouse_board("acme")
    boards.set(GREENHOUSE_HOST, JOBS_PATH, sequence(transient, ok_jobs()))
    result = search(greenhouse_source(boards))
    assert result.targets_failed == 0 and result.issues == []
    assert len(result.jobs) == 5
    assert jobs_requests(boards) == 2
    assert result.retried_requests == 1
    # The fixture has one non-object entry, so the board is not "complete".
    assert (result.unreadable_items, result.complete_targets) == (1, [])


@pytest.mark.parametrize(
    ("responder", "code"),
    [
        (httpx.Response(503), "SOURCE_UNAVAILABLE"),
        (httpx.Response(429), "SOURCE_RATE_LIMITED"),
        (httpx.ReadTimeout("slow"), "SOURCE_TIMEOUT"),
        (httpx.ConnectError("refused"), "SOURCE_UNAVAILABLE"),
    ],
)
def test_retries_are_bounded_and_persistent_failures_are_reported(
    responder: Any, code: str
) -> None:
    boards = FakeJobBoards()
    boards.set(GREENHOUSE_HOST, JOBS_PATH, responder)
    result = search(greenhouse_source(boards, max_retries=2))
    assert jobs_requests(boards) == 3  # 1 attempt + 2 retries, never more
    assert result.retried_requests == 2
    assert result.targets_failed == 1
    assert result.issues[0].code == code


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(status: int) -> None:
    boards = FakeJobBoards()
    boards.set(GREENHOUSE_HOST, JOBS_PATH, httpx.Response(status))
    result = search(greenhouse_source(boards))
    assert jobs_requests(boards) == 1
    assert result.retried_requests == 0
    assert result.targets_failed == 1


def test_unreadable_responses_are_not_retried() -> None:
    boards = FakeJobBoards()
    boards.set(GREENHOUSE_HOST, JOBS_PATH, httpx.Response(200, content=b"<html>"))
    result = search(greenhouse_source(boards))
    assert jobs_requests(boards) == 1
    assert result.issues[0].code == "SOURCE_INVALID_RESPONSE"


def test_a_long_retry_after_is_not_waited_for() -> None:
    boards = FakeJobBoards()
    too_long = str(int(MAX_RETRY_AFTER_SECONDS) + 50)
    boards.set(GREENHOUSE_HOST, JOBS_PATH, httpx.Response(429, headers={"Retry-After": too_long}))
    result = search(greenhouse_source(boards))
    assert jobs_requests(boards) == 1
    assert result.issues[0].code == "SOURCE_RATE_LIMITED"


def test_retries_can_be_disabled() -> None:
    boards = FakeJobBoards()
    boards.set(GREENHOUSE_HOST, JOBS_PATH, httpx.Response(503))
    search(greenhouse_source(boards, max_retries=0))
    assert jobs_requests(boards) == 1


def test_backoff_is_exponential_and_honors_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("app.services.job_discovery.sources.http.asyncio.sleep", fake_sleep)
    boards = FakeJobBoards()
    boards.set(GREENHOUSE_HOST, JOBS_PATH, httpx.Response(503))
    search(greenhouse_source(boards, max_retries=2, retry_backoff_seconds=0.5))
    assert delays == [0.5, 1.0]

    delays.clear()
    boards.set(GREENHOUSE_HOST, JOBS_PATH, httpx.Response(429, headers={"Retry-After": "3"}))
    search(greenhouse_source(boards, max_retries=1, retry_backoff_seconds=0.5))
    assert delays == [3.0]


def test_lever_pages_are_retried_individually() -> None:
    boards = FakeJobBoards()
    postings = load_fixture("lever_postings.json")
    many = [{**postings[0], "id": f"id-{i}"} for i in range(150)]
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        skip = request.url.params["skip"]
        calls.append(skip)
        if skip == "100" and calls.count("100") == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=many[int(skip) : int(skip) + 100])

    boards.set(LEVER_HOST, "/v0/postings/globex", respond)
    result = search(lever_source(boards))
    assert len(result.jobs) == 150
    assert calls == ["0", "100", "100"]
    assert result.retried_requests == 1


@pytest.fixture
def auth(register_and_login: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {register_and_login()['access_token']}"}


def discover(client: TestClient, auth: dict[str, str], **criteria: Any) -> dict[str, Any]:
    response = client.post("/api/v1/jobs/discover", json={"criteria": criteria}, headers=auth)
    assert response.status_code == 202, response.text
    return client.get(f"/api/v1/jobs/discover/runs/{response.json()['id']}", headers=auth).json()


def test_summary_reports_retried_requests(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards
) -> None:
    job_boards.set(GREENHOUSE_HOST, JOBS_PATH, sequence(httpx.Response(503), ok_jobs()))
    run = discover(client, auth, sources=["greenhouse"])
    assert run["status"] == "completed"
    assert run["summary"]["retried_requests"] == 1
    assert run["summary"]["sources"][0]["retried_requests"] == 1
    assert run["summary"]["created"] == 3


# --- Error isolation ------------------------------------------------------------------------


def count(db: Session, model: Any) -> int:
    db.expire_all()
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def assert_counts_add_up(summary: dict[str, Any]) -> None:
    for source in summary["sources"]:
        stored = source["created"] + source["merged"] + source["updated"] + source["unchanged"]
        assert source["skipped"] == (
            source["invalid"] + source["duplicates"] + source["filtered_out"]
            + source["beyond_limit"]
        )
        assert source["fetched"] == source["skipped"] + stored
        assert source["error_count"] == len(source["errors"])


@pytest.mark.parametrize(
    "malformed",
    [
        {"offices": 5},  # wrong types where the adapter iterates lists
        {"departments": 7},
    ],
)
def test_one_malformed_greenhouse_record_does_not_sink_the_board(
    client: TestClient,
    auth: dict[str, str],
    job_boards: FakeJobBoards,
    db_session: Session,
    malformed: dict[str, Any],
) -> None:
    jobs = load_fixture("greenhouse_jobs.json")
    jobs["jobs"].append({**jobs["jobs"][0], "id": 999, **malformed})
    job_boards.greenhouse_board("acme", jobs=jobs)
    run = discover(client, auth, sources=["greenhouse"])
    summary = run["summary"]
    assert run["status"] == "completed"
    # The three valid postings are stored; the malformed one is counted as
    # invalid, not silently dropped (fixture: 3 invalid before).
    assert summary["created"] == 3
    assert summary["invalid"] == 4
    assert_counts_add_up(summary)


def test_a_record_with_an_unexpected_location_shape_is_kept_without_location(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards
) -> None:
    jobs = load_fixture("greenhouse_jobs.json")
    jobs["jobs"].append({**jobs["jobs"][0], "id": 999, "location": ["Berlin"], "offices": []})
    job_boards.greenhouse_board("acme", jobs=jobs)
    summary = discover(client, auth, sources=["greenhouse"])["summary"]
    assert (summary["created"], summary["invalid"]) == (4, 3)


def test_one_malformed_lever_record_does_not_sink_the_site(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards
) -> None:
    postings = load_fixture("lever_postings.json")
    postings.append({**postings[0], "id": "broken", "lists": 7})
    job_boards.lever_site("globex", postings=postings)
    summary = discover(client, auth, sources=["lever"])["summary"]
    assert (summary["created"], summary["invalid"]) == (2, 1)
    assert_counts_add_up(summary)


def test_unexpected_normalizer_error_skips_only_that_record(
    client: TestClient, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.job_discovery.normalizer import JobNormalizer

    original = JobNormalizer.normalize

    def flaky(self: JobNormalizer, raw: Any, **kwargs: Any) -> Any:
        if raw.title and "Frontend" in raw.title:
            raise ValueError("unexpected shape")
        return original(self, raw, **kwargs)

    monkeypatch.setattr(JobNormalizer, "normalize", flaky)
    run = discover(client, auth)
    assert run["status"] == "completed"
    lever = next(s for s in run["summary"]["sources"] if s["source"] == "lever")
    assert (lever["created"], lever["invalid"]) == (1, 1)
    assert_counts_add_up(run["summary"])


def test_a_posting_the_database_rejects_leaves_no_partial_job(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = discovery_service.upsert_job

    def rejecting(db: Session, job: Any, **kwargs: Any) -> Any:
        result = original(db, job, **kwargs)  # the job and posting are written...
        if job.source_job_id == "4012345":
            # ...then the database rejects something about this posting.
            raise DataError("INSERT ...", {}, psycopg.errors.StringDataRightTruncation())
        return result

    monkeypatch.setattr(discovery_service, "upsert_job", rejecting)
    run = discover(client, auth, sources=["greenhouse"])
    summary = run["summary"]
    assert run["status"] == "completed"
    assert summary["created"] == 2
    errors = summary["sources"][0]["errors"]
    assert errors == [
        {
            "code": "POSTING_REJECTED",
            "message": "A job posting could not be stored and was skipped.",
            "target": "acme",
        }
    ]
    assert_counts_add_up(summary)
    # Rolled back completely: no job or posting for it, nothing partial.
    assert count(db_session, Job) == 2
    assert count(db_session, JobSourcePosting) == 2
    assert not db_session.execute(
        select(JobSourcePosting).where(JobSourcePosting.source_job_id == "4012345")
    ).first()

    # Next run without the problem stores it normally, still no duplicates.
    monkeypatch.setattr(discovery_service, "upsert_job", original)
    summary = discover(client, auth, sources=["greenhouse"])["summary"]
    assert (summary["created"], summary["unchanged"]) == (1, 2)
    assert count(db_session, Job) == 3


def test_a_board_with_unreadable_records_never_inactivates_postings(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    jobs = load_fixture("greenhouse_jobs.json")
    job_boards.greenhouse_board("acme", jobs=jobs)
    discover(client, auth, sources=["greenhouse"])
    # The listing now lacks a stored posting but has a malformed record: the
    # missing one might be that record, so nothing may be marked inactive.
    jobs = load_fixture("greenhouse_jobs.json")
    jobs["jobs"][0] = {**jobs["jobs"][0], "offices": 5}
    job_boards.greenhouse_board("acme", jobs=jobs)
    discover(client, auth, sources=["greenhouse"])
    db_session.expire_all()
    assert all(p.is_active for p in db_session.execute(select(JobSourcePosting)).scalars())
