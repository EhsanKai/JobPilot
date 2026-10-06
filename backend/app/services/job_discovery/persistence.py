"""Persisting source postings and resolving them to canonical jobs.

    NormalizedJob  (one posting from one source)
          |
          v
    upsert_job()                                   caller holds the identity locks
          +-- known posting (same dedup_key)  -> refresh the posting (UPDATED /
          |                                      UNCHANGED), re-derive its Job
          +-- new posting -> resolve identity against existing jobs
                 EXACT / HIGH_CONFIDENCE      -> attach to that Job (MERGED)
                 POSSIBLE_MATCH / NO_MATCH    -> new canonical Job (CREATED)

Guarantees:

- Same source + same source id (or, without an id, same canonical URL) is
  the same posting: `job_source_postings.dedup_key` is UNIQUE and inserts
  use ON CONFLICT DO NOTHING, so concurrent runs never store a posting twice.
- A new canonical Job is only ever created together with its first posting,
  inside a savepoint: if the posting turns out to exist already (a
  concurrent run won), the Job is rolled back and the existing posting is
  refreshed instead. No posting-less duplicate Jobs.
- Cross-source resolution for a (company, title) block runs under a
  transaction-scoped advisory lock (`lock_postings`), so two runs can't both
  decide "no match" for the same vacancy and create two Jobs.
- A refresh or merge never changes a Job's `id` and never touches user
  state (`user_jobs`, `applications`).
- Nothing is deleted. Postings a complete board listing no longer contains
  are marked inactive; `last_seen_at` records the latest sighting.
"""

import enum
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from app.models.user_job import UserJob
from app.services.job_discovery.deduplication.identity import (
    IDENTITY_VERSION,
    PostingIdentity,
    block_key,
    identity_of,
    lock_blocks,
)
from app.services.job_discovery.deduplication.merger import refresh_canonical_job
from app.services.job_discovery.deduplication.resolver import (
    AUTO_MERGE_DECISIONS,
    MatchDecision,
    PostingFacts,
    find_candidates,
    resolve,
)
from app.services.job_discovery.deduplication.snapshot import (
    CANONICAL_CONTENT_FIELDS,
    encode_content,
)
from app.services.job_discovery.normalizer import NormalizedJob


class UpsertOutcome(str, enum.Enum):
    #: New posting, no existing job matched: a new canonical Job.
    CREATED = "created"
    #: New posting attached to an existing canonical Job (EXACT / HIGH_CONFIDENCE).
    MERGED = "merged"
    #: Known posting whose content changed.
    UPDATED = "updated"
    #: Known posting seen again without changes.
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class UpsertResult:
    job_id: uuid.UUID
    outcome: UpsertOutcome
    #: How a *new* posting was resolved; None for known postings.
    decision: MatchDecision | None = None


def _identity(job: NormalizedJob) -> PostingIdentity:
    return identity_of(
        company_name=job.company_name, title=job.title, location=job.location, url=job.canonical_url
    )


# --- Locks -----------------------------------------------------------------------------


def lock_postings(db: Session, jobs: Iterable[NormalizedJob]) -> None:
    """Take every identity lock `upsert_job` will need for `jobs`: their
    current blocks, plus the blocks their stored postings are in (a title
    change moves a posting between blocks). Locks are held until commit."""
    jobs = list(jobs)
    keys = {_identity(job).block_key for job in jobs}
    if jobs:
        stored = db.execute(
            select(JobSourcePosting.normalized_company_name, JobSourcePosting.normalized_title)
            .where(JobSourcePosting.dedup_key.in_([job.dedup_key for job in jobs]))
        ).all()
        keys |= {block_key(company, title) for company, title in stored}
    lock_blocks(db, keys)


