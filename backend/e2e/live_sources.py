"""Live validation of the real Greenhouse and Lever APIs (V0.4.1).

    python -m e2e.live_sources                       # boards from .env / settings
    python -m e2e.live_sources --greenhouse getyourguide,hellofresh \\
        --lever "ppro:PPRO,netlight:Netlight" --capture tests/fixtures/jobs/live \\
        --report live-report.json

What it does -- production code only, **no mocks**:

1. Probe: one real GET per configured board on the endpoints the adapters
   use. Records DNS/network reachability, HTTP status, Retry-After, the
   response shape (top-level type, keys, item count, the first item's keys)
   -- never response content.
2. If at least one provider answered: recreate a scratch database
   (`<db>_live`), migrate it with Alembic, create a validation user and run
   `JobDiscoveryService.discover` twice through the real adapters,
   normalizer, persistence and identity resolution. Then analyze what was
   stored: field coverage per source, normalization anomalies, duplicate
   postings, cross-source matches, same-title/different-location
   separation, repeated-discovery stability.
3. `--capture DIR` saves the raw JSON each adapter received
   (`greenhouse/<board>.jobs.json`, `greenhouse/<board>.board.json`,
   `lever/<site>.json`) for the offline regression tests in
   `tests/test_live_sources.py`.

Exit status: 0 PASS (every provider reached, every check passed), 3 PARTIAL
(some provider/board unreachable, checks on the rest passed), 2 BLOCKED (no
provider reachable), 1 FAIL (a check failed). A provider is only ever
reported as validated after it actually returned HTTP 200 with JSON.
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, settings
from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from app.models.user import User
from app.schemas.jobs import DiscoverySummary, JobSearchCriteria
from app.services.job_discovery.discovery_service import JobDiscoveryService
from app.services.job_discovery.normalization.text import tokens
from app.services.job_discovery.registry import JobSourceRegistry, build_default_registry
from app.services.job_discovery.sources.ats import BoardJobSource, parse_board_config
from app.services.job_discovery.sources.greenhouse import API_BASE_URL as GREENHOUSE_API
from app.services.job_discovery.sources.http import USER_AGENT, JsonFetcher
from app.services.job_discovery.sources.lever import API_HOSTS as LEVER_HOSTS

PASS, PARTIAL, BLOCKED, FAIL = "PASS", "PARTIAL", "BLOCKED", "FAIL"
EXIT_CODES = {PASS: 0, FAIL: 1, BLOCKED: 2, PARTIAL: 3}


# --- 1. Probe ------------------------------------------------------------------------------


@dataclass
class Probe:
    provider: str
    board: str
    url: str
    reachable: bool = False
    http_status: int | None = None
    error: str | None = None
    retry_after: str | None = None
    shape: dict[str, Any] = field(default_factory=dict)


def _shape(data: object) -> dict[str, Any]:
    """Structure only, never content."""
    if isinstance(data, dict):
        items = data.get("jobs")
        shape: dict[str, Any] = {"type": "object", "keys": sorted(data)}
    elif isinstance(data, list):
        items = data
        shape = {"type": "array"}
    else:
        return {"type": type(data).__name__}
    if isinstance(items, list):
        shape["items"] = len(items)
        first = next((i for i in items if isinstance(i, dict)), None)
        if first is not None:
            shape["first_item_keys"] = sorted(first)
    return shape


def probe_urls(config: Settings) -> list[tuple[str, str, str]]:
    urls = [
        ("greenhouse", b.key, f"{GREENHOUSE_API}/{quote(b.key, safe='')}/jobs?content=true")
        for b in parse_board_config(config.GREENHOUSE_BOARD_TOKENS)
    ]
    host = LEVER_HOSTS.get(config.LEVER_INSTANCE.strip().lower(), LEVER_HOSTS["global"])
    urls += [
        ("lever", b.key, f"https://{host}/v0/postings/{quote(b.key, safe='')}?mode=json&limit=1")
        for b in parse_board_config(config.LEVER_SITES)
    ]
    return urls


async def probe(
    urls: list[tuple[str, str, str]], *, transport: httpx.AsyncBaseTransport | None = None
) -> list[Probe]:
    """One real request per board (the same headers the adapters send).
    `transport` exists for the harness's own tests; the CLI never sets it."""
    results: list[Probe] = []
    async with httpx.AsyncClient(
        transport=transport,
        timeout=20.0,
        follow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    ) as client:
        for provider, board, url in urls:
            result = Probe(provider=provider, board=board, url=url)
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                result.error = f"{type(exc).__name__}: {str(exc)[:200]}"
                results.append(result)
                continue
            result.http_status = response.status_code
            result.retry_after = response.headers.get("Retry-After")
            if response.status_code == 200:
                try:
                    result.shape = _shape(response.json())
                    result.reachable = True
                except ValueError:
                    result.error = "response is not JSON"
            results.append(result)
    return results


