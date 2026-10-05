"""Deterministic normalization: `RawJob` -> `NormalizedJob`.

    RawJob (source-shaped, loose) --> JobNormalizer --> NormalizedJob (canonical, validated)

All cleaning rules live here, once, instead of in every adapter:
whitespace, empty values, URLs, dates, salary, employment type, work model,
seniority, technologies, identifiers -- and required-field validation. No AI.

A posting that fails validation raises `InvalidJobError` with a user-safe
reason. The discovery service counts it as invalid and carries on with the
rest of the batch.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel
from app.services.job_discovery.raw_job import RawJob, RawSalary
from app.services.job_discovery.vocabulary import (
    CURRENCY_CODES,
    CURRENCY_SYMBOLS,
    EMPLOYMENT_TYPE_PATTERNS,
    EMPLOYMENT_TYPE_TITLE_PATTERNS,
    SALARY_PERIOD_PATTERNS,
    SENIORITY_TITLE_PATTERNS,
    TECHNOLOGY_PATTERNS,
    WORK_MODEL_LOCATION_PATTERNS,
    WORK_MODEL_PATTERNS,
)

NORMALIZER_VERSION = "job_normalizer_v1"

MAX_TITLE_CHARS = 500
MAX_COMPANY_CHARS = 255
MAX_LOCATION_CHARS = 500
MAX_SHORT_TEXT_CHARS = 255
MAX_URL_CHARS = 2000
MAX_SOURCE_ID_CHARS = 255
MAX_DESCRIPTION_CHARS = 100_000
MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_CHARS = 1000
MAX_LOCATIONS = 30
# Largest value NUMERIC(12, 2) can hold, rounded down.
MAX_SALARY = Decimal("9999999999")

_SOURCE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset(
    {"gclid", "fbclid", "msclkid", "dclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "igshid"}
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


class InvalidJobError(Exception):
    """A posting that can't become a canonical Job. `reason` is user-safe."""

    def __init__(self, reason: str, *, field_name: str | None = None) -> None:
        self.reason = reason
        self.field_name = field_name
        super().__init__(reason)


@dataclass(frozen=True)
class NormalizedSalary:
    min_amount: Decimal | None
    max_amount: Decimal | None
    currency: str | None
    period: SalaryPeriod | None


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
        """The fields stored on `Job` (everything except identity/provenance
        that the persistence layer manages itself)."""
        data = asdict(self)
        for key in ("raw_data", "content_hash", "normalizer_version", "dedup_key"):
            data.pop(key)
        return data


# --- Text -----------------------------------------------------------------------------


