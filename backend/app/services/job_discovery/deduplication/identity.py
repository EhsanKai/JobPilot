"""Comparison keys of a posting, and the locks that serialize resolution.

A posting's identity for cross-source matching is

    company_key   normalized company, legal suffix removed   "example"
    title_key     normalized title                           "senior sap abap developer"
    location      normalized primary location                "de:cologne"
    url_key       URL identity key                           "https://jobs.example.com/123"

Every automatic merge requires equal `company_key` and `title_key` (see
resolver.py), so (company_key, title_key) -- the *block key* -- partitions
postings into groups that can never merge with each other. Resolution takes
a transaction-scoped Postgres advisory lock per block key: two concurrent
discovery runs that could merge (or duplicate) the same job are serialized
by the database, while unrelated jobs proceed in parallel.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.job_discovery.normalization.company import company_key
from app.services.job_discovery.normalization.location import (
    NormalizedLocation,
    normalize_location,
)
from app.services.job_discovery.normalization.text import title_key
from app.services.job_discovery.normalization.url import url_identity_key

# Stored on each posting. Changing the key rules means bumping this; postings
# with another version are re-keyed before the next resolution
# (`persistence.backfill_identities`).
IDENTITY_VERSION = "identity_v1"

# Column sizes of the stored keys. Folding can make a key longer than its
# source text ("&" -> "and", http -> https), so keys are bounded here.
MAX_TITLE_KEY = 500
MAX_COMPANY_KEY = 255
MAX_LOCATION_KEY = 600
MAX_URL_KEY = 2000

_BLOCK_SEPARATOR = "\x1f"
_LOCK_NAMESPACE = b"jobpilot:job-identity:"


@dataclass(frozen=True)
class PostingIdentity:
    company_key: str
    title_key: str
    location: NormalizedLocation | None
    url_key: str | None

    @property
    def location_key(self) -> str | None:
        return self.location.key if self.location else None

    @property
    def block_key(self) -> str:
        return block_key(self.company_key, self.title_key)

    @property
    def is_matchable(self) -> bool:
        """Without a company and a title there is nothing to match on."""
        return bool(self.company_key and self.title_key)


def identity_of(
    *, company_name: str | None, title: str | None, location: str | None, url: str | None
) -> PostingIdentity:
    location_ = normalize_location(location)
    if location_ is not None and len(location_.key) > MAX_LOCATION_KEY:
        location_ = replace(location_, key=location_.key[:MAX_LOCATION_KEY])
    url_key = url_identity_key(url)
    return PostingIdentity(
        company_key=company_key(company_name)[:MAX_COMPANY_KEY],
        title_key=title_key(title)[:MAX_TITLE_KEY],
        location=location_,
        # A truncated URL could point anywhere: no key rather than a wrong one.
        url_key=url_key if url_key and len(url_key) <= MAX_URL_KEY else None,
    )


def block_key(company: str | None, title: str | None) -> str:
    return f"{company or ''}{_BLOCK_SEPARATOR}{title or ''}"


def lock_id(key: str) -> int:
    """A stable signed 64-bit advisory-lock id for a block key. Collisions
    only cause extra serialization, never incorrect results."""
    digest = hashlib.sha256(_LOCK_NAMESPACE + key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def lock_blocks(db: Session, keys: Iterable[str]) -> None:
    """Take the identity locks for `keys`, held until the transaction ends.

    Always in ascending lock-id order, so transactions that need overlapping
    sets of locks can't deadlock on each other.
    """
    for lock in sorted({lock_id(key) for key in keys}):
        db.execute(select(func.pg_advisory_xact_lock(lock)))
