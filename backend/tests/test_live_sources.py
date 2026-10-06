"""Live source validation (V0.4.1).

Three groups, deliberately kept apart so mocked results can never be
mistaken for live ones:

1. The validation harness itself (`e2e/live_sources.py`), over mocked HTTP:
   it reports BLOCKED when nothing answers and analyzes stored data
   correctly when something does. Says nothing about the real providers.
2. Live tests (`live_network`): real requests to Greenhouse and Lever
   through the production pipeline. Skipped unless JOBPILOT_LIVE_SOURCES=1;
   when enabled, an unreachable provider is a failure, not a skip.
3. Regression tests over *captured real responses* in
   tests/fixtures/jobs/live/ (written by `python -m e2e.live_sources
   --capture ...`). Skipped while no capture exists.
"""

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from e2e import live_sources
from tests.conftest import TestSessionLocal
from tests.job_helpers import (
    FakeJobBoards,
    cross_source_registry,
    greenhouse_job,
    lever_posting,
    make_registry,
)

LIVE = os.environ.get("JOBPILOT_LIVE_SOURCES") == "1"
CAPTURED = Path(__file__).parent / "fixtures" / "jobs" / "live"


def config(
    greenhouse: str = "example:Example GmbH", lever: str = "example:Example GmbH"
) -> Settings:
    return settings.model_copy(
        update={"GREENHOUSE_BOARD_TOKENS": greenhouse, "LEVER_SITES": lever}
    )


# --- 1. The harness (mocked HTTP) -------------------------------------------------------------


def test_harness_reports_blocked_when_no_provider_answers() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("403 Forbidden")

    report = live_sources.validate(
        config(),
        session_factory=lambda: pytest.fail("no discovery may run when blocked"),  # type: ignore[arg-type,return-value]
        transport=httpx.MockTransport(refuse),
    )
    assert report["status"] == live_sources.BLOCKED
    assert report["runs"] == [] and report["analysis"] is None
    assert {p["error"] for p in report["probes"]} == {"ProxyError: 403 Forbidden"}
    assert all(not p["reachable"] for p in report["probes"])


def test_harness_reports_partial_when_one_provider_is_unreachable(
    job_boards: FakeJobBoards,
) -> None:
    registry = cross_source_registry(
        job_boards, greenhouse_jobs=[greenhouse_job(1)], lever_postings=[lever_posting("a")]
    )
    job_boards.routes = {k: v for k, v in job_boards.routes.items() if "lever" not in k[0]}
    report = live_sources.validate(
        config(),
        session_factory=lambda: TestSessionLocal,
        registry=registry,
        transport=job_boards.transport,
    )
    assert report["status"] == live_sources.PARTIAL
    assert report["providers"]["lever"]["reachable_boards"] == 0
    assert report["probes"][-1]["http_status"] == 404


def test_harness_validates_a_reachable_pipeline_and_captures_responses(
    job_boards: FakeJobBoards, tmp_path: Path
) -> None:
    registry = cross_source_registry(
        job_boards,
        greenhouse_jobs=[greenhouse_job(1), greenhouse_job(2, location="Munich, Germany")],
        lever_postings=[lever_posting("a"), lever_posting("b", location="München")],
    )
    report = live_sources.validate(
        config(),
        session_factory=lambda: TestSessionLocal,
        registry=registry,
        transport=job_boards.transport,
        capture_dir=tmp_path,
    )
    assert report["status"] == live_sources.PASS, report["analysis"]["checks"]
    probe = report["probes"][0]
    assert probe["http_status"] == 200 and probe["shape"]["items"] == 2
    assert "absolute_url" in probe["shape"]["first_item_keys"]

    analysis = report["analysis"]
    assert {c["name"] for c in analysis["checks"]} >= {
        "no duplicate source postings",
        "repeated discovery creates no jobs",
        "first_seen_at stable",
        "last_seen_at advanced on repeat",
    }
    assert analysis["totals"] == {"canonical_jobs": 2, "source_postings": 4}
    assert len(analysis["cross_source_jobs"]) == 2
    separated = analysis["same_company_title_kept_separate"][0]
    assert separated["jobs"] == 2 and separated["locations"] == ["de:cologne", "de:munich"]
    greenhouse = analysis["coverage"]["greenhouse"]
    assert greenhouse["postings"] == 2
    assert greenhouse["coverage_pct"]["location_recognized"] == 100.0
    assert report["runs"][1]["created"] == 0

    files = sorted(Path(f).relative_to(tmp_path).as_posix() for f in report["captured_files"])
    # A configured display name means the board endpoint is never fetched.
    assert files == ["greenhouse/example.jobs.json", "lever/example.json"]
    lever = json.loads((tmp_path / "lever/example.json").read_text())
    assert [p["id"] for p in lever] == ["a", "b"]  # captured once despite two runs


def test_harness_fails_when_a_check_fails() -> None:
    probes = [live_sources.Probe("greenhouse", "x", "u", reachable=True)]
    analysis = {"checks": [{"name": "no duplicate source postings", "passed": False}]}
    assert live_sources.overall_status(probes, analysis) == live_sources.FAIL


def test_harness_cli_exit_codes() -> None:
    assert live_sources.EXIT_CODES == {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "PARTIAL": 3}


# --- 2. Live (real network, opt-in) ---------------------------------------------------------