# --- Capture ----------------------------------------------------------------------------------


class CapturingJsonFetcher(JsonFetcher):
    """A production `JsonFetcher` that also keeps every JSON document it
    received, for offline regression fixtures."""

    def __init__(self, inner: JsonFetcher, sink: "Capture") -> None:
        self.__dict__.update(inner.__dict__)
        self._sink = sink

    async def get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        target: str | None = None,
    ) -> Any:
        data = await super().get_json(client, url, params=params, target=target)
        self._sink.add(url, target or "unknown", data, params)
        return data


@dataclass
class Capture:
    greenhouse_jobs: dict[str, Any] = field(default_factory=dict)
    greenhouse_boards: dict[str, Any] = field(default_factory=dict)
    lever: dict[str, list[Any]] = field(default_factory=lambda: defaultdict(list))
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def add(self, url: str, board: str, data: Any, params: Mapping[str, str] | None) -> None:
        # Discovery runs more than once; keep each document once.
        key = (url, json.dumps(dict(params or {}), sort_keys=True))
        if key in self._seen:
            return
        self._seen.add(key)
        if "greenhouse.io" in url:
            target = self.greenhouse_jobs if url.endswith("/jobs") else self.greenhouse_boards
            target[board] = data
        elif isinstance(data, list):
            self.lever[board].extend(data)

    def write(self, directory: Path) -> list[str]:
        written: list[str] = []
        files = [
            *((f"greenhouse/{b}.jobs.json", d) for b, d in self.greenhouse_jobs.items()),
            *((f"greenhouse/{b}.board.json", d) for b, d in self.greenhouse_boards.items()),
            *((f"lever/{b}.json", d) for b, d in self.lever.items()),
        ]
        for name, data in files:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            written.append(str(path))
        return written


def capturing(registry: JobSourceRegistry, sink: Capture) -> JobSourceRegistry:
    for source in registry.all():
        if isinstance(source, BoardJobSource):
            source._fetcher = CapturingJsonFetcher(source._fetcher, sink)
    return registry


# --- 2. Discovery + analysis -------------------------------------------------------------------


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def coverage(postings: list[JobSourcePosting]) -> dict[str, dict[str, Any]]:
    """Field coverage and anomalies per source, from the stored postings'
    normalized snapshots (what the pipeline actually produced)."""
    by_source: dict[str, list[JobSourcePosting]] = defaultdict(list)
    for posting in postings:
        by_source[posting.source].append(posting)
    report: dict[str, dict[str, Any]] = {}
    for source, items in sorted(by_source.items()):
        counts: Counter[str] = Counter()
        unrecognized: Counter[str] = Counter()
        for p in items:
            data = p.normalized_data or {}
            words = len(tokens(" ".join([data.get("description") or ""])))
            counts["description"] += bool(data.get("description"))
            counts["short_description_lt_30_words"] += 0 < words < 30
            counts["requirements"] += bool(data.get("requirements"))
            counts["responsibilities"] += bool(data.get("responsibilities"))
            counts["location"] += bool(p.location)
            counts["multiple_locations"] += len(data.get("locations") or []) > 1
            counts["location_recognized"] += bool(
                p.normalized_location and not p.normalized_location.startswith("text:")
            )
            counts["remote"] += bool(
                p.normalized_location and p.normalized_location.startswith("remote")
            )
            counts["department"] += bool(data.get("department"))
            counts["employment_type"] += bool(data.get("employment_type"))
            counts["work_model"] += bool(data.get("work_model"))
            counts["salary_structured"] += data.get("salary_min") is not None or (
                data.get("salary_max") is not None
            )
            counts["salary_text_only"] += bool(data.get("salary_text")) and (
                data.get("salary_min") is None and data.get("salary_max") is None
            )
            counts["apply_url"] += bool(p.apply_url)
            counts["posted_at"] += p.posted_at is not None
            counts["tracking_params_removed"] += bool(
                p.source_url and p.source_url != p.canonical_source_url
            )
            if p.normalized_location and p.normalized_location.startswith("text:"):
                unrecognized[p.location or ""] += 1
        report[source] = {
            "postings": len(items),
            "coverage_pct": {k: _pct(v, len(items)) for k, v in sorted(counts.items())},
            "unrecognized_locations_top": unrecognized.most_common(15),
        }
    return report


