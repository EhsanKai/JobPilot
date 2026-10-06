"""Job discovery orchestration.

    JobSearchCriteria
          |
          v
    JobDiscoveryService
          +-- select enabled sources (registry)
          +-- JobSource.search()      concurrently, each source isolated
          +-- JobNormalizer           RawJob -> NormalizedJob, invalid ones counted
          +-- in-run dedup            same dedup_key twice -> counted once
          +-- criteria filters        filters the source didn't apply itself
          +-- page / limit            newest first
          +-- persist (V0.4)          upsert source posting, resolve it to a
          |                           canonical Job (cross-source dedup), link UserJob
          +-- freshness               last seen / no longer listed
          v
    DiscoverySummary  (counts per source, errors, who applied each filter)

Sources are persisted one after another, each in its own transaction, so a
later source's postings are matched against jobs an earlier source just
created.

Contains no provider-specific logic: it only talks to `JobSource`.
"""

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg.errors
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.user import User
from app.schemas.jobs import (
    DiscoverySummary,
    FilterAppliedBy,
    FilterReport,
    JobSearchCriteria,
    SourceIssueRead,
    SourceRunStatus,
    SourceRunSummary,
)
from app.services.job_discovery.deduplication.resolver import MatchDecision
from app.services.job_discovery.filters import JOBPILOT_EVALUABLE_FILTERS, matches
from app.services.job_discovery.normalizer import InvalidJobError, JobNormalizer, NormalizedJob
from app.services.job_discovery.persistence import (
    UpsertOutcome,
    UpsertResult,
    backfill_identities,
    deactivate_missing,
    link_user_job,
    lock_postings,
    mark_seen,
    multi_source_job_ids,
    refresh_jobs,
    upsert_job,
)
from app.services.job_discovery.registry import JobSourceRegistry
from app.services.job_discovery.sources.base import (
    JobSource,
    SourceError,
    SourceSearchResult,
    SourceTimeoutError,
)

logger = get_logger(__name__)

# Upper bound for one source's whole search (all boards, all pages). Each
# HTTP request has its own, shorter timeout as well.
SOURCE_SEARCH_TIMEOUT_SECONDS = 180.0
# Summed over sources for the run total. (`discovered` is not: it counts
# distinct jobs, and sources can share jobs.)
_SUMMED_FIELDS = (
    "fetched", "invalid", "filtered_out", "beyond_limit", "duplicates", "created", "merged",
    "updated", "unchanged", "cross_source_matches", "possible_matches", "skipped", "error_count",
    "retried_requests",
)
# Storing a source's postings is retried when Postgres aborts the
# transaction to break a deadlock or a serialization conflict with a
# concurrent run. Every attempt starts from scratch.
_STORE_ATTEMPTS = 3
_RETRYABLE_ERRORS = (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure)
# Database errors caused by one posting's data. That posting is skipped (its
# savepoint rolled back, so no partial job is left) and reported; the rest of
# the source is stored. Anything else is a bug and fails the run.
_POSTING_DATA_ERRORS = (DataError, IntegrityError)
POSTING_REJECTED = SourceIssueRead(
    code="POSTING_REJECTED", message="A job posting could not be stored and was skipped."
)


@dataclass
class SourceOutcome:
    source: JobSource
    result: SourceSearchResult | None = None
    error: SourceError | None = None


def filter_reports(source: JobSource, criteria: JobSearchCriteria) -> list[FilterReport]:
    reports = []
    for search_filter in sorted(criteria.requested_filters(), key=lambda f: f.value):
        if search_filter in source.native_filters:
            applied_by = FilterAppliedBy.SOURCE
        elif search_filter in JOBPILOT_EVALUABLE_FILTERS:
            applied_by = FilterAppliedBy.JOBPILOT
        else:
            applied_by = FilterAppliedBy.NOT_APPLIED
        reports.append(FilterReport(filter=search_filter, applied_by=applied_by))
    return reports


def _sort_key(job: NormalizedJob) -> tuple[bool, float, str]:
    # Newest first; undated postings last; stable by key.
    timestamp = job.posted_at.timestamp() if job.posted_at else 0.0
    return (job.posted_at is None, -timestamp, job.dedup_key)


