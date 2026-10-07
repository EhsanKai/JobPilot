"""Normalized job content and the content hash.

`normalized_body` is the comparable text of a posting: description,
responsibilities and requirements, folded (case, Unicode variants, HTML
artifacts, bullets, punctuation and line breaks removed). Description
similarity (deduplication) works on it.

`content_hash` fingerprints the *meaningful* content of a posting so a
refresh can tell "materially changed" from "seen again":

    included   title, company, locations, description, requirements,
               responsibilities, salary (incl. its original text), employment
               type, work model, department, the posting URL exactly as the
               source returned it (it is shown as the original link), apply URL
    excluded   discovered_at, last_seen_at, posted/updated timestamps from the
               source, raw payloads -- anything that changes without the job
               changing

Text fields are hashed in folded form, so pure formatting changes (spacing,
case, "-" vs "–") don't count as a change.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from decimal import Decimal
from enum import Enum

from app.services.job_discovery.normalization.company import company_key
from app.services.job_discovery.normalization.location import location_key
from app.services.job_discovery.normalization.text import fold, title_key
from app.services.job_discovery.normalization.url import url_identity_key

# Part of the hashed payload: changing what is hashed changes every hash.
CONTENT_HASH_VERSION = "content_v2"


def normalized_body(
    description: str | None, requirements: Iterable[str], responsibilities: Iterable[str]
) -> str:
    parts = [description or "", *responsibilities, *requirements]
    return fold("\n".join(part for part in parts if part))


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list | tuple) else []


def _scalar(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value.normalize())
    return value


def content_hash(content: Mapping[str, object]) -> str:
    """Hash of a posting's meaningful content. `content` holds the
    normalized posting fields (`NormalizedJob.content_fields()`)."""

    def text(name: str) -> str:
        value = content.get(name)
        return fold(value) if isinstance(value, str) else ""

    def text_list(name: str) -> list[str]:
        return [fold(v) for v in _strings(content.get(name))]

    title = content.get("title")
    company = content.get("company_name")
    payload = {
        "v": CONTENT_HASH_VERSION,
        "title": title_key(title if isinstance(title, str) else None),
        "company": company_key(company if isinstance(company, str) else None),
        "location": text("location"),
        "locations": sorted({location_key(v) or "" for v in _strings(content.get("locations"))}),
        "description": text("description"),
        "requirements": text_list("requirements"),
        "responsibilities": text_list("responsibilities"),
        "department": text("department"),
        "salary": [
            _scalar(content.get(name))
            for name in ("salary_min", "salary_max", "salary_currency", "salary_period")
        ],
        "salary_text": text("salary_text"),
        "employment_type": _scalar(content.get("employment_type")),
        "work_model": _scalar(content.get("work_model")),
        "url": url_identity_key(content.get("canonical_url")),
        "source_url": content.get("source_url"),
        "apply_url": url_identity_key(content.get("apply_url")),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
