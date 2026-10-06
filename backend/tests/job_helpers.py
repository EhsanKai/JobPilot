"""Deterministic helpers for Job Discovery tests.

No test ever reaches a real job board: sources get an `httpx.MockTransport`
backed by fixtures in `tests/fixtures/jobs/`, and `conftest.py` makes any
real HTTP transport raise.
"""

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

from app.services.job_discovery.registry import JobSourceRegistry
from app.services.job_discovery.sources.ats import parse_board_config
from app.services.job_discovery.sources.greenhouse import API_HOST as GREENHOUSE_HOST
from app.services.job_discovery.sources.greenhouse import GreenhouseJobSource
from app.services.job_discovery.sources.http import JsonFetcher
from app.services.job_discovery.sources.lever import API_HOSTS as LEVER_HOSTS
from app.services.job_discovery.sources.lever import LeverJobSource

FIXTURES = Path(__file__).parent / "fixtures" / "jobs"
LEVER_HOST = LEVER_HOSTS["global"]

Responder = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeJobBoards:
    """A programmable stand-in for the Greenhouse and Lever APIs.

    Routes are keyed by (host, path). Unknown routes return 404. Every request
    is recorded so tests can assert what was (and wasn't) called.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Responder] = {}
        self.requests: list[httpx.Request] = []

    def set(self, host: str, path: str, responder: Responder) -> None:
        self.routes[(host, path)] = responder

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        responder = self.routes.get((request.url.host, request.url.path))
        if responder is None:
            return httpx.Response(404, json={"error": "not found"})
        if isinstance(responder, Exception):
            raise responder
        if callable(responder):
            return responder(request)
        return responder

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    # --- Canned sources -------------------------------------------------------------

    def greenhouse_board(self, token: str = "acme", jobs: Any = None, board: Any = None) -> None:
        self.set(
            GREENHOUSE_HOST,
            f"/v1/boards/{token}/jobs",
            httpx.Response(
                200, json=jobs if jobs is not None else load_fixture("greenhouse_jobs.json")
            ),
        )
        self.set(
            GREENHOUSE_HOST,
            f"/v1/boards/{token}",
            httpx.Response(
                200, json=board if board is not None else load_fixture("greenhouse_board.json")
            ),
        )

    def lever_site(self, site: str = "globex", postings: Any = None) -> None:
        data = postings if postings is not None else load_fixture("lever_postings.json")

        def respond(request: httpx.Request) -> httpx.Response:
            skip = int(request.url.params.get("skip", "0"))
            limit = int(request.url.params.get("limit", "100"))
            if not isinstance(data, list):
                return httpx.Response(200, json=data)
            return httpx.Response(200, json=data[skip : skip + limit])

        self.set(LEVER_HOST, f"/v0/postings/{site}", respond)

    def requests_to(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]


def fetcher_for(boards: FakeJobBoards, hosts: Iterable[str], **kwargs: Any) -> JsonFetcher:
    return JsonFetcher(
        allowed_hosts=frozenset(hosts),
        timeout_seconds=kwargs.get("timeout_seconds", 5.0),
        max_response_bytes=kwargs.get("max_response_bytes", 5_000_000),
        transport=boards.transport,
    )


def greenhouse_source(
    boards: FakeJobBoards, config: str = "acme", **kwargs: Any
) -> GreenhouseJobSource:
    return GreenhouseJobSource(
        parse_board_config(config),
        fetcher_for(boards, [GREENHOUSE_HOST], **kwargs),
        max_jobs_per_board=kwargs.get("max_jobs_per_board", 1000),
    )


def lever_source(
    boards: FakeJobBoards, config: str = "globex:Globex", **kwargs: Any
) -> LeverJobSource:
    return LeverJobSource(
        parse_board_config(config),
        fetcher_for(boards, [LEVER_HOST], **kwargs),
        max_jobs_per_board=kwargs.get("max_jobs_per_board", 1000),
    )


def make_registry(
    boards: FakeJobBoards,
    *,
    greenhouse: str = "acme",
    lever: str = "globex:Globex",
    enabled: Iterable[str] = ("greenhouse", "lever"),
) -> JobSourceRegistry:
    enabled = set(enabled)
    registry = JobSourceRegistry()
    registry.register(greenhouse_source(boards, greenhouse), enabled="greenhouse" in enabled)
    registry.register(lever_source(boards, lever), enabled="lever" in enabled)
    return registry


# --- Cross-source scenarios (V0.4) ----------------------------------------------------
# The same vacancy as Greenhouse and Lever would each publish it: different
# ids, URLs, HTML structure and location spelling, same text.

SAP_INTRO = (
    "Example GmbH builds SAP solutions for logistics and manufacturing customers across "
    "Europe. Our Cologne team modernizes core ERP processes and we are looking for an "
    "experienced developer who enjoys clean code, close collaboration with consultants "
    "and long term ownership of business critical systems."
)
SAP_RESPONSIBILITIES = (
    "Design, build and maintain ABAP developments in SAP S/4HANA",
    "Translate business requirements into robust technical solutions with our consultants",
    "Review code and mentor junior developers in modern ABAP practices",
)
SAP_REQUIREMENTS = (
    "At least five years of experience with ABAP and ABAP Objects",
    "Experience with CDS views, OData services and SAP Fiori integration",
    "Fluent German and good English communication skills",
)
OTHER_INTRO = (
    "Example GmbH is growing its finance platform team. In this role you will focus on "
    "payment processing, reconciliation reports and the integration of banking interfaces, "
    "working closely with our accounting department on month end closing topics and "
    "regulatory reporting requirements for international subsidiaries."
)
OTHER_RESPONSIBILITIES = (
    "Develop interfaces to banks and payment service providers",
    "Automate reconciliation and month end closing reports",
    "Support audits and regulatory reporting for subsidiaries",
)
OTHER_REQUIREMENTS = (
    "Background in financial accounting processes",
    "Experience with electronic bank statements and payment formats",
    "Structured way of working and attention to detail",
)


def _sections_html(intro: str, responsibilities: Iterable[str], requirements: Iterable[str]) -> str:
    items = lambda values: "".join(f"<li>{v}</li>" for v in values)  # noqa: E731
    return (
        f"<p>{intro}</p><h3>What you'll do</h3><ul>{items(responsibilities)}</ul>"
        f"<h3>What you bring</h3><ul>{items(requirements)}</ul>"
    )


def greenhouse_job(
    job_id: int,
    *,
    title: str = "Senior SAP ABAP Developer",
    location: str = "Cologne, Germany",
    intro: str = SAP_INTRO,
    responsibilities: Iterable[str] = SAP_RESPONSIBILITIES,
    requirements: Iterable[str] = SAP_REQUIREMENTS,
    url: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    import html

    return {
        "id": job_id,
        "title": title,
        "updated_at": "2026-10-01T10:00:00Z",
        "first_published": "2026-09-30T08:00:00Z",
        "location": {"name": location},
        "absolute_url": url or f"https://job-boards.greenhouse.io/example/jobs/{job_id}",
        "metadata": [{"name": "Employment Type", "value": "Full-time"}],
        "content": html.escape(_sections_html(intro, responsibilities, requirements)),
        **extra,
    }


def lever_posting(
    posting_id: str,
    *,
    site: str = "example",
    title: str = "Senior SAP ABAP Developer",
    location: str = "Köln",
    intro: str = SAP_INTRO,
    responsibilities: Iterable[str] = SAP_RESPONSIBILITIES,
    requirements: Iterable[str] = SAP_REQUIREMENTS,
    commitment: str = "Full Time",
    **extra: Any,
) -> dict[str, Any]:
    items = lambda values: "".join(f"<li>{v}</li>" for v in values)  # noqa: E731
    return {
        "id": posting_id,
        "text": title,
        "categories": {"location": location, "commitment": commitment, "allLocations": [location]},
        "country": "DE",
        "description": f"<div>{intro}</div>",
        "lists": [
            {"text": "What you'll do", "content": items(responsibilities)},
            {"text": "What you bring", "content": items(requirements)},
        ],
        "hostedUrl": f"https://jobs.lever.co/{site}/{posting_id}",
        "applyUrl": f"https://jobs.lever.co/{site}/{posting_id}/apply",
        "workplaceType": "unspecified",
        "createdAt": 1759219200000,
        **extra,
    }


def cross_source_registry(
    boards: FakeJobBoards,
    *,
    greenhouse_jobs: list[dict[str, Any]],
    lever_postings: list[dict[str, Any]],
    company: str = "Example GmbH",
) -> JobSourceRegistry:
    """Greenhouse board "example" and Lever site "example", both configured
    with the same company display name."""
    boards.greenhouse_board("example", jobs={"jobs": greenhouse_jobs}, board={"name": company})
    boards.lever_site("example", postings=lever_postings)
    return make_registry(boards, greenhouse=f"example:{company}", lever=f"example:{company}")