def analyze(db: Session, runs: list[DiscoverySummary], first_seen: dict[str, datetime]) -> dict:
    postings = list(db.execute(select(JobSourcePosting)).scalars())
    jobs = list(db.execute(select(Job)).scalars())
    checks: list[Check] = []

    dup_keys = db.execute(
        select(JobSourcePosting.dedup_key).group_by(JobSourcePosting.dedup_key)
        .having(func.count() > 1)
    ).all()
    checks.append(Check("no duplicate source postings", not dup_keys, f"{len(dup_keys)} dupes"))
    orphan_jobs = [j.id for j in jobs if not any(p.job_id == j.id for p in postings)]
    checks.append(
        Check("every canonical job has a posting", not orphan_jobs, f"{len(orphan_jobs)} orphans")
    )

    if len(runs) >= 2:
        second = runs[1]
        checks.append(Check(
            "repeated discovery creates no jobs",
            second.created == 0 and second.merged == 0,
            f"created={second.created} merged={second.merged}",
        ))
        moved = [k for k, v in first_seen.items() if any(
            p.dedup_key == k and p.first_seen_at != v for p in postings
        )]
        checks.append(Check("first_seen_at stable", not moved, f"{len(moved)} moved"))
        seen_again = [p for p in postings if p.dedup_key in first_seen]
        stale = [p for p in seen_again if p.last_seen_at <= first_seen[p.dedup_key]]
        checks.append(Check(
            "last_seen_at advanced on repeat",
            not stale,
            f"{len(seen_again) - len(stale)}/{len(seen_again)} advanced",
        ))

    blocks: dict[tuple[str | None, str | None], set[Any]] = defaultdict(set)
    locations: dict[Any, set[str | None]] = defaultdict(set)
    for p in postings:
        blocks[(p.normalized_company_name, p.normalized_title)].add(p.job_id)
        locations[p.job_id].add(p.normalized_location)
    split = {k: v for k, v in blocks.items() if len(v) > 1}
    same_title_different_location = [
        {"company": k[0], "title": k[1], "jobs": len(v),
         "locations": sorted({loc or "-" for job in v for loc in locations[job]})}
        for k, v in split.items()
    ]
    multi_source = [
        {"title": j.title, "company": j.company_name,
         "sources": sorted({p.source for p in postings if p.job_id == j.id})}
        for j in jobs
        if len({p.source for p in postings if p.job_id == j.id}) > 1
    ]
    return {
        "checks": [asdict(c) for c in checks],
        "totals": {"canonical_jobs": len(jobs), "source_postings": len(postings)},
        "cross_source_jobs": multi_source,
        "same_company_title_kept_separate": same_title_different_location[:50],
        "coverage": coverage(postings),
    }


