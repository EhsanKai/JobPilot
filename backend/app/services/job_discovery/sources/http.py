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

Retries (V0.4.1) -- bounded and observable:

- Retried: connection failures, timeouts, HTTP 429 and 500/502/503/504.
  Never retried: 404 and other 4xx, oversized or unreadable responses.
- At most `max_retries` extra attempts per request (default 2), with
  exponential backoff (`retry_backoff_seconds` * 2^n). A `Retry-After`
  header (seconds) is honored up to `MAX_RETRY_AFTER_SECONDS`; a provider
  asking for longer is not waited for -- the failure is reported instead.
- Every retry is logged (host, board, attempt, reason) and counted per
  session (`retries(client)`), and the discovery summary reports the count
  (`retried_requests`). A request that still fails is reported as failed:
  persistent provider failures are never hidden.
"""

import asyncio
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

USER_AGENT = "JobPilot/0.4 (job discovery; +https://github.com/EhsanKai/JobPilot)"

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 10.0


class _Retryable(Exception):
    """Internal: a failed attempt that may be retried."""

    def __init__(self, error: SourceError, *, reason: str, retry_after: float | None = None):
        self.error = error
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(reason)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "").strip()
    if not value.isdigit():
        return None  # HTTP-date or absent: use the normal backoff
    return float(value)


class JsonFetcher:
    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        timeout_seconds: float,
        max_response_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self._allowed_hosts = allowed_hosts
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        # Tests inject an `httpx.MockTransport`; production uses the default.
        self._transport = transport
        self._max_retries = max(0, max_retries)
        self._backoff = max(0.0, retry_backoff_seconds)
        # Retries per open session (keyed by client), see `retries()`.
        self._retries: dict[int, int] = {}

    @asynccontextmanager
    async def session(self) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        ) as client:
            self._retries[id(client)] = 0
            try:
                yield client
            finally:
                self._retries.pop(id(client), None)

    def retries(self, client: httpx.AsyncClient) -> int:
        """Retries made so far in this session."""
        return self._retries.get(id(client), 0)

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
        attempt = 0
        while True:
            try:
                return await self._get_json_once(client, url, params=params, target=target)
            except _Retryable as failure:
                delay = self._backoff * (2**attempt)
                if failure.retry_after is not None:
                    if failure.retry_after > MAX_RETRY_AFTER_SECONDS:
                        raise failure.error from None  # don't stall a run for minutes
                    delay = max(delay, failure.retry_after)
                if attempt >= self._max_retries:
                    raise failure.error from None
                attempt += 1
                self._retries[id(client)] = self._retries.get(id(client), 0) + 1
                logger.warning(
                    "Retrying job source request host=%s target=%s attempt=%d/%d reason=%s "
                    "delay=%.1fs",
                    urlsplit(url).hostname,
                    target,
                    attempt,
                    self._max_retries,
                    failure.reason,
                    delay,
                )
                if delay:
                    await asyncio.sleep(delay)

    async def _get_json_once(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Mapping[str, str] | None,
        target: str | None,
    ) -> Any:
        try:
            async with client.stream("GET", url, params=params) as response:
                if response.status_code in RETRYABLE_STATUS:
                    error = self._error_for_status(response, target)
                    raise _Retryable(
                        error,
                        reason=f"http_{response.status_code}",
                        retry_after=_retry_after_seconds(response),
                    )
                self._raise_for_status(response, target)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_bytes:
                        raise SourceResponseError(
                            "The job source returned more data than JobPilot accepts.",
                            target=target,
                        )
        except (SourceError, _Retryable):
            raise
        except httpx.TimeoutException:
            raise _Retryable(SourceTimeoutError(target=target), reason="timeout") from None
        except httpx.TransportError as exc:
            logger.warning(
                "Job source request failed host=%s target=%s error=%s",
                urlsplit(url).hostname,
                target,
                type(exc).__name__,
            )
            raise _Retryable(
                SourceUnavailableError(target=target), reason=type(exc).__name__
            ) from None

        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SourceResponseError(target=target) from None

    @classmethod
    def _raise_for_status(cls, response: httpx.Response, target: str | None) -> None:
        if 200 <= response.status_code < 300:
            return
        raise cls._error_for_status(response, target)

    @staticmethod
    def _error_for_status(response: httpx.Response, target: str | None) -> SourceError:
        status = response.status_code
        logger.warning(
            "Job source returned HTTP %s host=%s target=%s",
            status,
            response.request.url.host,
            target,
        )
        if status == 404:
            return SourceNotFoundError(target=target)
        if status == 429:
            return SourceRateLimitedError(target=target)
        if 500 <= status < 600:
            return SourceUnavailableError(
                f"The job source is currently unavailable (HTTP {status}).", target=target
            )
        return SourceHTTPError(f"The job source returned HTTP {status}.", target=target)
