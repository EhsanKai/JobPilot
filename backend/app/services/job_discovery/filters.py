"""Deterministic evaluation of `JobSearchCriteria` against normalized jobs.

Used for every filter a source can't apply natively. Rules:

- Text criteria (keywords, job titles, locations) are always evaluable,
  because every job has a title and URL and almost always a location.
  Matching is case-insensitive on whole words, never substrings of words
  ("java" doesn't match "javascript", "intern" doesn't match "internal").
- Structured criteria (work model, employment type, salary, posted date)
  exclude a job only when its value is **known and contradicts** the
  criterion. When a source doesn't state the value, the job is kept, and the
  UI shows the value as "not specified" -- dropping it would hide most jobs
  from sources that rarely state e.g. salary, while keeping it never claims
  a match JobPilot can't see.
- Industries are not evaluable: no implemented source provides an industry,
  so the filter is reported as not applied instead of pretending.
"""

import re
from decimal import Decimal

from app.models.job import SalaryPeriod
from app.models.job_preferences import RemotePreference
from app.schemas.jobs import JobSearchCriteria, SearchFilter
from app.services.job_discovery.normalizer import NormalizedJob

JOBPILOT_EVALUABLE_FILTERS = frozenset(
    {
        SearchFilter.KEYWORDS,
        SearchFilter.JOB_TITLES,
        SearchFilter.LOCATIONS,
        SearchFilter.WORK_MODELS,
        SearchFilter.EMPLOYMENT_TYPES,
        SearchFilter.SALARY,
        SearchFilter.POSTED_AFTER,
    }
)

_TOKEN_RE = re.compile(r"[\w+#]+", re.UNICODE)
_ANNUAL_FACTORS = {SalaryPeriod.YEAR: Decimal(1), SalaryPeriod.MONTH: Decimal(12)}


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w+#]){re.escape(phrase.casefold())}(?![\w+#])")


def _contains_phrase(texts: list[str], phrase: str) -> bool:
    pattern = _phrase_pattern(phrase)
    return any(pattern.search(text.casefold()) for text in texts)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.casefold()))


def matches_keywords(job: NormalizedJob, keywords: list[str]) -> bool:
    texts = [
        job.title,
        job.company_name,
        job.department or "",
        job.description or "",
        *job.requirements,
        *job.responsibilities,
        *job.technologies,
    ]
    return any(_contains_phrase(texts, keyword) for keyword in keywords)


def matches_job_titles(job: NormalizedJob, titles: list[str]) -> bool:
    """A desired title matches when all of its words appear in the job
    title: "Backend Engineer" matches "Senior Backend Software Engineer"."""
    job_tokens = _tokens(job.title)
    return any((wanted := _tokens(title)) and wanted <= job_tokens for title in titles)


def matches_locations(job: NormalizedJob, locations: list[str]) -> bool:
    texts = [t for t in [job.location, *job.locations] if t]
    for location in locations:
        if location.casefold() == "remote" and job.work_model == RemotePreference.REMOTE:
            return True
        if _contains_phrase(texts, location):
            return True
    return False


def _annual(amount: Decimal | None, period: SalaryPeriod | None) -> Decimal | None:
    if amount is None:
        return None
    factor = _ANNUAL_FACTORS.get(period or SalaryPeriod.YEAR)
    return amount * factor if factor is not None else None


def salary_contradicts(job: NormalizedJob, criteria: JobSearchCriteria) -> bool:
    """True only when the job's salary is known, comparable (same currency,
    annual or monthly) and outside the requested range."""
    if job.salary_min is None and job.salary_max is None:
        return False
    if criteria.salary_currency and job.salary_currency not in (None, criteria.salary_currency):
        return False
    job_min = job.salary_min if job.salary_min is not None else job.salary_max
    job_max = job.salary_max if job.salary_max is not None else job.salary_min
    low = _annual(job_min, job.salary_period)
    high = _annual(job_max, job.salary_period)
    if low is None or high is None:
        return False
    if criteria.salary_min is not None and high < criteria.salary_min:
        return True
    if criteria.salary_max is not None and low > criteria.salary_max:
        return True
    return False


def matches(job: NormalizedJob, criteria: JobSearchCriteria, filters: set[SearchFilter]) -> bool:
    """Whether `job` satisfies every filter in `filters` (AND across filters,
    OR within each filter's values)."""
    if SearchFilter.KEYWORDS in filters and not matches_keywords(job, criteria.keywords):
        return False
    if SearchFilter.JOB_TITLES in filters and not matches_job_titles(job, criteria.job_titles):
        return False
    if SearchFilter.LOCATIONS in filters and not matches_locations(job, criteria.locations):
        return False
    if (
        SearchFilter.WORK_MODELS in filters
        and job.work_model is not None
        and job.work_model not in criteria.work_models
    ):
        return False
    if (
        SearchFilter.EMPLOYMENT_TYPES in filters
        and job.employment_type is not None
        and job.employment_type not in criteria.employment_types
    ):
        return False
    if SearchFilter.SALARY in filters and salary_contradicts(job, criteria):
        return False
    if (
        SearchFilter.POSTED_AFTER in filters
        and criteria.posted_after is not None
        and job.posted_at is not None
        and job.posted_at.date() < criteria.posted_after
    ):
        return False
    return True
