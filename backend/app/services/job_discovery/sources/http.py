"""A deliberately narrow JSON fetcher for job-source adapters.

Security properties (no SSRF surface):

- Only `https` URLs on a fixed, per-source host allowlist can be fetched.
  Hosts come from code, never from user input or configuration; config only
  supplies board/site *names*, which adapters validate and URL-encode into a
  path on an allowlisted host.
- Redirects are not followed, so an allowlisted host can't bounce the
  request elsewhere.
- Responses are size-capped and read as a stream.
- Errors are mapped to user-safe `SourceError`s. Response bodies are never
  included in error messages or logs.
"""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.logging import get_logger
from app.services.job_discovery.sources.base import (
    SourceError,
    SourceHTTPError,
    SourceNotFoundError,
    SourceRateLimitedError,
    SourceResponseError,
    SourceTimeoutError,
    SourceUnavailableError,
)

logger = get_logger(__name__)

USER_AGENT = "JobPilot/0.3 (job discovery; +https://github.com/EhsanKai/JobPilot)"


class JsonFetcher:
    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        timeout_seconds: float,
        max_response_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._allowed_hosts = allowed_hosts
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        # Tests inject an `httpx.MockTransport`; production uses the default.
        self._transport = transport

    @asynccontextmanager
    async def session(self) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        ) as client:
            yield client

    def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in self._allowed_hosts:
            # A programming error, not a source failure: never fetch it.
            raise ValueError("Refusing to fetch a URL outside the source's allowed hosts.")
        if parts.username or parts.password:
            raise ValueError("Refusing to fetch a URL containing credentials.")

    async def get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        target: str | None = None,
    ) -> Any:
        self._check_url(url)
        try:
            async with client.stream("GET", url, params=params) as response:
                self._raise_for_status(response, target)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_bytes:
                        raise SourceResponseError(
                            "The job source returned more data than JobPilot accepts.",
                            target=target,
                        )
        except SourceError:
            raise
        except httpx.TimeoutException:
            raise SourceTimeoutError(target=target) from None
        except httpx.TransportError as exc:
            logger.warning(
                "Job source request failed host=%s target=%s error=%s",
                urlsplit(url).hostname,
                target,
                type(exc).__name__,
            )
            raise SourceUnavailableError(target=target) from None

        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SourceResponseError(target=target) from None

    @staticmethod
    def _raise_for_status(response: httpx.Response, target: str | None) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        logger.warning(
            "Job source returned HTTP %s host=%s target=%s",
            status,
            response.request.url.host,
            target,
        )
        if status == 404:
            raise SourceNotFoundError(target=target)
        if status == 429:
            raise SourceRateLimitedError(target=target)
        if 500 <= status < 600:
            raise SourceUnavailableError(
                f"The job source is currently unavailable (HTTP {status}).", target=target
            )
        raise SourceHTTPError(f"The job source returned HTTP {status}.", target=target)
