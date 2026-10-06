"""A posting's normalized content, stored as JSON (`JobSourcePosting.normalized_data`).

Each posting keeps its own normalized content so the canonical Job can be
rebuilt from whichever posting is preferred, and so a new posting can be
compared against every posting of a job -- not only against the canonical
text. This module converts between `NormalizedJob` content and that JSON,
and from JSON back to typed `Job` column values.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from app.models.job import Job, SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel

#: Job columns that come from a posting's content.
CANONICAL_CONTENT_FIELDS: tuple[str, ...] = (
    "title",
    "company_name",
    "location",
    "locations",
    "country",
    "department",
    "description",
    "requirements",
    "responsibilities",
    "technologies",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_text",
    "employment_type",
    "work_model",
    "seniority",
    "industry",
    "posted_at",
)

_LIST_FIELDS = frozenset({"locations", "requirements", "responsibilities", "technologies"})
_DECIMAL_FIELDS = frozenset({"salary_min", "salary_max"})
_DATETIME_FIELDS = frozenset({"posted_at", "source_updated_at"})
_ENUM_FIELDS: dict[str, type[Enum]] = {
    "salary_period": SalaryPeriod,
    "employment_type": EmploymentTypeValue,
    "work_model": RemotePreference,
    "seniority": SeniorityLevel,
}


def _encode(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_encode(v) for v in value]
    return value


def encode_content(content: dict[str, Any]) -> dict[str, Any]:
    """`NormalizedJob.content_fields()` -> JSON-safe dict."""
    return {key: _encode(value) for key, value in content.items()}


def _decode(name: str, value: object) -> object:
    if value is None:
        return [] if name in _LIST_FIELDS else None
    if name in _LIST_FIELDS:
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
    if name in _DECIMAL_FIELDS:
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if name in _DATETIME_FIELDS:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if name in _ENUM_FIELDS:
        try:
            return _ENUM_FIELDS[name](value)
        except ValueError:
            return None
    return value


def decode_content(data: dict[str, Any]) -> dict[str, Any]:
    """Stored JSON -> typed values for `CANONICAL_CONTENT_FIELDS`."""
    return {name: _decode(name, data.get(name)) for name in CANONICAL_CONTENT_FIELDS}


def content_of_job(job: Job) -> dict[str, Any]:
    """The canonical content currently on a Job (used for postings migrated
    from V0.3, which have no stored snapshot of their own)."""
    return {name: getattr(job, name) for name in CANONICAL_CONTENT_FIELDS}