def run_discovery(
    session_factory: sessionmaker[Session], registry: JobSourceRegistry, *, repeats: int = 2
) -> tuple[list[DiscoverySummary], dict]:
    with session_factory() as db:
        user = db.execute(
            select(User).where(User.email == "live-validation@jobpilot.invalid")
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email="live-validation@jobpilot.invalid",
                password_hash="!",  # cannot log in
                first_name="Live",
                last_name="Validation",
            )
            db.add(user)
            db.commit()
        runs: list[DiscoverySummary] = []
        first_seen: dict[str, datetime] = {}
        service = JobDiscoveryService(registry)
        for index in range(repeats):
            runs.append(service.discover(db, user, JobSearchCriteria(limit=200)))
            if index == 0:
                rows = db.execute(
                    select(JobSourcePosting.dedup_key, JobSourcePosting.first_seen_at)
                ).all()
                first_seen = {key: seen for key, seen in rows}
        db.expire_all()
        return runs, analyze(db, runs, first_seen)


def overall_status(probes: list[Probe], analysis: dict | None) -> str:
    providers = {p.provider for p in probes}
    reached = {p.provider for p in probes if p.reachable}
    if not reached:
        return BLOCKED
    if analysis and not all(c["passed"] for c in analysis["checks"]):
        return FAIL
    if reached != providers or not all(p.reachable for p in probes):
        return PARTIAL
    return PASS


def validate(
    config: Settings,
    *,
    session_factory: Callable[[], sessionmaker[Session]],
    registry: JobSourceRegistry | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    capture_dir: Path | None = None,
) -> dict[str, Any]:
    """Probe, then (if anything is reachable) discover twice and analyze.
    `session_factory` is only called when discovery actually runs."""
    probes = asyncio.run(probe(probe_urls(config), transport=transport))
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "probes": [asdict(p) for p in probes],
        "providers": {
            provider: {
                "boards": sum(p.provider == provider for p in probes),
                "reachable_boards": sum(p.provider == provider and p.reachable for p in probes),
            }
            for provider in sorted({p.provider for p in probes})
        },
        "runs": [],
        "analysis": None,
        "captured_files": [],
    }
    analysis = None
    if any(p.reachable for p in probes):
        sink = Capture()
        registry = registry or build_default_registry(config)
        if capture_dir is not None:
            capturing(registry, sink)
        runs, analysis = run_discovery(session_factory(), registry)
        report["runs"] = [run.model_dump(mode="json") for run in runs]
        report["analysis"] = analysis
        if capture_dir is not None:
            report["captured_files"] = sink.write(capture_dir)
    report["status"] = overall_status(probes, analysis)
    return report


def _scratch_session_factory() -> sessionmaker[Session]:
    """Recreate and migrate `<db>_live`, the same way the E2E launcher does."""
    from sqlalchemy.engine import make_url

    from e2e.serve import _reset_database

    base = make_url(settings.DATABASE_URL)
    url = os.environ.get(
        "LIVE_DATABASE_URL",
        base.set(database=f"{base.database}_live").render_as_string(hide_password=False),
    )
    settings.DATABASE_URL = url
    _reset_database(url)
    from sqlalchemy import create_engine

    return sessionmaker(bind=create_engine(url), autoflush=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--greenhouse", help="board tokens, e.g. 'getyourguide,grover'")
    parser.add_argument("--lever", help="sites, e.g. 'ppro:PPRO,netlight:Netlight'")
    parser.add_argument("--lever-instance", choices=["global", "eu"])
    parser.add_argument("--capture", type=Path, help="directory for raw response fixtures")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    args = parser.parse_args(argv)

    overrides: dict[str, Any] = {}
    if args.greenhouse is not None:
        overrides["GREENHOUSE_BOARD_TOKENS"] = args.greenhouse
    if args.lever is not None:
        overrides["LEVER_SITES"] = args.lever
    if args.lever_instance:
        overrides["LEVER_INSTANCE"] = args.lever_instance
    config = settings.model_copy(update=overrides)

    report = validate(
        config, session_factory=_scratch_session_factory, capture_dir=args.capture
    )
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nLIVE SOURCE VALIDATION: {report['status']}", file=sys.stderr)
    return EXIT_CODES[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
