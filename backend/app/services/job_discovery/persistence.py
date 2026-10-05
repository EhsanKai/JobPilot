"""Persisting normalized jobs: basic deduplication and refresh.

    dedup_key unique  ->  INSERT ... ON CONFLICT (dedup_key) DO NOTHING
                          then update the existing row if its content changed

- Same source + same source_job_id (or, without an id, same canonical URL)
  is the same job: never a second row, even under concurrent runs.
- A refresh updates the posting's content but never `id`, `discovered_at`,
  or anything user-specific (saved state and, later, applications live in
  `user_jobs` / `applications` and are never touched here).
- Jobs are never deleted because a run didn't return them; `last_seen_at`
  records when a source last listed them.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.user_job import UserJob
from app.services.job_discovery.normalizer import NormalizedJob


class UpsertOutcome(str, enum.Enum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


def upsert_job(
    db: Session, job: NormalizedJob, *, now: datetime
) -> tuple[uuid.UUID, UpsertOutcome]:
    content = job.content_fields()
    inserted_id = db.execute(
        insert(Job)
        .values(
            id=uuid.uuid4(),
            dedup_key=job.dedup_key,
            raw_data=job.raw_data,
            content_hash=job.content_hash,
            normalizer_version=job.normalizer_version,
            discovered_at=now,
            last_seen_at=now,
            **content,
        )
        .on_conflict_do_nothing(index_elements=[Job.dedup_key])
        .returning(Job.id)
    ).scalar_one_or_none()
    if inserted_id is not None:
        return inserted_id, UpsertOutcome.CREATED

    existing = db.execute(
        select(Job).where(Job.dedup_key == job.dedup_key).with_for_update()
    ).scalar_one()
    existing.last_seen_at = now
    if (
        existing.content_hash == job.content_hash
        and existing.normalizer_version == job.normalizer_version
    ):
        return existing.id, UpsertOutcome.UNCHANGED

    for key, value in content.items():
        setattr(existing, key, value)
    existing.raw_data = job.raw_data
    existing.content_hash = job.content_hash
    existing.normalizer_version = job.normalizer_version
    return existing.id, UpsertOutcome.UPDATED


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
