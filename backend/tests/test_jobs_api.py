"""Job Discovery API: discovery runs, persistence, dedup, refresh, ownership.

Sources are real adapters (Greenhouse, Lever) over mocked HTTP
(`FakeJobBoards`), so these tests exercise the whole pipeline
API -> service -> adapter -> normalizer -> DB without network access.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.discovery_run import DiscoveryRun, DiscoveryRunStatus
from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from app.models.user_job import UserJob
from app.services.job_discovery.sources.greenhouse import API_HOST as GREENHOUSE_HOST
from tests.job_helpers import LEVER_HOST, FakeJobBoards, load_fixture


@pytest.fixture
def auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = register_and_login()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def other_auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = register_and_login(email="mallory@example.com")["access_token"]
    return {"Authorization": f"Bearer {token}"}


def discover(
    client: TestClient, auth: dict[str, str], criteria: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Start a run and return its final state (TestClient runs background
    tasks before returning)."""
    body = {"criteria": criteria} if criteria is not None else None
    response = client.post("/api/v1/jobs/discover", json=body, headers=auth)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    run = client.get(f"/api/v1/jobs/discover/runs/{run_id}", headers=auth)
    assert run.status_code == 200, run.text
    return run.json()


def list_jobs(client: TestClient, auth: dict[str, str], **params: Any) -> dict[str, Any]:
    response = client.get("/api/v1/jobs", params=params, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def job_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Job)).scalar_one()


# --- Authentication & authorization -----------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/jobs"),
        ("get", f"/api/v1/jobs/{uuid.uuid4()}"),
        ("put", f"/api/v1/jobs/{uuid.uuid4()}/save"),
        ("delete", f"/api/v1/jobs/{uuid.uuid4()}/save"),
        ("post", "/api/v1/jobs/discover"),
        ("get", "/api/v1/jobs/discover/runs/latest"),
        ("get", f"/api/v1/jobs/discover/runs/{uuid.uuid4()}"),
        ("get", "/api/v1/jobs/sources"),
        ("get", "/api/v1/jobs/criteria/defaults"),
    ],
)
def test_endpoints_require_authentication(client: TestClient, method: str, path: str) -> None:
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_users_only_see_their_own_jobs_and_runs(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str]
) -> None:
    run = discover(client, auth)
    job_id = list_jobs(client, auth)["items"][0]["id"]

    assert list_jobs(client, other_auth)["total"] == 0
    for method, path in [
        ("get", f"/api/v1/jobs/{job_id}"),
        ("put", f"/api/v1/jobs/{job_id}/save"),
        ("delete", f"/api/v1/jobs/{job_id}/save"),
        ("get", f"/api/v1/jobs/discover/runs/{run['id']}"),
    ]:
        response = getattr(client, method)(path, headers=other_auth)
        assert response.status_code == 404, path
    assert client.get("/api/v1/jobs/discover/runs/latest", headers=other_auth).json() is None