class JobDiscoveryService:
    def __init__(self, registry: JobSourceRegistry, normalizer: JobNormalizer | None = None):
        self.registry = registry
        self.normalizer = normalizer or JobNormalizer()

    # --- Fetching -------------------------------------------------------------

    async def collect(
        self, sources: list[JobSource], criteria: JobSearchCriteria
    ) -> list[SourceOutcome]:
        async def run(source: JobSource) -> SourceOutcome:
            try:
                result = await asyncio.wait_for(
                    source.search(criteria), timeout=SOURCE_SEARCH_TIMEOUT_SECONDS
                )
                return SourceOutcome(source=source, result=result)
            except TimeoutError:
                return SourceOutcome(source=source, error=SourceTimeoutError())
            except SourceError as exc:
                return SourceOutcome(source=source, error=exc)
            except Exception as exc:  # noqa: BLE001 -- one source must never sink the run
                logger.error(
                    "Job source crashed source=%s error=%s", source.name, type(exc).__name__
                )
                return SourceOutcome(source=source, error=SourceError())

        return list(await asyncio.gather(*(run(source) for source in sources)))

    # --- Orchestration ----------------------------------------------------------

    def discover(self, db: Session, user: User, criteria: JobSearchCriteria) -> DiscoverySummary:
        sources = self.registry.select(criteria.sources)
        outcomes = asyncio.run(self.collect(sources, criteria))
        return self.persist(db, user, criteria, outcomes)

    def persist(
        self,
        db: Session,
        user: User,
        criteria: JobSearchCriteria,
        outcomes: list[SourceOutcome],
        *,
        now: datetime | None = None,
    ) -> DiscoverySummary:
        now = now or datetime.now(UTC)
        # Postings migrated from V0.3 (or keyed by older rules) get current
        # comparison keys before anything is matched against them.
        backfill_identities(db)
        summary = DiscoverySummary()
        seen_keys: set[str] = set()
        discovered_jobs: set[uuid.UUID] = set()
        for outcome in outcomes:
            source_summary, job_ids = self._persist_source(
                db, user, criteria, outcome, seen_keys, now
            )
            summary.sources.append(source_summary)
            discovered_jobs |= job_ids
            for name in _SUMMED_FIELDS:
                setattr(summary, name, getattr(summary, name) + getattr(source_summary, name))
        summary.discovered = len(discovered_jobs)
        return summary

    def _persist_source(
        self,
        db: Session,
        user: User,
        criteria: JobSearchCriteria,
        outcome: SourceOutcome,
        seen_keys: set[str],
        now: datetime,
    ) -> tuple[SourceRunSummary, set[uuid.UUID]]:
        source = outcome.source
        summary = SourceRunSummary(
            source=source.name,
            display_name=source.display_name,
            status=SourceRunStatus.SUCCEEDED,
            filters=filter_reports(source, criteria),
        )
        if outcome.error is not None or outcome.result is None:
            error = outcome.error or SourceError()
            summary.status = SourceRunStatus.FAILED
            summary.errors.append(
                SourceIssueRead(code=error.code, message=error.message, target=error.target)
            )
            summary.error_count = len(summary.errors)
            return summary, set()

        result = outcome.result
        summary.errors = [
            SourceIssueRead(code=i.code, message=i.message, target=i.target) for i in result.issues
        ]
        summary.error_count = len(summary.errors)
        summary.retried_requests = result.retried_requests
        if result.targets_attempted and result.targets_failed >= result.targets_attempted:
            summary.status = SourceRunStatus.FAILED
        elif result.targets_failed or result.issues:
            summary.status = SourceRunStatus.PARTIAL

        summary.fetched = len(result.jobs) + result.unreadable_items
        summary.invalid = result.unreadable_items

        local_filters = criteria.requested_filters() - set(source.native_filters)
        listed: list[NormalizedJob] = []  # every valid posting the source listed
        boards_with_invalid: set[str | None] = set()
        matching: list[NormalizedJob] = []
        for raw in result.jobs:
            try:
                job = self.normalizer.normalize(raw, now=now)
            except InvalidJobError as exc:
                summary.invalid += 1
                boards_with_invalid.add(raw.source_company_key)
                logger.info(
                    "Skipping invalid job source=%s field=%s", source.name, exc.field_name
                )
                continue
            except Exception as exc:  # noqa: BLE001 -- one odd record must not sink the run
                summary.invalid += 1
                boards_with_invalid.add(raw.source_company_key)
                logger.warning(
                    "Could not normalize job source=%s board=%s error=%s",
                    source.name,
                    raw.source_company_key,
                    type(exc).__name__,
                )
                continue
            if job.dedup_key in seen_keys:
                summary.duplicates += 1
                continue
            seen_keys.add(job.dedup_key)
            listed.append(job)
            if not matches(job, criteria, local_filters):
                summary.filtered_out += 1
                continue
            matching.append(job)

        matching.sort(key=_sort_key)
        start = (criteria.page - 1) * criteria.limit
        selected = matching[start : start + criteria.limit]
        summary.beyond_limit = len(matching) - len(selected)

        stored, rejected = self._store(db, user, selected, now)
        for job in rejected:
            summary.invalid += 1
            summary.errors.append(
                POSTING_REJECTED.model_copy(update={"target": job.source_company_key})
            )
            boards_with_invalid.add(job.source_company_key)
        summary.error_count = len(summary.errors)
        summary.skipped = (
            summary.invalid + summary.duplicates + summary.filtered_out + summary.beyond_limit
        )
        for item in stored:
            if item.outcome is UpsertOutcome.CREATED:
                summary.created += 1
                if item.decision is MatchDecision.POSSIBLE_MATCH:
                    summary.possible_matches += 1
            elif item.outcome is UpsertOutcome.MERGED:
                summary.merged += 1
            elif item.outcome is UpsertOutcome.UPDATED:
                summary.updated += 1
            else:
                summary.unchanged += 1
        job_ids = {item.job_id for item in stored}
        multi_source = multi_source_job_ids(db, job_ids)
        summary.cross_source_matches = sum(1 for item in stored if item.job_id in multi_source)
        summary.discovered = len(job_ids)

        self._update_freshness(db, source, result, listed, boards_with_invalid, now)
        logger.info(
            "Discovery source done source=%s status=%s fetched=%d discovered=%d created=%d "
            "merged=%d possible=%d",
            source.name,
            summary.status.value,
            summary.fetched,
            summary.discovered,
            summary.created,
            summary.merged,
            summary.possible_matches,
        )
        return summary, job_ids

    def _store(
        self, db: Session, user: User, jobs: list[NormalizedJob], now: datetime
    ) -> tuple[list[UpsertResult], list[NormalizedJob]]:
        """Store one source's selected postings in one transaction, holding
        the identity locks they need (see persistence.lock_postings). Each
        posting gets its own savepoint: one whose data the database rejects
        is rolled back completely (no partial job) and returned as rejected."""
        ordered = sorted(jobs, key=lambda job: job.dedup_key)
        for attempt in range(1, _STORE_ATTEMPTS + 1):
            try:
                lock_postings(db, ordered)
                stored: list[UpsertResult] = []
                rejected: list[NormalizedJob] = []
                for job in ordered:
                    savepoint = db.begin_nested()
                    try:
                        item = upsert_job(db, job, now=now)
                        link_user_job(db, user_id=user.id, job_id=item.job_id, now=now)
                    except _POSTING_DATA_ERRORS as exc:
                        savepoint.rollback()
                        rejected.append(job)
                        logger.warning(
                            "Rejected job posting source=%s board=%s error=%s",
                            job.source,
                            job.source_company_key,
                            type(exc.orig).__name__,
                        )
                        continue
                    savepoint.commit()
                    stored.append(item)
                db.commit()  # keep each source's results even if a later one fails
                return stored, rejected
            except OperationalError as exc:
                db.rollback()
                if not isinstance(exc.orig, _RETRYABLE_ERRORS) or attempt == _STORE_ATTEMPTS:
                    raise
                logger.warning(
                    "Retrying job storage after a concurrency conflict attempt=%d error=%s",
                    attempt,
                    type(exc.orig).__name__,
                )
        raise AssertionError("unreachable")

    def _update_freshness(
        self,
        db: Session,
        source: JobSource,
        result: SourceSearchResult,
        listed: list[NormalizedJob],
        boards_with_invalid: set[str | None],
        now: datetime,
    ) -> None:
        """Everything the source listed was seen now. A posting missing from
        a board's *complete* listing is no longer listed there (inactive);
        boards that failed, were capped or had unreadable/invalid entries
        are never used for that conclusion."""
        affected = mark_seen(db, [job.dedup_key for job in listed], now=now)
        listed_by_board: dict[str | None, set[str]] = defaultdict(set)
        for job in listed:
            listed_by_board[job.source_company_key].add(job.dedup_key)
        for board in result.complete_targets:
            if board in boards_with_invalid:
                continue
            affected |= deactivate_missing(
                db, source=source.name, board=board, listed_keys=listed_by_board[board]
            )
        db.commit()
        refresh_jobs(db, affected)


def overall_succeeded(summary: DiscoverySummary) -> tuple[bool, bool]:
    """(any source succeeded, any source failed)."""
    statuses = [s.status for s in summary.sources]
    any_ok = any(s != SourceRunStatus.FAILED for s in statuses)
    any_failed = any(s != SourceRunStatus.SUCCEEDED for s in statuses)
    return any_ok, any_failed

