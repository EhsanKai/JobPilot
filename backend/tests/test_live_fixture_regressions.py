"""Regressions derived from real Greenhouse/Lever responses (V0.4.1).

The fixtures in tests/fixtures/jobs/live/ were captured from the live APIs by
`python -m e2e.live_sources --capture` (2026-10-06). Assertions are written
against properties of whatever is captured, not today's ids or counts, so a
re-capture keeps them meaningful. Skipped when no capture exists.
"""

import json
import uuid
from collections import defaultdict
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.services.job_discovery.deduplication.resolver import (
    CandidateJob,
    MatchDecision,
    PostingFacts,
    compare,
    resolve,
)
from app.services.job_discovery.normalizer import JobNormalizer, NormalizedJob
from app.services.job_discovery.sources.ats import Board
from tests.job_helpers import FakeJobBoards, greenhouse_source, lever_source

LIVE = Path(__file__).parent / "fixtures" / "jobs" / "live"
GREENHOUSE = sorted((LIVE / "greenhouse").glob("*.jobs.json")) if LIVE.is_dir() else []
LEVER = sorted((LIVE / "lever").glob("*.json")) if LIVE.is_dir() else []
pytestmark = pytest.mark.skipif(not GREENHOUSE and not LEVER, reason="no captured real responses")


@cache
def greenhouse_jobs() -> list[NormalizedJob]:
    adapter = greenhouse_source(FakeJobBoards())
    jobs = []
    for path in GREENHOUSE:
        token = path.name.removesuffix(".jobs.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data["jobs"]:
            raw = adapter.to_raw_job(item, board=Board(token), company_name=None)
            jobs.append(JobNormalizer().normalize(raw))
    return jobs


def lever_items() -> list[tuple[str, dict[str, Any]]]:
    return [
        (path.stem, item)
        for path in LEVER
        for item in json.loads(path.read_text(encoding="utf-8"))
    ]


def facts(job: NormalizedJob, source: str | None = None) -> PostingFacts:
    """The posting's facts; with `source`, as if another source listed the
    same text under its own URL."""
    url = f"https://{source}.example/postings/{job.source_job_id}" if source else job.canonical_url
    return PostingFacts.from_content(
        source=source or job.source, url=url, content=job.content_fields()
    )


# --- Greenhouse -----------------------------------------------------------------------------


@pytest.mark.skipif(not GREENHOUSE, reason="no captured Greenhouse responses")
def test_every_real_greenhouse_posting_normalizes_cleanly() -> None:
    jobs = greenhouse_jobs()
    assert jobs
    for job in jobs:
        # Company identity comes from the posting's own company_name field
        # (present on real list responses).
        assert job.company_name and job.company_name != job.source_company_key
        # Entity-encoded source HTML never leaks tags into stored text.
        assert "<p>" not in (job.description or "") and "<li>" not in (job.description or "")
        # Required query parameters (careers-site ?gh_jid=...) are kept.
        if "gh_jid=" in (job.source_url or ""):
            assert "gh_jid=" in job.canonical_url
        assert job.posted_at is not None  # first_published is on list responses


@pytest.mark.skipif(not GREENHOUSE, reason="no captured Greenhouse responses")
def test_real_same_board_twins_stay_separate_and_block_cross_source_merges() -> None:
    """Real boards list distinct vacancies with the same title, location and
    near-identical text (e.g. two openings at one site). They must stay
    separate, and a matching posting from another source must not be merged
    into either one (ambiguous)."""
    groups: dict[tuple[str, str, str | None], list[NormalizedJob]] = defaultdict(list)
    for job in greenhouse_jobs():
        f = facts(job)
        groups[(f.identity.company_key, f.identity.title_key, f.identity.location_key)].append(job)
    twins = [jobs for jobs in groups.values() if len({j.source_job_id for j in jobs}) > 1]
    if not twins:
        pytest.skip("this capture has no same-board twins")
    for first, second, *_ in twins:
        assert compare(facts(second), facts(first)).decision is MatchDecision.NO_MATCH
        incoming = facts(first, source="other_source")
        candidates = [
            CandidateJob(
                job_id=uuid.uuid4(), sources=frozenset({"greenhouse"}), postings=(facts(job),)
            )
            for job in (first, second)
        ]
        resolution = resolve(incoming, candidates)
        assert resolution.job_id is None
        assert resolution.decision in (MatchDecision.POSSIBLE_MATCH, MatchDecision.NO_MATCH)


@pytest.mark.skipif(not GREENHOUSE and not LEVER, reason="no captured responses")
def test_real_same_title_in_different_locations_never_matches() -> None:
    jobs = greenhouse_jobs()
    by_title: dict[tuple[str, str], list[NormalizedJob]] = defaultdict(list)
    for job in jobs:
        f = facts(job)
        by_title[(f.identity.company_key, f.identity.title_key)].append(job)
    pairs = [
        (a, b)
        for group in by_title.values()
        for a in group
        for b in group
        if facts(a).identity.location_key != facts(b).identity.location_key
    ]
    if not pairs:
        pytest.skip("this capture has no same-title/different-location postings")
    for a, b in pairs:
        # Even if they came from two different sources.
        assert compare(facts(a, source="other_source"), facts(b)).decision is not (
            MatchDecision.HIGH_CONFIDENCE
        )
        assert compare(facts(a, source="other_source"), facts(b)).decision is not (
            MatchDecision.EXACT
        )


# --- Lever ------------------------------------------------------------------------------------


@pytest.mark.skipif(not LEVER, reason="no captured Lever responses")
def test_real_lever_postings_normalize_into_the_vocabulary() -> None:
    adapter = lever_source(FakeJobBoards())
    for site, item in lever_items():
        job = JobNormalizer().normalize(adapter.to_raw_job(item, board=Board(site, site.title())))
        workplace = item.get("workplaceType")
        # Real values are "onsite"/"hybrid"/"remote" (no hyphen).
        expected = {
            "onsite": RemotePreference.ONSITE,
            "on-site": RemotePreference.ONSITE,
            "hybrid": RemotePreference.HYBRID,
            "remote": RemotePreference.REMOTE,
        }.get(workplace or "")
        if expected is not None:
            assert job.work_model is expected, workplace
        commitment = (item.get("categories") or {}).get("commitment") or ""
        if "working student" in commitment.lower():
            assert job.employment_type is EmploymentTypeValue.WORKING_STUDENT
        elif "internship" in commitment.lower():
            assert job.employment_type is EmploymentTypeValue.INTERNSHIP
        elif commitment.lower() == "event":
            assert job.employment_type is None  # not an employment type: stays unknown
        assert job.apply_url and job.apply_url.startswith("https://jobs.lever.co/")
        assert job.company_name == site.title()  # configured identity, never invented


@pytest.mark.skipif(not LEVER, reason="no captured Lever responses")
def test_an_existing_lever_site_without_postings_is_a_complete_empty_board() -> None:
    empty = [path.stem for path in LEVER if json.loads(path.read_text()) == []]
    if not empty:
        pytest.skip("no empty Lever site captured")
    import asyncio

    from app.schemas.jobs import JobSearchCriteria

    boards = FakeJobBoards()
    for site in empty:
        boards.lever_site(site, postings=[])
    result = asyncio.run(
        lever_source(boards, ",".join(empty)).search(JobSearchCriteria())
    )
    assert result.jobs == [] and result.issues == []
    assert sorted(result.complete_targets) == sorted(empty)