def _lock_job_blocks(db: Session, job_id: uuid.UUID) -> None:
    rows = db.execute(
        select(JobSourcePosting.normalized_company_name, JobSourcePosting.normalized_title).where(
            JobSourcePosting.job_id == job_id
        )
    ).all()
    lock_blocks(db, {block_key(company, title) for company, title in rows})


# --- Upsert ------------------------------------------------------------------------------


def _posting_values(job: NormalizedJob, identity: PostingIdentity) -> dict[str, Any]:
    """Columns derived from the posting's content (everything but identity,
    freshness and timestamps managed elsewhere)."""
    return {
        "source_company_key": job.source_company_key,
        "source_url": job.source_url,
        "canonical_source_url": job.canonical_url,
        "apply_url": job.apply_url,
        "title": job.title,
        "company_name": job.company_name,
        "location": job.location,
        "url_key": identity.url_key,
        "normalized_title": identity.title_key,
        "normalized_company_name": identity.company_key,
        "normalized_location": identity.location_key,
        "identity_version": IDENTITY_VERSION,
        "normalized_data": encode_content(job.content_fields()),
        "raw_data": job.raw_data,
        "content_hash": job.content_hash,
        "normalizer_version": job.normalizer_version,
    }


def _insert_posting(
    db: Session, job: NormalizedJob, identity: PostingIdentity, *, job_id: uuid.UUID, now: datetime
) -> JobSourcePosting | None:
    """Insert the posting; None if it already exists (unique dedup_key)."""
    posting_id = db.execute(
        insert(JobSourcePosting)
        .values(
            id=uuid.uuid4(),
            job_id=job_id,
            source=job.source,
            source_job_id=job.source_job_id,
            dedup_key=job.dedup_key,
            posted_at=job.posted_at,
            source_updated_at=job.source_updated_at,
            first_seen_at=now,
            last_seen_at=now,
            is_active=True,
            **_posting_values(job, identity),
        )
        .on_conflict_do_nothing()
        .returning(JobSourcePosting.id)
    ).scalar_one_or_none()
    return db.get(JobSourcePosting, posting_id) if posting_id is not None else None


