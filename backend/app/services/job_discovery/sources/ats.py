"""Shared plumbing for ATS sources that read one public job board per company.

Greenhouse and Lever both work this way: each company has a board/site name,
and the source reads every configured board. This base class handles what
they share: validating board names, reading boards concurrently, and
isolating failures so one broken board never discards the others.
"""

import asyncio
import re
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger
from app.schemas.jobs import JobSearchCriteria
from app.services.job_discovery.raw_job import RawJob
from app.services.job_discovery.sources.base import (
    AcquisitionMethod,
    JobSource,
    SourceError,
    SourceIssue,
    SourceNotConfiguredError,
    SourceSearchResult,
)
from app.services.job_discovery.sources.http import JsonFetcher

logger = get_logger(__name__)

# Board/site names become a URL path segment on a fixed host. Restricting
# them to this alphabet (and URL-encoding anyway) means configuration can
# never change which host or path family is requested.
_BOARD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_MAX_CONCURRENT_BOARDS = 4


@dataclass(frozen=True)
class Board:
    #: The board/site name at the source.
    key: str
    #: Company display name, when configured.
    company_name: str | None = None


@dataclass
class BoardResult:
    jobs: list[RawJob]
    unreadable_items: int = 0


def is_valid_board_name(name: str) -> bool:
    return bool(_BOARD_NAME_RE.fullmatch(name))


def parse_board_config(value: str) -> list[Board]:
    """Parse `"key1,key2:Display Name"` into boards. Invalid names are
    dropped (and logged), never fetched."""
    boards: list[Board] = []
    seen: set[str] = set()
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        key, _, display = entry.partition(":")
        key = key.strip()
        if not is_valid_board_name(key):
            logger.warning("Ignoring invalid job board name in configuration")
            continue
        if key.lower() in seen:
            continue
        seen.add(key.lower())
        boards.append(Board(key=key, company_name=display.strip() or None))
    return boards


class BoardJobSource(JobSource):
    acquisition_method = AcquisitionMethod.ATS

    def __init__(self, boards: list[Board], fetcher: JsonFetcher, *, max_jobs_per_board: int):
        self.boards = boards
        self._fetcher = fetcher
        self._max_jobs_per_board = max_jobs_per_board

    @property
    def target_count(self) -> int:
        return len(self.boards)

    async def search(self, criteria: JobSearchCriteria) -> SourceSearchResult:
        if not self.boards:
            raise SourceNotConfiguredError()

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BOARDS)

        async with self._fetcher.session() as client:

            async def read(board: Board) -> BoardResult | SourceError:
                async with semaphore:
                    try:
                        return await self.fetch_board(client, board)
                    except SourceError as exc:
                        exc.target = exc.target or board.key
                        return exc

            outcomes = await asyncio.gather(*(read(board) for board in self.boards))
            retried = self._fetcher.retries(client)

        result = SourceSearchResult(targets_attempted=len(self.boards), retried_requests=retried)
        for board, outcome in zip(self.boards, outcomes, strict=True):
            if isinstance(outcome, SourceError):
                result.targets_failed += 1
                result.issues.append(SourceIssue.from_error(outcome))
                logger.info(
                    "Job board failed source=%s board=%s code=%s",
                    self.name,
                    board.key,
                    outcome.code,
                )
                continue
            result.jobs.extend(outcome.jobs[: self._max_jobs_per_board])
            result.unreadable_items += outcome.unreadable_items
            if outcome.unreadable_items == 0 and len(outcome.jobs) < self._max_jobs_per_board:
                result.complete_targets.append(board.key)
        return result

    def read_items(
        self, items: list[object], board: Board, to_raw_job: Callable[[dict[str, Any]], RawJob]
    ) -> BoardResult:
        """Convert a board's items one by one. An item that isn't an object,
        or whose shape the adapter can't handle, is counted as unreadable and
        logged -- it never discards the board's other postings."""
        jobs: list[RawJob] = []
        unreadable = 0
        for item in items:
            if not isinstance(item, dict):
                unreadable += 1
                continue
            try:
                jobs.append(to_raw_job(item))
            except Exception as exc:  # noqa: BLE001 -- isolate one malformed record
                unreadable += 1
                logger.warning(
                    "Unreadable job record source=%s board=%s error=%s",
                    self.name,
                    board.key,
                    type(exc).__name__,
                )
        return BoardResult(jobs=jobs, unreadable_items=unreadable)

    @abstractmethod
    async def fetch_board(self, client: httpx.AsyncClient, board: Board) -> BoardResult:
        """Read one board. Raise `SourceError` if the board can't be read."""
        raise NotImplementedError


def as_str(value: object) -> str | None:
    """A non-empty string from a JSON value, or None."""
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None