def test_same_posting_discovered_by_two_users_is_one_job(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    discover(client, auth)
    count = job_count(db_session)
    discover(client, other_auth)
    assert job_count(db_session) == count
    assert db_session.execute(select(func.count()).select_from(UserJob)).scalar_one() == 2 * count
    assert list_jobs(client, other_auth)["total"] == count


# --- Discovery ---------------------------------------------------------------------------------


def test_discovery_creates_jobs_and_reports_a_summary(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    run = discover(client, auth)

    assert run["status"] == "completed"
    assert run["is_active"] is False
    assert run["started_at"] and run["finished_at"]
    summary = run["summary"]
    assert summary["fetched"] == 8  # 6 greenhouse entries (one unreadable) + 2 lever
    assert summary["invalid"] == 3  # blank title, javascript: URL, non-object entry
    assert summary["created"] == 5
    assert summary["discovered"] == 5
    assert summary["updated"] == summary["unchanged"] == summary["duplicates"] == 0

    by_source = {s["source"]: s for s in summary["sources"]}
    assert by_source["greenhouse"]["status"] == "succeeded"
    assert by_source["greenhouse"]["created"] == 3
    assert by_source["greenhouse"]["invalid"] == 3
    assert by_source["lever"]["created"] == 2
    assert job_count(db_session) == 5


def test_discovered_jobs_are_normalized_and_keep_source_metadata(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    discover(client, auth)
    # Since V0.4 source identity and provenance live on the source posting;
    # the canonical Job carries the normalized content.
    posting = db_session.execute(
        select(JobSourcePosting).where(
            JobSourcePosting.source == "greenhouse", JobSourcePosting.source_job_id == "4012345"
        )
    ).scalar_one()
    job = db_session.get(Job, posting.job_id)
    assert job is not None
    assert job.preferred_source_posting_id == posting.id
    assert job.title == "Senior Backend Engineer (Python)"
    assert job.company_name == "Acme GmbH"
    assert posting.dedup_key == "greenhouse:id:4012345"
    assert posting.source_company_key == "acme"
    assert job.canonical_url == "https://job-boards.greenhouse.io/acme/jobs/4012345"
    assert posting.canonical_source_url == job.canonical_url
    assert posting.source_url is not None and "utm_source=linkedin" in posting.source_url
    assert job.employment_type is not None and job.employment_type.value == "full_time"
    assert job.seniority is not None and job.seniority.value == "senior"
    assert job.salary_min == 70000 and job.salary_currency == "EUR"
    assert set(job.technologies) >= {"Python", "PostgreSQL", "Kubernetes", "AWS"}
    assert job.posted_at == datetime(2026, 9, 20, 8, tzinfo=UTC)
    assert posting.raw_data is not None and posting.raw_data["id"] == 4012345
    assert posting.content_hash and posting.normalizer_version == "job_normalizer_v2"

    designer = db_session.execute(
        select(Job).join(Job.source_postings).where(JobSourcePosting.source_job_id == "4012346")
    ).scalar_one()
    # Required query parameter kept, fragment dropped.
    assert designer.canonical_url == "https://acme.example.com/careers?gh_jid=4012346"
    assert designer.work_model is not None and designer.work_model.value == "remote"


def test_discovery_with_criteria_filters_and_reports_who_applied_them(
    client: TestClient, auth: dict[str, str]
) -> None:
    run = discover(
        client,
        auth,
        {
            "keywords": ["python", "typescript"],
            "locations": ["Berlin"],
            "industries": ["Logistics"],
        },
    )
    summary = run["summary"]
    assert summary["discovered"] == 2  # Greenhouse backend role + Lever frontend role
    titles = {j["title"] for j in list_jobs(client, auth)["items"]}
    assert titles == {"Senior Backend Engineer (Python)", "Frontend Engineer"}

    filters = {f["filter"]: f["applied_by"] for f in summary["sources"][0]["filters"]}
    assert filters == {
        "keywords": "jobpilot",
        "locations": "jobpilot",
        "industries": "not_applied",  # no source provides industries; never pretended
    }
    assert summary["filtered_out"] == 3


def test_discovery_limit_and_page(client: TestClient, auth: dict[str, str]) -> None:
    run = discover(client, auth, {"limit": 1, "sources": ["greenhouse"]})
    assert run["summary"]["discovered"] == 1
    assert run["summary"]["beyond_limit"] == 2
    # Newest posting first.
    assert list_jobs(client, auth)["items"][0]["title"].startswith("Werkstudent")

    second = discover(client, auth, {"limit": 1, "page": 2, "sources": ["greenhouse"]})
    assert second["summary"]["created"] == 1
    assert list_jobs(client, auth)["total"] == 2


def test_discovery_without_body_uses_job_preferences(
    client: TestClient, auth: dict[str, str]
) -> None:
    client.post(
        "/api/v1/preferences/job-titles",
        json={"title": "Frontend Engineer", "priority": "primary"},
        headers=auth,
    )
    defaults = client.get("/api/v1/jobs/criteria/defaults", headers=auth).json()
    assert defaults["job_titles"] == ["Frontend Engineer"]

    run = discover(client, auth)
    assert run["criteria"]["job_titles"] == ["Frontend Engineer"]
    assert [j["title"] for j in list_jobs(client, auth)["items"]] == ["Frontend Engineer"]


def test_criteria_defaults_reflect_preferences(client: TestClient, auth: dict[str, str]) -> None:
    client.put(
        "/api/v1/preferences",
        json={
            "minimum_salary": 65000,
            "salary_currency": "EUR",
            "work_models": ["remote", "hybrid"],
            "employment_types": ["full_time"],
            "preferred_industries": ["Fintech"],
        },
        headers=auth,
    )
    client.post("/api/v1/preferences/locations", json={"location": "Berlin"}, headers=auth)
    defaults = client.get("/api/v1/jobs/criteria/defaults", headers=auth).json()
    assert defaults["locations"] == ["Berlin"]
    assert set(defaults["work_models"]) == {"remote", "hybrid"}
    assert defaults["employment_types"] == ["full_time"]
    assert defaults["industries"] == ["Fintech"]
    assert float(defaults["salary_min"]) == 65000
    assert defaults["salary_currency"] == "EUR"


@pytest.mark.parametrize(
    "criteria",
    [{"sources": ["linkedin"]}, {"salary_min": 100, "salary_max": 1}, {"limit": 5000}],
)
def test_invalid_discovery_requests_are_rejected(
    client: TestClient, auth: dict[str, str], criteria: dict[str, Any], db_session: Session
) -> None:
    response = client.post("/api/v1/jobs/discover", json={"criteria": criteria}, headers=auth)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert db_session.execute(select(func.count()).select_from(DiscoveryRun)).scalar_one() == 0


def test_a_second_run_is_rejected_while_one_is_active(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    user_id = client.get("/api/v1/users/me", headers=auth).json()["id"]
    run = DiscoveryRun(user_id=uuid.UUID(user_id), status=DiscoveryRunStatus.RUNNING, criteria={})
    run.started_at = datetime.now(UTC)
    db_session.add(run)
    db_session.commit()

    response = client.post("/api/v1/jobs/discover", headers=auth)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["run_id"] == str(run.id)

    # A run abandoned long ago (e.g. server restart) no longer blocks.
    run.started_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.commit()
    assert discover(client, auth)["status"] == "completed"
    db_session.refresh(run)
    assert run.status == DiscoveryRunStatus.FAILED
    assert run.error_code == "ABANDONED"


def test_latest_run(client: TestClient, auth: dict[str, str]) -> None:
    assert client.get("/api/v1/jobs/discover/runs/latest", headers=auth).json() is None
    run = discover(client, auth)
    latest = client.get("/api/v1/jobs/discover/runs/latest", headers=auth).json()
    assert latest["id"] == run["id"]


# --- Failure handling ------------------------------------------------------------------------


def test_partial_source_failure_keeps_successful_results(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards
) -> None:
    job_boards.set(LEVER_HOST, "/v0/postings/globex", httpx.Response(503))
    run = discover(client, auth)

    assert run["status"] == "partial"
    by_source = {s["source"]: s for s in run["summary"]["sources"]}
    assert by_source["greenhouse"]["status"] == "succeeded"
    assert by_source["lever"]["status"] == "failed"
    assert by_source["lever"]["errors"][0]["code"] == "SOURCE_UNAVAILABLE"
    assert list_jobs(client, auth)["total"] == 3


def test_all_sources_failing_fails_the_run_without_crashing(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards
) -> None:
    job_boards.set(LEVER_HOST, "/v0/postings/globex", httpx.ConnectError("down"))
    job_boards.set(GREENHOUSE_HOST, "/v1/boards/acme/jobs", httpx.ReadTimeout("slow"))
    run = discover(client, auth)
    assert run["status"] == "failed"
    assert run["error_code"] == "ALL_SOURCES_FAILED"
    assert run["error_message"] == "No job source could be reached. Please try again later."
    codes = {s["source"]: s["errors"][0]["code"] for s in run["summary"]["sources"]}
    assert codes == {"lever": "SOURCE_UNAVAILABLE", "greenhouse": "SOURCE_TIMEOUT"}


def test_malformed_source_payload_is_reported_not_raised(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards
) -> None:
    job_boards.set(GREENHOUSE_HOST, "/v1/boards/acme/jobs", httpx.Response(200, content=b"{oops"))
    run = discover(client, auth, {"sources": ["greenhouse"]})
    assert run["status"] == "failed"
    error = run["summary"]["sources"][0]["errors"][0]
    assert error == {
        "code": "SOURCE_INVALID_RESPONSE",
        "message": "The job source returned data JobPilot could not read.",
        "target": "acme",
    }


def test_unexpected_errors_are_recorded_with_a_generic_message(
    client: TestClient, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.job_discovery import discovery_service

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database password=hunter2 leaked in message")

    monkeypatch.setattr(discovery_service, "upsert_job", boom)
    run = discover(client, auth)
    assert run["status"] == "failed"
    assert run["error_code"] == "DISCOVERY_FAILED"
    assert "hunter2" not in str(run)


def test_rate_limited_board(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards
) -> None:
    job_boards.set(GREENHOUSE_HOST, "/v1/boards/acme/jobs", httpx.Response(429))
    run = discover(client, auth, {"sources": ["greenhouse"]})
    assert run["summary"]["sources"][0]["errors"][0]["code"] == "SOURCE_RATE_LIMITED"


# --- Deduplication & refresh -----------------------------------------------------------------


def test_repeated_discovery_never_duplicates_and_preserves_identity(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    discover(client, auth)
    before = {
        p.dedup_key: (j.id, j.discovered_at, j.updated_at)
        for p, j in db_session.execute(select(JobSourcePosting, Job).join(JobSourcePosting.job))
    }
    job_id = list_jobs(client, auth)["items"][0]["id"]
    client.put(f"/api/v1/jobs/{job_id}/save", headers=auth)

    run = discover(client, auth)
    summary = run["summary"]
    assert summary["created"] == 0
    assert summary["unchanged"] == 5
    db_session.expire_all()
    after = {
        p.dedup_key: (j.id, j.discovered_at, j.updated_at)
        for p, j in db_session.execute(select(JobSourcePosting, Job).join(JobSourcePosting.job))
    }
    assert after.keys() == before.keys()
    for key, (job_id_before, discovered_before, updated_before) in before.items():
        job_id_after, discovered_after, updated_after = after[key]
        assert job_id_after == job_id_before
        assert discovered_after == discovered_before
        assert updated_after >= updated_before
    # Saved state survives a refresh.
    assert client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()["saved"] is True


def test_refresh_updates_changed_jobs_and_adds_new_ones(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    discover(client, auth)
    jobs = load_fixture("greenhouse_jobs.json")
    jobs["jobs"][0]["title"] = "Staff Backend Engineer (Python)"
    jobs["jobs"].append(
        {
            "id": 4019999,
            "title": "Site Reliability Engineer",
            "location": {"name": "Hamburg, Germany"},
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/4019999",
            "updated_at": "2026-10-01T00:00:00Z",
        }
    )
    # A posting that disappeared from the source is NOT deleted.
    del jobs["jobs"][1]
    job_boards.greenhouse_board("acme", jobs=jobs)

    summary = discover(client, auth, {"sources": ["greenhouse"]})["summary"]
    assert (summary["created"], summary["updated"], summary["unchanged"]) == (1, 1, 1)
    db_session.expire_all()
    updated = db_session.execute(
        select(Job).join(Job.source_postings).where(JobSourcePosting.source_job_id == "4012345")
    ).scalar_one()
    assert updated.title == "Staff Backend Engineer (Python)"
    assert updated.seniority is not None and updated.seniority.value == "lead"
    assert db_session.execute(
        select(Job).join(Job.source_postings).where(JobSourcePosting.source_job_id == "4012346")
    ).scalar_one()
    assert job_count(db_session) == 6


def test_duplicates_within_one_run_are_counted_once(
    client: TestClient, auth: dict[str, str], job_registry: Any, job_boards: FakeJobBoards
) -> None:
    job_boards.greenhouse_board("acme2")  # same postings listed on a second board
    from tests.job_helpers import greenhouse_source

    job_registry._sources["greenhouse"] = greenhouse_source(job_boards, "acme,acme2")
    summary = discover(client, auth, {"sources": ["greenhouse"]})["summary"]
    assert summary["created"] == 3
    assert summary["duplicates"] == 3


def test_jobs_without_source_id_deduplicate_by_canonical_url(
    client: TestClient, auth: dict[str, str], job_boards: FakeJobBoards, db_session: Session
) -> None:
    posting = {
        "title": "No-ID Job",
        "location": {"name": "Berlin"},
        "absolute_url": "https://acme.example.com/jobs/no-id?utm_campaign=a",
    }
    job_boards.greenhouse_board("acme", jobs={"jobs": [posting]})
    discover(client, auth, {"sources": ["greenhouse"]})
    posting["absolute_url"] = "https://ACME.example.com/jobs/no-id?utm_campaign=b#x"
    job_boards.greenhouse_board("acme", jobs={"jobs": [posting]})
    summary = discover(client, auth, {"sources": ["greenhouse"]})["summary"]
    # Same posting (canonical URL identical): never a second row. The raw
    # source_url changed, so it counts as an update.
    assert (summary["created"], summary["updated"]) == (0, 1)
    assert job_count(db_session) == 1
    posting = db_session.execute(select(JobSourcePosting)).scalar_one()
    assert posting.source_job_id is None
    assert posting.dedup_key.startswith("greenhouse:url:")


# --- Listing, detail, saving -------------------------------------------------------------------


def test_list_jobs_pagination_and_filters(client: TestClient, auth: dict[str, str]) -> None:
    discover(client, auth)

    page1 = list_jobs(client, auth, page_size=2)
    assert (page1["total"], page1["page"], page1["total_pages"], len(page1["items"])) == (
        5,
        1,
        3,
        2,
    )
    page3 = list_jobs(client, auth, page_size=2, page=3)
    assert len(page3["items"]) == 1
    seen = {j["id"] for j in page1["items"]} | {
        j["id"] for j in list_jobs(client, auth, page_size=2, page=2)["items"]
    }
    assert len(seen | {j["id"] for j in page3["items"]}) == 5

    assert list_jobs(client, auth, source="lever")["total"] == 2
    assert list_jobs(client, auth, q="python")["total"] == 1
    assert list_jobs(client, auth, q="100%_")["total"] == 0  # LIKE wildcards are escaped
    # Comma-separated terms are OR'ed, like discovery keywords.
    assert list_jobs(client, auth, q="python, typescript")["total"] == 2
    assert list_jobs(client, auth, location="Munich, Hamburg")["total"] == 2
    assert list_jobs(client, auth, location="hamburg")["total"] == 1  # matches allLocations
    assert list_jobs(client, auth, work_model="remote")["total"] == 2
    assert list_jobs(client, auth, work_model=["remote", "hybrid"])["total"] == 3
    assert list_jobs(client, auth, employment_type="contract")["total"] == 1
    assert list_jobs(client, auth, posted_after="2026-09-21")["total"] == 1


def test_list_rejects_invalid_parameters(client: TestClient, auth: dict[str, str]) -> None:
    for params in ({"page": 0}, {"page_size": 1000}, {"work_model": "underwater"}, {"sort": "x"}):
        response = client.get("/api/v1/jobs", params=params, headers=auth)
        assert response.status_code == 422, params


def test_job_detail_returns_full_information_and_original_url(
    client: TestClient, auth: dict[str, str]
) -> None:
    discover(client, auth)
    item = next(
        j
        for j in list_jobs(client, auth)["items"]
        if j["source"] == "lever" and j["title"] == "Frontend Engineer"
    )
    assert (
        item["canonical_url"] == "https://jobs.lever.co/globex/5ac21346-8e0c-4494-8e7a-3eb92ff77902"
    )

    detail = client.get(f"/api/v1/jobs/{item['id']}", headers=auth).json()
    assert detail["company_name"] == "Globex"
    assert detail["work_model"] == "hybrid"
    assert detail["employment_type"] == "full_time"
    assert detail["salary_min"] == "60000.00" and detail["salary_period"] == "year"
    assert detail["requirements"] == ["3+ years of TypeScript and React", "Experience with Next.js"]
    assert detail["responsibilities"] == ["Build our planning UI", "Improve accessibility"]
    assert {"TypeScript", "React", "Next.js"} <= set(detail["technologies"])
    assert detail["apply_url"].endswith("/apply")
    assert detail["source"] == "lever" and detail["source_job_id"]
    assert detail["locations"] == ["Berlin", "Hamburg"]
    assert detail["first_discovered_by_you_at"]
    assert "raw_data" not in detail  # source payloads are never exposed


def test_unknown_job_is_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.get(f"/api/v1/jobs/{uuid.uuid4()}", headers=auth).status_code == 404
    assert client.get("/api/v1/jobs/not-a-uuid", headers=auth).status_code == 422


def test_save_and_unsave(client: TestClient, auth: dict[str, str]) -> None:
    discover(client, auth)
    job_id = list_jobs(client, auth)["items"][0]["id"]

    saved = client.put(f"/api/v1/jobs/{job_id}/save", headers=auth).json()
    assert saved["saved"] is True and saved["saved_at"]
    again = client.put(f"/api/v1/jobs/{job_id}/save", headers=auth).json()
    assert again["saved_at"] == saved["saved_at"]  # idempotent

    saved_list = list_jobs(client, auth, saved="true", sort="saved")
    assert [j["id"] for j in saved_list["items"]] == [job_id]

    unsaved = client.delete(f"/api/v1/jobs/{job_id}/save", headers=auth).json()
    assert unsaved["saved"] is False and unsaved["saved_at"] is None
    assert list_jobs(client, auth, saved="true")["total"] == 0


def test_sources_endpoint_describes_sources_without_secrets(
    client: TestClient, auth: dict[str, str]
) -> None:
    sources = client.get("/api/v1/jobs/sources", headers=auth).json()
    by_name = {s["name"]: s for s in sources}
    assert by_name["greenhouse"]["enabled"] is True
    assert by_name["greenhouse"]["acquisition_method"] == "ats"
    assert by_name["greenhouse"]["target_count"] == 1
    assert by_name["greenhouse"]["native_filters"] == []
    assert "keywords" in by_name["lever"]["jobpilot_filters"]
    assert "industries" not in by_name["lever"]["jobpilot_filters"]
    assert "api_key" not in str(sources).lower()