@pytest.mark.live_network
@pytest.mark.skipif(not LIVE, reason="live sources disabled; set JOBPILOT_LIVE_SOURCES=1")
def test_live_greenhouse_and_lever_through_the_production_pipeline() -> None:
    # conftest blanks the board settings for the offline suite; live runs use
    # LIVE_GREENHOUSE_BOARDS / LIVE_LEVER_SITES, else JobPilot's default boards.
    defaults = Settings.model_fields
    boards = config(
        greenhouse=os.environ.get(
            "LIVE_GREENHOUSE_BOARDS", str(defaults["GREENHOUSE_BOARD_TOKENS"].default)
        ),
        lever=os.environ.get("LIVE_LEVER_SITES", str(defaults["LEVER_SITES"].default)),
    )
    assert live_sources.probe_urls(boards), "no boards configured for the live test"
    report = live_sources.validate(boards, session_factory=lambda: TestSessionLocal)
    print(json.dumps(report, indent=2, default=str))
    assert report["status"] != live_sources.BLOCKED, (
        "JOBPILOT_LIVE_SOURCES=1 but no provider was reachable: "
        + "; ".join(f"{p['provider']}/{p['board']}: {p['error']}" for p in report["probes"])
    )
    assert report["status"] in (live_sources.PASS, live_sources.PARTIAL), report["analysis"]


# --- 3. Captured real responses ------------------------------------------------------------


def _captured(provider: str, pattern: str) -> list[Path]:
    return sorted((CAPTURED / provider).glob(pattern)) if (CAPTURED / provider).is_dir() else []


GREENHOUSE_CAPTURES = _captured("greenhouse", "*.jobs.json")
LEVER_CAPTURES = _captured("lever", "*.json")
NO_CAPTURE = (
    "no captured real responses in tests/fixtures/jobs/live/; capture them with "
    "`python -m e2e.live_sources --capture tests/fixtures/jobs/live` where the APIs are reachable"
)


@pytest.fixture
def auth(register_and_login: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {register_and_login()['access_token']}"}


def _discover(client: TestClient, auth: dict[str, str], source: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/jobs/discover", json={"criteria": {"sources": [source], "limit": 200}},
        headers=auth,
    )
    assert response.status_code == 202
    return client.get(f"/api/v1/jobs/discover/runs/{response.json()['id']}", headers=auth).json()


def _replay(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    job_boards: FakeJobBoards,
    registry: Any,
    source: str,
    serve: Any,
    items: list[Any],
) -> None:
    serve(items)
    first = _discover(client, auth, source)
    assert first["status"] in ("completed", "partial"), first
    summary = first["summary"]
    stored = summary["created"] + summary["merged"] + summary["updated"] + summary["unchanged"]
    assert summary["fetched"] == summary["skipped"] + stored
    jobs = db.execute(select(func.count()).select_from(Job)).scalar_one()
    postings = db.execute(select(func.count()).select_from(JobSourcePosting)).scalar_one()
    assert postings == stored

    again = _discover(client, auth, source)["summary"]
    assert (again["created"], again["merged"]) == (0, 0)
    db.expire_all()
    assert db.execute(select(func.count()).select_from(Job)).scalar_one() == jobs
    assert db.execute(select(func.count()).select_from(JobSourcePosting)).scalar_one() == postings

    # Disappearance, simulated on the captured listing (never on the real board).
    if len(items) > 1 and summary["invalid"] == 0:
        serve(items[1:])
        _discover(client, auth, source)
        db.expire_all()
        inactive = db.execute(
            select(func.count()).select_from(JobSourcePosting)
            .where(JobSourcePosting.is_active.is_(False))
        ).scalar_one()
        assert inactive >= 1


@pytest.mark.skipif(not GREENHOUSE_CAPTURES, reason=NO_CAPTURE)
@pytest.mark.parametrize("capture", GREENHOUSE_CAPTURES, ids=[p.stem for p in GREENHOUSE_CAPTURES])
def test_captured_greenhouse_board_through_the_pipeline(
    client: TestClient, auth: dict[str, str], db_session: Session, job_boards: FakeJobBoards,
    job_registry: Any, capture: Path,
) -> None:
    token = capture.name.removesuffix(".jobs.json")
    data = json.loads(capture.read_text(encoding="utf-8"))
    board_file = capture.with_name(f"{token}.board.json")
    board = json.loads(board_file.read_text()) if board_file.exists() else {"name": token}
    job_registry._sources["greenhouse"] = make_registry(job_boards, greenhouse=token).all()[0]

    def serve(items: list[Any]) -> None:
        job_boards.greenhouse_board(token, jobs={**data, "jobs": items}, board=board)

    _replay(client, auth, db_session, job_boards, job_registry, "greenhouse", serve, data["jobs"])


@pytest.mark.skipif(not LEVER_CAPTURES, reason=NO_CAPTURE)
@pytest.mark.parametrize("capture", LEVER_CAPTURES, ids=[p.stem for p in LEVER_CAPTURES])
def test_captured_lever_site_through_the_pipeline(
    client: TestClient, auth: dict[str, str], db_session: Session, job_boards: FakeJobBoards,
    job_registry: Any, capture: Path,
) -> None:
    site = capture.stem
    data = json.loads(capture.read_text(encoding="utf-8"))
    job_registry._sources["lever"] = make_registry(job_boards, lever=site).all()[1]

    def serve(items: list[Any]) -> None:
        job_boards.lever_site(site, postings=items)

    _replay(client, auth, db_session, job_boards, job_registry, "lever", serve, data)