def clean_text(value: object, *, max_chars: int | None = None) -> str | None:
    """Single-line text: collapse all whitespace; empty -> None."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("\xa0", " ").split())
    if not text:
        return None
    if max_chars is not None and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def clean_multiline(value: object, *, max_chars: int) -> str | None:
    """Multi-line text: trim each line, collapse runs of spaces and of blank
    lines, keep paragraph structure; empty -> None."""
    if not isinstance(value, str):
        return None
    lines = [" ".join(line.replace("\xa0", " ").split()) for line in value.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        return None
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def clean_list(values: object, *, max_items: int = MAX_LIST_ITEMS) -> list[str]:
    """Clean each entry, drop empties and case-insensitive duplicates."""
    if not isinstance(values, list | tuple):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value, max_chars=MAX_LIST_ITEM_CHARS)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
        if len(result) >= max_items:
            break
    return result


# --- URLs -------------------------------------------------------------------------------


def _valid_http_url_parts(value: object) -> tuple[str, Any] | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_URL_CHARS or any(c.isspace() for c in value):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        # Credentials in a job URL are never legitimate; don't store them.
        return None
    return value, (scheme, parts, port)


def normalize_source_url(value: object) -> str | None:
    """The URL as the source gave it, if it is a safe absolute http(s) URL.
    Anything else (javascript:, data:, relative, credentials) -> None."""
    checked = _valid_http_url_parts(value)
    return checked[0] if checked else None


def canonicalize_url(value: object) -> str | None:
    """A stable form of a posting URL, for display and deduplication.

    Lowercases scheme and host, drops default ports, the fragment and known
    tracking parameters (utm_*, gclid, ...). Every other query parameter is
    kept, in order and byte-for-byte, because some are required to reach the
    posting (e.g. Greenhouse's `?gh_jid=...` on company career pages).
    """
    checked = _valid_http_url_parts(value)
    if checked is None:
        return None
    _, (scheme, parts, port) = checked
    host = parts.hostname.lower().rstrip(".")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"

    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        kept = [
            (k, v)
            for k, v in pairs
            if k.lower() not in _TRACKING_PARAMS
            and not k.lower().startswith(_TRACKING_PARAM_PREFIXES)
        ]
        if len(kept) != len(pairs):  # only re-encode when something was removed
            query = urlencode(kept)
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


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


# --- Salary ----------------------------------------------------------------------------------


def normalize_currency(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[text]
    code = text.upper()
    return code if code in CURRENCY_CODES else None


def normalize_salary_period(value: object) -> SalaryPeriod | None:
    if not isinstance(value, str) or not value.strip():
        return None
    for period, pattern in SALARY_PERIOD_PATTERNS:
        if pattern.search(value):
            return period
    return None


def _to_amount(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, Decimal):
            amount = value
        elif isinstance(value, int | float):
            amount = Decimal(str(value))
        elif isinstance(value, str) and value.strip():
            amount = Decimal(value.strip().replace(",", ""))
        else:
            return None
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount > MAX_SALARY:
        return None
    return amount.quantize(Decimal("0.01"))


_AMOUNT_RE = re.compile(
    r"(?P<number>\d{1,3}(?:[.,   ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?)"
    r"\s*(?P<k>[kK]\b|tsd\.?|thousand)?"
)


def parse_salary_text(text: str) -> NormalizedSalary | None:
    """Parse simple salary text like "€60,000 – €75,000 per year",
    "60k-75k EUR", "EUR 60.000 - 75.000 p.a." or "$45/hour".

    Takes at most the first two amounts. Returns None when no amount is found.
    """
    amounts: list[Decimal] = []
    for match in _AMOUNT_RE.finditer(text):
        number = match.group("number")
        multiplier = Decimal(1000) if match.group("k") else Decimal(1)
        if re.fullmatch(r"\d{1,3}(?:[.,   ]\d{3})+", number) and not match.group("k"):
            # Thousands separators only, e.g. "60,000" or "60.000".
            number = re.sub(r"[.,   ]", "", number)
        elif re.fullmatch(r"\d{1,3}(?:[.,   ]\d{3})+[.,]\d{1,2}", number):
            # Thousands separators plus decimals, e.g. "60.000,00".
            number = re.sub(r"[.,   ]", "", number[:-3]) + "." + number[-2:]
        else:
            number = number.replace(",", ".")
        amount = _to_amount(Decimal(number) * multiplier) if number else None
        if amount is not None:
            amounts.append(amount)
        if len(amounts) == 2:
            break
    if not amounts:
        return None

    currency = None
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            break
    if currency is None:
        for token in re.findall(r"\b[A-Z]{3}\b", text.upper()):
            if token in CURRENCY_CODES:
                currency = token
                break
    low, high = (amounts[0], amounts[1]) if len(amounts) == 2 else (amounts[0], None)
    return NormalizedSalary(low, high, currency, normalize_salary_period(text))


def normalize_salary(raw: RawSalary | None) -> NormalizedSalary | None:
    """Structured amounts win; free text is parsed only when no amount is
    given. Inconsistent ranges (min > max) are dropped, not guessed at."""
    if raw is None:
        return None
    min_amount = _to_amount(raw.min_amount)
    max_amount = _to_amount(raw.max_amount)
    currency = normalize_currency(raw.currency)
    period = normalize_salary_period(raw.period)

    if min_amount is None and max_amount is None and raw.text:
        parsed = parse_salary_text(raw.text)
        if parsed is not None:
            min_amount, max_amount = parsed.min_amount, parsed.max_amount
            currency = currency or parsed.currency
            period = period or parsed.period

    if min_amount is None and max_amount is None:
        return None
    if min_amount == 0 and max_amount in (None, 0):
        return None
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        return None
    return NormalizedSalary(min_amount, max_amount, currency, period)


# --- Classification -------------------------------------------------------------------------


def normalize_employment_type(
    value: str | None, *, title: str | None
) -> EmploymentTypeValue | None:
    if value:
        for employment_type, pattern in EMPLOYMENT_TYPE_PATTERNS:
            if pattern.search(value):
                return employment_type
    if title:
        for employment_type, pattern in EMPLOYMENT_TYPE_TITLE_PATTERNS:
            if pattern.search(title):
                return employment_type
    return None


def normalize_work_model(
    value: str | None, *, location_texts: list[str], title: str | None
) -> RemotePreference | None:
    if value:
        for work_model, pattern in WORK_MODEL_PATTERNS:
            if pattern.search(value):
                return work_model
    for text in [title or "", *location_texts]:
        for work_model, pattern in WORK_MODEL_LOCATION_PATTERNS:
            if pattern.search(text):
                return work_model
    return None


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


def _content_hash(job: NormalizedJob) -> str:
    serialized = json.dumps(job.content_fields(), sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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
        return replace(job, content_hash=_content_hash(job))
