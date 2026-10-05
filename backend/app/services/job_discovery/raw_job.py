"""The source-independent shape of a job posting *before* normalization.

    External source --> JobSource adapter --> RawJob --> JobNormalizer --> NormalizedJob --> Job

Every adapter, whatever its acquisition method (JSON API, ATS endpoint,
HTML page), converts its own format into `RawJob`. Fields are deliberately
loose (strings as the source gave them, salary and dates possibly unparsed):
cleaning, parsing and validation are the normalizer's job, so the rules live
in one place instead of once per source.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class RawSalary:
    """Salary as the source expressed it. Any subset may be present."""

    min_amount: Decimal | float | int | str | None = None
    max_amount: Decimal | float | int | str | None = None
    currency: str | None = None
    #: Free-text period/interval, e.g. "per-year-salary" or "hourly".
    period: str | None = None
    #: Free-text salary, e.g. "€60,000 – €75,000 per year". Only parsed when
    #: no structured amounts are present.
    text: str | None = None


@dataclass(frozen=True)
class RawJob:
    #: Registry name of the source, e.g. "greenhouse".
    source: str
    #: The posting's id at the source, unique within that source. None when
    #: the source has no stable id (dedup then falls back to the URL).
    source_job_id: str | None
    title: str | None
    company_name: str | None
    #: The posting's URL as returned by the source.
    url: str | None
    #: Board/site within the source (e.g. a Greenhouse board token).
    source_company_key: str | None = None
    apply_url: str | None = None
    location: str | None = None
    #: All locations, when the source lists several.
    locations: tuple[str, ...] = ()
    country: str | None = None
    department: str | None = None
    #: Plain text. Adapters convert HTML with `sources.html.html_to_text`.
    description: str | None = None
    requirements: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()
    salary: RawSalary | None = None
    #: Free text as the source states it, e.g. "Full-time" or "Werkstudent".
    employment_type: str | None = None
    #: Free text, e.g. "remote", "on-site", "hybrid".
    work_model: str | None = None
    industry: str | None = None
    #: ISO 8601 string, epoch milliseconds, or datetime.
    posted_at: str | int | float | datetime | None = None
    updated_at: str | int | float | datetime | None = None
    #: The source's own payload for this posting.
    raw_payload: dict[str, Any] = field(default_factory=dict)
