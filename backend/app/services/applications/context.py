"""Building the immutable application context (V0.7).

The context is the canonical job as the user saw it when they started the
application, plus the source posting chosen as the application target. It is
built from what already exists: the canonical content fields of V0.4
(`CANONICAL_CONTENT_FIELDS`, encoded the same way a posting's own snapshot
is) and the posting's identity. Nothing is fetched, parsed or inferred here,
and nothing the user wrote is included.

"Where applying happens" reuses V0.4's rule (`application_url`): the
posting's explicit apply URL, or the posting page itself for sources that
host the form. The job's canonical URL is never assumed to be it.
"""

import hashlib
import json
from datetime import datetime
from typing import Any

from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from app.schemas.application_context import (
    ApplicationContextDocument,
    ContextJob,
    ContextOrigin,
    ContextSource,
)
from app.services.job_discovery.deduplication.merger import application_url
from app.services.job_discovery.deduplication.snapshot import content_of_job, encode_content

#: Name used when a job has no source posting at all (only transiently the case).
UNKNOWN_SOURCE = "unknown"


def build_context(
    job: Job,
    posting: JobSourcePosting | None,
    *,
    captured_at: datetime,
    origin: ContextOrigin = "created",
) -> ApplicationContextDocument:
    """The context of an application started from `job` through `posting`
    (the job's preferred posting unless the user chose another)."""
    job_part = ContextJob.model_validate(
        {
            "job_id": job.id,
            "canonical_url": job.canonical_url,
            "apply_url": job.apply_url,
            **encode_content(content_of_job(job)),
        }
    )
    if posting is not None:
        source = ContextSource(
            posting_id=posting.id,
            source=posting.source,
            source_job_id=posting.source_job_id,
            source_company_key=posting.source_company_key,
            title=posting.title,
            company_name=posting.company_name,
            location=posting.location,
            posted_at=posting.posted_at,
            source_updated_at=posting.source_updated_at,
            original_url=posting.source_url or posting.canonical_source_url,
            canonical_url=posting.canonical_source_url,
            apply_url=posting.apply_url,
            application_url=application_url(posting),
            was_preferred=posting.id == job.preferred_source_posting_id,
        )
    else:
        source = ContextSource(
            source=UNKNOWN_SOURCE,
            title=job.title,
            company_name=job.company_name,
            location=job.location,
            original_url=job.canonical_url,
            canonical_url=job.canonical_url,
            apply_url=job.apply_url,
            application_url=job.apply_url,
        )
    return ApplicationContextDocument(
        origin=origin, captured_at=captured_at, job=job_part, source=source
    )


def hash_document(document: dict[str, Any]) -> str:
    """SHA-256 of a context document in canonical JSON form. Applied to the
    *stored* JSON, so a snapshot can always be verified against its
    `content_hash` without depending on how a model re-serializes it."""
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def content_hash(document: ApplicationContextDocument) -> str:
    """The `content_hash` of the snapshot that stores `document`."""
    return hash_document(document.model_dump(mode="json"))
