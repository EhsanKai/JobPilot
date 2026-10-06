"""Building the canonical Job from its source postings.

Preferred posting policy -- deterministic, first difference wins:

    1. active before inactive (a posting its source no longer lists can't
       be applied to)
    2. source reliability: the company's own ATS board (Greenhouse, Lever)
       before job-search APIs before web pages; unknown sources last
    3. an application URL is available (an explicit apply URL, or a source
       whose posting page hosts the application form)
    4. source priority (`SOURCE_PRIORITY`)
    5. stable fallback: first seen earliest, then posting id

The preferred posting is the one future application automation (V0.10+)
should use, and the canonical content comes from it:

- every content field is taken from the preferred posting;
- fields the preferred posting leaves empty are filled from the next posting
  in preference order that states them (salary is taken as one group --
  min/max/currency/period/text never mix across postings);
- `canonical_url` / `apply_url` are the preferred posting's;
- `discovered_at` only ever moves earlier, `last_seen_at` is the latest
  sighting of any posting.

The Job's `id` is never changed here; user jobs and applications keep
pointing at it.
"""

from datetime import datetime
from typing import Any

from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from app.services.job_discovery.deduplication.snapshot import (
    CANONICAL_CONTENT_FIELDS,
    content_of_job,
    decode_content,
)
from app.services.job_discovery.normalization.location import normalize_location

#: Lower is more reliable. 0 = the employer's own ATS job board.
SOURCE_RELIABILITY: dict[str, int] = {"greenhouse": 0, "lever": 0}
UNKNOWN_SOURCE_RELIABILITY = 9
#: Tie-breaker between equally reliable sources.
SOURCE_PRIORITY: tuple[str, ...] = ("greenhouse", "lever")
#: Sources whose posting page itself hosts the application form.
SOURCES_WITH_HOSTED_APPLICATION = frozenset({"greenhouse", "lever"})

# Filled from another posting when the preferred one leaves them empty.
# Each tuple is taken as a unit.
GAP_FILL_GROUPS: tuple[tuple[str, ...], ...] = (
    ("salary_min", "salary_max", "salary_currency", "salary_period", "salary_text"),
    ("employment_type",),
    ("work_model",),
    ("seniority",),
    ("department",),
    ("industry",),
    ("country",),
    ("posted_at",),
)


def application_url(posting: JobSourcePosting) -> str | None:
    if posting.apply_url:
        return posting.apply_url
    if posting.source in SOURCES_WITH_HOSTED_APPLICATION:
        return posting.canonical_source_url
    return None


def preference_key(posting: JobSourcePosting) -> tuple[Any, ...]:
    priority = (
        SOURCE_PRIORITY.index(posting.source)
        if posting.source in SOURCE_PRIORITY
        else len(SOURCE_PRIORITY)
    )
    return (
        0 if posting.is_active else 1,
        SOURCE_RELIABILITY.get(posting.source, UNKNOWN_SOURCE_RELIABILITY),
        0 if application_url(posting) else 1,
        priority,
        posting.first_seen_at,
        str(posting.id),
    )


def order_postings(postings: list[JobSourcePosting]) -> list[JobSourcePosting]:
    """Postings in preference order; the first one is preferred."""
    return sorted(postings, key=preference_key)


def _is_empty(value: object) -> bool:
    return value is None or value == [] or value == ""


def canonical_content(job: Job, ordered: list[JobSourcePosting]) -> dict[str, Any]:
    """Canonical content from postings in preference order (see module docs)."""
    preferred = ordered[0]
    # A posting migrated from V0.3 has no snapshot; the job's current content
    # is exactly what that posting produced.
    values = (
        decode_content(preferred.normalized_data)
        if preferred.normalized_data is not None
        else content_of_job(job)
    )
    others = [decode_content(p.normalized_data) for p in ordered[1:] if p.normalized_data]
    for group in GAP_FILL_GROUPS:
        if all(_is_empty(values.get(name)) for name in group):
            for other in others:
                if not all(_is_empty(other.get(name)) for name in group):
                    values.update({name: other.get(name) for name in group})
                    break
    return values


def _set(job: Job, name: str, value: object) -> None:
    if getattr(job, name) != value:
        setattr(job, name, value)


def refresh_canonical_job(job: Job, postings: list[JobSourcePosting]) -> JobSourcePosting:
    """Re-derive the canonical Job from `postings` (all of the job's
    postings, loaded under the job's identity lock). Only attributes whose
    value actually changes are written. Returns the preferred posting."""
    ordered = order_postings(postings)
    preferred = ordered[0]
    content = canonical_content(job, ordered)
    for name in CANONICAL_CONTENT_FIELDS:
        _set(job, name, content[name])
    _set(job, "canonical_url", preferred.canonical_source_url)
    _set(job, "apply_url", preferred.apply_url)
    _set(job, "preferred_source_posting_id", preferred.id)

    if preferred.identity_version is not None:
        _set(job, "normalized_title", preferred.normalized_title)
        _set(job, "normalized_company_name", preferred.normalized_company_name)
        _set(job, "normalized_location", preferred.normalized_location)
    location = normalize_location(job.location)
    _set(job, "city", location.city if location else None)

    first_seen: datetime = min(p.first_seen_at for p in postings)
    if job.discovered_at is None or first_seen < job.discovered_at:
        job.discovered_at = first_seen
    last_seen: datetime = max(p.last_seen_at for p in postings)
    if job.last_seen_at is None or last_seen > job.last_seen_at:
        job.last_seen_at = last_seen
    return preferred