def _job_postings(db: Session, job_id: uuid.UUID) -> list[JobSourcePosting]:
    return list(
        db.execute(
            select(JobSourcePosting)
            .where(JobSourcePosting.job_id == job_id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def refresh_job(db: Session, job_id: uuid.UUID) -> None:
    """Re-derive a canonical Job from its postings (row-locked)."""
    job = db.execute(
        select(Job).where(Job.id == job_id).with_for_update().execution_options(
            populate_existing=True
        )
    ).scalar_one()
    postings = _job_postings(db, job_id)
    if postings:
        refresh_canonical_job(job, postings)


def _refresh_posting(
    db: Session,
    posting: JobSourcePosting,
    job: NormalizedJob,
    identity: PostingIdentity,
    now: datetime,
) -> UpsertResult:
    if posting.last_seen_at < now:
        posting.last_seen_at = now
    posting.is_active = True
    # Source timestamps are freshness metadata, not content: kept current
    # without counting as a content change.
    posting.posted_at = job.posted_at
    posting.source_updated_at = job.source_updated_at

    changed = (
        posting.content_hash != job.content_hash
        or posting.normalizer_version != job.normalizer_version
        or posting.identity_version != IDENTITY_VERSION
        or posting.normalized_data is None
    )
    if changed:
        for name, value in _posting_values(job, identity).items():
            setattr(posting, name, value)
    db.flush()
    refresh_job(db, posting.job_id)
    return UpsertResult(
        posting.job_id, UpsertOutcome.UPDATED if changed else UpsertOutcome.UNCHANGED
    )


def _create_job(
    db: Session, job: NormalizedJob, identity: PostingIdentity, now: datetime
) -> uuid.UUID | None:
    """A new canonical Job with this posting as its first posting, or None
    if the posting already exists (nothing is created then)."""
    content = job.content_fields()
    savepoint = db.begin_nested()
    new_job = Job(
        id=uuid.uuid4(),
        canonical_url=job.canonical_url,
        apply_url=job.apply_url,
        discovered_at=now,
        last_seen_at=now,
        **{name: content[name] for name in CANONICAL_CONTENT_FIELDS},
    )
    db.add(new_job)
    db.flush()
    posting = _insert_posting(db, job, identity, job_id=new_job.id, now=now)
    if posting is None:
        savepoint.rollback()
        return None
    savepoint.commit()
    refresh_canonical_job(new_job, [posting])
    return new_job.id


def upsert_job(db: Session, job: NormalizedJob, *, now: datetime) -> UpsertResult:
    """Store one source posting and return its canonical Job (see module
    docs). The caller must hold the identity locks (`lock_postings`)."""
    identity = _identity(job)
    for _ in range(2):
        existing = db.execute(
            select(JobSourcePosting)
            .where(JobSourcePosting.dedup_key == job.dedup_key)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if existing is not None:
            return _refresh_posting(db, existing, job, identity, now)

        facts = PostingFacts.from_content(
            source=job.source, url=job.canonical_url, content=job.content_fields()
        )
        resolution = resolve(facts, find_candidates(db, identity))
        if resolution.decision in AUTO_MERGE_DECISIONS and resolution.job_id is not None:
            db.execute(select(Job.id).where(Job.id == resolution.job_id).with_for_update())
            if _insert_posting(db, job, identity, job_id=resolution.job_id, now=now) is not None:
                refresh_job(db, resolution.job_id)
                return UpsertResult(resolution.job_id, UpsertOutcome.MERGED, resolution.decision)
        else:
            job_id = _create_job(db, job, identity, now)
            if job_id is not None:
                return UpsertResult(job_id, UpsertOutcome.CREATED, resolution.decision)
        # The posting appeared concurrently (it can only have been committed,
        # since ON CONFLICT waits for the other transaction): refresh it.
    raise RuntimeError("Posting could neither be inserted nor found.")


def link_user_job(db: Session, *, user_id: uuid.UUID, job_id: uuid.UUID, now: datetime) -> None:
    """Record that the user's discovery returned this job. Saved state is
    preserved on conflict."""
    statement = insert(UserJob).values(
        id=uuid.uuid4(),
        user_id=user_id,
        job_id=job_id,
        first_discovered_at=now,
        last_discovered_at=now,
    )
    db.execute(
        statement.on_conflict_do_update(
            constraint="uq_user_jobs_user_job",
            set_={
                "last_discovered_at": now,
                "first_discovered_at": func.coalesce(UserJob.first_discovered_at, now),
                "updated_at": func.now(),
            },
        )
    )


def multi_source_job_ids(db: Session, job_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """The jobs among `job_ids` that have postings from at least two sources."""
    ids = list(set(job_ids))
    if not ids:
        return set()
    return set(
        db.execute(
            select(JobSourcePosting.job_id)
            .where(JobSourcePosting.job_id.in_(ids))
            .group_by(JobSourcePosting.job_id)
            .having(func.count(func.distinct(JobSourcePosting.source)) > 1)
        ).scalars()
    )


# --- Freshness ---------------------------------------------------------------------------
# These run in their own short transactions after a source's postings are
# stored. Rows another transaction is busy with are skipped (SKIP LOCKED)
# instead of waited for: that transaction is refreshing them anyway, and
# not waiting means no lock-order deadlocks.


def mark_seen(db: Session, dedup_keys: Iterable[str], *, now: datetime) -> set[uuid.UUID]:
    """Record that the source listed these postings (whether or not they
    matched the run's criteria). Returns the jobs whose postings were
    re-activated (their preferred posting may change)."""
    keys = list(set(dedup_keys))
    if not keys:
        return set()
    rows = db.execute(
        select(JobSourcePosting.id, JobSourcePosting.job_id, JobSourcePosting.is_active)
        .where(
            JobSourcePosting.dedup_key.in_(keys),
            or_(JobSourcePosting.last_seen_at < now, JobSourcePosting.is_active.is_(False)),
        )
        .order_by(JobSourcePosting.id)
        .with_for_update(skip_locked=True)
    ).all()
    if not rows:
        return set()
    db.execute(
        update(JobSourcePosting)
        .where(JobSourcePosting.id.in_([row.id for row in rows]))
        .values(last_seen_at=func.greatest(JobSourcePosting.last_seen_at, now), is_active=True)
    )
    job_ids = sorted({row.job_id for row in rows})
    locked_jobs = db.execute(
        select(Job.id)
        .where(Job.id.in_(job_ids), Job.last_seen_at < now)
        .order_by(Job.id)
        .with_for_update(skip_locked=True)
    ).scalars().all()
    if locked_jobs:
        db.execute(update(Job).where(Job.id.in_(locked_jobs)).values(last_seen_at=now))
    return {row.job_id for row in rows if not row.is_active}


def deactivate_missing(
    db: Session, *, source: str, board: str, listed_keys: Iterable[str]
) -> set[uuid.UUID]:
    """Mark the board's postings that its complete listing no longer
    contains as inactive. Only call this for a board that was read
    completely in this run. Returns the affected jobs."""
    listed = list(set(listed_keys))
    rows = db.execute(
        select(JobSourcePosting.id, JobSourcePosting.job_id)
        .where(
            JobSourcePosting.source == source,
            JobSourcePosting.source_company_key == board,
            JobSourcePosting.is_active.is_(True),
            JobSourcePosting.dedup_key.not_in(listed),
        )
        .order_by(JobSourcePosting.id)
        .with_for_update(skip_locked=True)
    ).all()
    if not rows:
        return set()
    db.execute(
        update(JobSourcePosting)
        .where(JobSourcePosting.id.in_([row.id for row in rows]))
        .values(is_active=False)
    )
    return {row.job_id for row in rows}


def refresh_jobs(db: Session, job_ids: Iterable[uuid.UUID]) -> None:
    """Re-derive several canonical Jobs, each in its own transaction under
    its identity locks (commits)."""
    for job_id in sorted(set(job_ids)):
        _lock_job_blocks(db, job_id)
        refresh_job(db, job_id)
        db.commit()


# --- Identity maintenance -----------------------------------------------------------------


def backfill_identities(db: Session, *, batch_size: int = 500) -> int:
    """Compute comparison keys for postings that don't have current ones:
    postings migrated from V0.3, or all postings after `IDENTITY_VERSION`
    changes. Cheap when there is nothing to do; commits per batch. Returns
    the number of postings updated."""
    updated = 0
    while True:
        postings = list(
            db.execute(
                select(JobSourcePosting)
                .where(
                    or_(
                        JobSourcePosting.identity_version.is_(None),
                        JobSourcePosting.identity_version != IDENTITY_VERSION,
                    )
                )
                .order_by(JobSourcePosting.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            ).scalars()
        )
        if not postings:
            return updated
        for posting in postings:
            identity = identity_of(
                company_name=posting.company_name,
                title=posting.title,
                location=posting.location,
                url=posting.canonical_source_url,
            )
            posting.url_key = identity.url_key
            posting.normalized_title = identity.title_key
            posting.normalized_company_name = identity.company_key
            posting.normalized_location = identity.location_key
            posting.identity_version = IDENTITY_VERSION
        db.commit()
        updated += len(postings)
        job_ids = sorted({posting.job_id for posting in postings})
        jobs = db.execute(
            select(Job).where(Job.id.in_(job_ids)).order_by(Job.id).with_for_update(skip_locked=True)
        ).scalars().all()
        for job in jobs:
            postings_of_job = _job_postings(db, job.id)
            if postings_of_job:
                refresh_canonical_job(job, postings_of_job)
        db.commit()
