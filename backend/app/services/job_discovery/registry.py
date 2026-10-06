"""Registry of job sources.

The discovery service asks the registry for sources by name and never
imports a concrete adapter. Adding a source means:

    1. implement `JobSource` (app/services/job_discovery/sources/<name>.py)
    2. register it in `build_default_registry` (optionally behind settings)

No change to the discovery service, persistence, or API is needed.
"""

from collections.abc import Iterable

from app.core.config import Settings, settings
from app.core.errors import ValidationAppError
from app.services.job_discovery.sources.ats import parse_board_config
from app.services.job_discovery.sources.base import JobSource
from app.services.job_discovery.sources.greenhouse import API_HOST as GREENHOUSE_HOST
from app.services.job_discovery.sources.greenhouse import GreenhouseJobSource
from app.services.job_discovery.sources.http import JsonFetcher
from app.services.job_discovery.sources.lever import API_HOSTS as LEVER_HOSTS
from app.services.job_discovery.sources.lever import LeverJobSource


class JobSourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, JobSource] = {}
        self._enabled: set[str] = set()

    def register(self, source: JobSource, *, enabled: bool = True) -> None:
        if source.name in self._sources:
            raise ValueError(f"A job source named {source.name!r} is already registered.")
        self._sources[source.name] = source
        if enabled:
            self._enabled.add(source.name)

    def all(self) -> list[JobSource]:
        return list(self._sources.values())

    def is_enabled(self, name: str) -> bool:
        return name in self._enabled

    def enabled(self) -> list[JobSource]:
        return [s for s in self._sources.values() if s.name in self._enabled]

    def select(self, names: Iterable[str] | None) -> list[JobSource]:
        """The enabled sources to query. `None` selects every enabled source;
        naming an unknown or disabled source is a validation error rather
        than being silently ignored."""
        if names is None:
            return self.enabled()
        selected: list[JobSource] = []
        for name in names:
            if name not in self._enabled:
                raise ValidationAppError(
                    f"Unknown or disabled job source: {name!r}.",
                    details={"available_sources": sorted(self._enabled)},
                )
            selected.append(self._sources[name])
        return selected


def build_default_registry(config: Settings = settings) -> JobSourceRegistry:
    enabled = {n.strip().lower() for n in config.JOB_SOURCES_ENABLED.split(",") if n.strip()}
    registry = JobSourceRegistry()

    def fetcher(hosts: Iterable[str]) -> JsonFetcher:
        return JsonFetcher(
            allowed_hosts=frozenset(hosts),
            timeout_seconds=config.JOB_SOURCE_TIMEOUT_SECONDS,
            max_response_bytes=config.JOB_SOURCE_MAX_RESPONSE_BYTES,
            max_retries=config.JOB_SOURCE_MAX_RETRIES,
            retry_backoff_seconds=config.JOB_SOURCE_RETRY_BACKOFF_SECONDS,
        )

    registry.register(
        GreenhouseJobSource(
            parse_board_config(config.GREENHOUSE_BOARD_TOKENS),
            fetcher([GREENHOUSE_HOST]),
            max_jobs_per_board=config.JOB_SOURCE_MAX_JOBS_PER_BOARD,
        ),
        enabled="greenhouse" in enabled,
    )
    instance = config.LEVER_INSTANCE.strip().lower()
    instance = instance if instance in LEVER_HOSTS else "global"
    registry.register(
        LeverJobSource(
            parse_board_config(config.LEVER_SITES),
            fetcher([LEVER_HOSTS[instance]]),
            max_jobs_per_board=config.JOB_SOURCE_MAX_JOBS_PER_BOARD,
            instance=instance,
        ),
        enabled="lever" in enabled,
    )
    return registry


def get_job_source_registry() -> JobSourceRegistry:
    """FastAPI dependency; tests override it with mocked sources."""
    return build_default_registry()
