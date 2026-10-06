"""Deterministic normalization: `RawJob` -> `NormalizedJob`.

    RawJob (source-shaped, loose) --> JobNormalizer --> NormalizedJob (canonical, validated)

All cleaning rules are applied here, once, instead of in every adapter:
whitespace, empty values, URLs, dates, salary, employment type, work model,
seniority, technologies, identifiers -- and required-field validation. No AI.
The individual rules live in `app.services.job_discovery.normalization`
(re-exported here for convenience); this module composes them.

A posting that fails validation raises `InvalidJobError` with a user-safe
reason. The discovery service counts it as invalid and carries on with the
rest of the batch.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel
from app.services.job_discovery.normalization.content import content_hash
from app.services.job_discovery.normalization.employment import normalize_employment_type
from app.services.job_discovery.normalization.salary import (
    NormalizedSalary,
    normalize_currency,
    normalize_salary,
    normalize_salary_period,
    parse_salary_text,
)
from app.services.job_discovery.normalization.text import (
    MAX_LIST_ITEM_CHARS,
    MAX_LIST_ITEMS,
    clean_list,
    clean_multiline,
    clean_text,
)
from app.services.job_discovery.normalization.url import (
    MAX_URL_CHARS,
    canonicalize_url,
    normalize_source_url,
)
from app.services.job_discovery.normalization.work_model import normalize_work_model
from app.services.job_discovery.raw_job import RawJob
from app.services.job_discovery.vocabulary import SENIORITY_TITLE_PATTERNS, TECHNOLOGY_PATTERNS

__all__ = [
    "MAX_LIST_ITEMS",
    "MAX_LIST_ITEM_CHARS",
    "MAX_URL_CHARS",
    "NORMALIZER_VERSION",
    "InvalidJobError",
    "JobNormalizer",
    "NormalizedJob",
    "NormalizedSalary",
    "build_dedup_key",
    "canonicalize_url",
    "clean_list",
    "clean_multiline",
    "clean_text",
    "extract_technologies",
    "infer_seniority",
    "normalize_currency",
    "normalize_datetime",
    "normalize_employment_type",
    "normalize_salary",
    "normalize_salary_period",
    "normalize_source_url",
    "normalize_work_model",
    "parse_salary_text",
]

# v2 (V0.4): content hash covers meaningful content only (see
# normalization/content.py), salary text is kept. Bumping the version makes
# the next refresh re-store every posting once.
NORMALIZER_VERSION = "job_normalizer_v2"

MAX_TITLE_CHARS = 500
MAX_COMPANY_CHARS = 255
MAX_LOCATION_CHARS = 500
MAX_SHORT_TEXT_CHARS = 255
MAX_SALARY_TEXT_CHARS = 500
MAX_SOURCE_ID_CHARS = 255
MAX_DESCRIPTION_CHARS = 100_000
MAX_LOCATIONS = 30

_SOURCE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class InvalidJobError(Exception):
    """A posting that can't become a canonical Job. `reason` is user-safe."""

    def __init__(self, reason: str, *, field_name: str | None = None) -> None:
        self.reason = reason
        self.field_name = field_name
        super().__init__(reason)


@dataclass(frozen=True)
class NormalizedJob:
    source: str
    source_job_id: str | None
    dedup_key: str
    source_company_key: str | None
    canonical_url: str
    source_url: str | None
    apply_url: str | None
    title: str
    company_name: str
    location: str | None
    locations: list[str]
    country: str | None
    department: str | None
    description: str | None
    requirements: list[str]
    responsibilities: list[str]
    technologies: list[str]
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_period: SalaryPeriod | None
    #: The source's own salary wording, kept as written (even when it was
    #: too ambiguous to structure).
    salary_text: str | None
    employment_type: EmploymentTypeValue | None
    work_model: RemotePreference | None
    seniority: SeniorityLevel | None
    industry: str | None
    posted_at: datetime | None
    source_updated_at: datetime | None
    raw_data: dict[str, Any] = field(repr=False)
    content_hash: str = ""
    normalizer_version: str = NORMALIZER_VERSION

    def content_fields(self) -> dict[str, Any]:
        """The posting's normalized content: everything except the identity
        and provenance fields the persistence layer manages itself."""
        data = asdict(self)
        for key in ("raw_data", "content_hash", "normalizer_version", "dedup_key"):
            data.pop(key)
        return data


def build_dedup_key(source: str, source_job_id: str | None, canonical_url: str) -> str:
    """The unique identity of a posting within a source.

    Prefers the source's own id. Without one, falls back to the canonical URL
    -- hashed, so the key has a fixed length -- because two different
    postings never share a URL, while titles are routinely shared.
    """
    if source_job_id:
        return f"{source}:id:{source_job_id}"
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    return f"{source}:url:{digest}"


# --- Dates ---------------------------------------------------------------------------------


