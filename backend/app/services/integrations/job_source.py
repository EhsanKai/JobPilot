"""Architectural placeholder for future job-discovery connectors (V0.3+).

Not implemented in V0. No network calls, scraping, or browser automation
happen here. This interface only defines the contract future connectors
(LinkedInConnector, IndeedConnector, StepStoneConnector, GreenhouseConnector,
WorkdayConnector, ...) will implement, so the core application never depends
on a specific job source.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class JobSearchQuery:
    keywords: str
    location: str | None = None


@dataclass(frozen=True)
class RawJobResult:
    """Unnormalized result as returned by a specific job source."""

    source: str
    external_id: str
    title: str
    company: str
    location: str | None
    url: str | None
    raw_data: dict


class JobSource(ABC):
    """Contract every future job-source connector must implement."""

    @abstractmethod
    async def search(self, query: JobSearchQuery) -> list[RawJobResult]:
        raise NotImplementedError
