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