def normalize_datetime(value: object, *, now: datetime | None = None) -> datetime | None:
    """Parse ISO 8601 / RFC 2822 strings, epoch seconds or milliseconds and
    datetimes into an aware UTC datetime. Naive values are taken as UTC.
    Implausible values (before 1990, or more than two days in the future)
    become None rather than polluting "posted date" sorting."""
    now = now or datetime.now(UTC)
    parsed: datetime | None = None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, int | float):
        parsed = _from_epoch(float(value))
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"\d{9,14}(\.\d+)?", text):
            parsed = _from_epoch(float(text))
        else:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(text)
                except (TypeError, ValueError, IndexError):
                    return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed.year < 1990 or parsed > now + timedelta(days=2):
        return None
    return parsed


def _from_epoch(value: float) -> datetime | None:
    # Values this large are milliseconds (seconds would be ~year 5000+).
    if value > 100_000_000_000:
        value /= 1000
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


# --- Classification -------------------------------------------------------------------------


def infer_seniority(title: str | None) -> SeniorityLevel | None:
    if not title:
        return None
    for level, pattern in SENIORITY_TITLE_PATTERNS:
        if pattern.search(title):
            return level
    return None


def extract_technologies(texts: list[str | None]) -> list[str]:
    """Technologies named in the posting, in vocabulary order."""
    corpus = "\n".join(t for t in texts if t)
    if not corpus:
        return []
    return [name for name, pattern in TECHNOLOGY_PATTERNS if pattern.search(corpus)]


# --- Normalizer -------------------------------------------------------------------------------


def _json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(payload, default=str))
    return result


class JobNormalizer:
    version = NORMALIZER_VERSION

    def normalize(self, raw: RawJob, *, now: datetime | None = None) -> NormalizedJob:
        source = (raw.source or "").strip().lower()
        if not _SOURCE_NAME_RE.fullmatch(source):
            raise InvalidJobError("The job has no valid source.", field_name="source")

        source_job_id = clean_text(raw.source_job_id)
        if source_job_id is not None and len(source_job_id) > MAX_SOURCE_ID_CHARS:
            # Truncating would change the posting's identity.
            raise InvalidJobError("The job's source id is too long.", field_name="source_job_id")

        title = clean_text(raw.title, max_chars=MAX_TITLE_CHARS)
        if title is None:
            raise InvalidJobError("The job has no title.", field_name="title")
        company_name = clean_text(raw.company_name, max_chars=MAX_COMPANY_CHARS)
        if company_name is None:
            raise InvalidJobError("The job has no company name.", field_name="company_name")
        canonical_url = canonicalize_url(raw.url)
        if canonical_url is None:
            raise InvalidJobError(
                "The job has no valid link to the original posting.", field_name="url"
            )

        location = clean_text(raw.location, max_chars=MAX_LOCATION_CHARS)
        locations = clean_list(
            [location, *raw.locations] if location else list(raw.locations),
            max_items=MAX_LOCATIONS,
        )
        description = clean_multiline(raw.description, max_chars=MAX_DESCRIPTION_CHARS)
        requirements = clean_list(list(raw.requirements))
        responsibilities = clean_list(list(raw.responsibilities))
        salary = normalize_salary(raw.salary)
        country = clean_text(raw.country)

        job = NormalizedJob(
            source=source,
            source_job_id=source_job_id,
            dedup_key=build_dedup_key(source, source_job_id, canonical_url),
            source_company_key=clean_text(raw.source_company_key, max_chars=MAX_SHORT_TEXT_CHARS),
            canonical_url=canonical_url,
            source_url=normalize_source_url(raw.url),
            apply_url=canonicalize_url(raw.apply_url),
            title=title,
            company_name=company_name,
            location=location or (locations[0] if locations else None),
            locations=locations,
            country=country.upper() if country and len(country) == 2 else None,
            department=clean_text(raw.department, max_chars=MAX_SHORT_TEXT_CHARS),
            description=description,
            requirements=requirements,
            responsibilities=responsibilities,
            technologies=extract_technologies(
                [title, description, *requirements, *responsibilities]
            ),
            salary_min=salary.min_amount if salary else None,
            salary_max=salary.max_amount if salary else None,
            salary_currency=salary.currency if salary else None,
            salary_period=salary.period if salary else None,
            salary_text=clean_text(
                raw.salary.text if raw.salary else None, max_chars=MAX_SALARY_TEXT_CHARS
            ),
            employment_type=normalize_employment_type(
                clean_text(raw.employment_type), title=title
            ),
            work_model=normalize_work_model(
                clean_text(raw.work_model), location_texts=locations, title=title
            ),
            seniority=infer_seniority(title),
            industry=clean_text(raw.industry, max_chars=MAX_SHORT_TEXT_CHARS),
            posted_at=normalize_datetime(raw.posted_at, now=now),
            source_updated_at=normalize_datetime(raw.updated_at, now=now),
            raw_data=_json_safe(raw.raw_payload),
        )
        return replace(job, content_hash=content_hash(job.content_fields()))
