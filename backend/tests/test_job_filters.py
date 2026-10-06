"""Search criteria validation and JobPilot-side filter semantics."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.schemas.jobs import JobSearchCriteria, SearchFilter
from app.services.job_discovery.filters import (
    matches,
    matches_job_titles,
    matches_keywords,
    matches_locations,
    salary_contradicts,
)
from app.services.job_discovery.normalizer import JobNormalizer, NormalizedJob
from app.services.job_discovery.raw_job import RawJob


def job(**overrides: object) -> NormalizedJob:
    base = JobNormalizer().normalize(
        RawJob(
            source="greenhouse",
            source_job_id="1",
            title="Senior Backend Software Engineer",
            company_name="Acme",
            url="https://x.com/1",
            location="Berlin, Germany",
            description="Build services in Python and Go.",
        )
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --- Criteria validation -------------------------------------------------------------


def test_criteria_cleans_and_dedupes_terms() -> None:
    criteria = JobSearchCriteria(
        keywords=[" python ", "Python", "", "  "],
        locations=["Berlin", "berlin"],
        work_models=[RemotePreference.REMOTE, RemotePreference.REMOTE],
        sources=[" Greenhouse ", "greenhouse"],
        salary_currency="eur",
    )
    assert criteria.keywords == ["python"]
    assert criteria.locations == ["Berlin"]
    assert criteria.work_models == [RemotePreference.REMOTE]
    assert criteria.sources == ["greenhouse"]
    assert criteria.salary_currency == "EUR"


@pytest.mark.parametrize(
    "payload",
    [
        {"salary_min": 80000, "salary_max": 50000},
        {"salary_min": -1},
        {"salary_currency": "E1R"},
        {"posted_after": (datetime.now(UTC).date() + timedelta(days=3)).isoformat()},
        {"keywords": ["x" * 101]},
        {"keywords": [f"k{i}" for i in range(21)]},
        {"limit": 0},
        {"limit": 1000},
        {"page": 0},
        {"work_models": ["underwater"]},
        {"unknown_field": True},
    ],
)
def test_invalid_criteria_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JobSearchCriteria.model_validate(payload)


def test_requested_filters() -> None:
    assert JobSearchCriteria().requested_filters() == set()
    criteria = JobSearchCriteria(
        keywords=["python"],
        industries=["Fintech"],
        salary_max=Decimal(1),
        posted_after=date(2026, 1, 1),
    )
    assert criteria.requested_filters() == {
        SearchFilter.KEYWORDS,
        SearchFilter.INDUSTRIES,
        SearchFilter.SALARY,
        SearchFilter.POSTED_AFTER,
    }


# --- Text filters --------------------------------------------------------------------------


def test_keywords_match_whole_words_anywhere_in_the_posting() -> None:
    assert matches_keywords(job(), ["python"])
    assert matches_keywords(job(), ["rust", "python"])  # OR within the list
    assert matches_keywords(job(), ["acme"])  # company name
    assert not matches_keywords(job(), ["java"])
    assert not matches_keywords(job(description="We use JavaScript."), ["java"])


def test_job_titles_match_when_all_words_appear() -> None:
    assert matches_job_titles(job(), ["Backend Engineer"])
    assert matches_job_titles(job(), ["backend engineer"])
    assert not matches_job_titles(job(), ["Frontend Engineer"])
    assert not matches_job_titles(job(title="Engineering Manager"), ["Engineer"])


def test_locations() -> None:
    assert matches_locations(job(), ["Berlin"])
    assert matches_locations(job(), ["germany"])
    assert not matches_locations(job(), ["Munich"])
    assert not matches_locations(job(location="Berlingen", locations=["Berlingen"]), ["Berlin"])
    assert matches_locations(
        job(location="Anywhere", locations=["Anywhere"], work_model=RemotePreference.REMOTE),
        ["Remote"],
    )


# --- Structured filters: unknown values are kept, contradictions excluded ---------------


def test_work_model_filter() -> None:
    criteria = JobSearchCriteria(work_models=[RemotePreference.REMOTE])
    filters = {SearchFilter.WORK_MODELS}
    assert matches(job(work_model=RemotePreference.REMOTE), criteria, filters)
    assert not matches(job(work_model=RemotePreference.ONSITE), criteria, filters)
    assert matches(job(work_model=None), criteria, filters)


def test_employment_type_filter() -> None:
    criteria = JobSearchCriteria(employment_types=[EmploymentTypeValue.FULL_TIME])
    filters = {SearchFilter.EMPLOYMENT_TYPES}
    assert matches(job(employment_type=EmploymentTypeValue.FULL_TIME), criteria, filters)
    assert not matches(job(employment_type=EmploymentTypeValue.CONTRACT), criteria, filters)
    assert matches(job(employment_type=None), criteria, filters)


def test_salary_filter() -> None:
    criteria = JobSearchCriteria(salary_min=Decimal(60000), salary_currency="EUR")
    eur = {"salary_currency": "EUR", "salary_period": SalaryPeriod.YEAR}
    assert not salary_contradicts(
        job(salary_min=Decimal(55000), salary_max=Decimal(70000), **eur), criteria
    )
    assert salary_contradicts(
        job(salary_min=Decimal(40000), salary_max=Decimal(50000), **eur), criteria
    )
    # Monthly salaries are annualized.
    monthly = {"salary_currency": "EUR", "salary_period": SalaryPeriod.MONTH}
    assert not salary_contradicts(job(salary_min=Decimal(5500), **monthly), criteria)
    # Unknown, other currency, or hourly: not comparable -> kept.
    assert not salary_contradicts(job(), criteria)
    assert not salary_contradicts(job(salary_max=Decimal(1000), salary_currency="USD"), criteria)
    hourly = {"salary_currency": "EUR", "salary_period": SalaryPeriod.HOUR}
    assert not salary_contradicts(job(salary_max=Decimal(50), **hourly), criteria)

    upper = JobSearchCriteria(salary_max=Decimal(50000))
    assert salary_contradicts(job(salary_min=Decimal(70000), **eur), upper)


def test_posted_after_filter() -> None:
    criteria = JobSearchCriteria(posted_after=date(2026, 9, 1))
    filters = {SearchFilter.POSTED_AFTER}
    assert matches(job(posted_at=datetime(2026, 9, 2, tzinfo=UTC)), criteria, filters)
    assert not matches(job(posted_at=datetime(2026, 8, 31, tzinfo=UTC)), criteria, filters)
    assert matches(job(posted_at=None), criteria, filters)


def test_filters_are_anded() -> None:
    criteria = JobSearchCriteria(keywords=["python"], locations=["Munich"])
    assert not matches(job(), criteria, {SearchFilter.KEYWORDS, SearchFilter.LOCATIONS})
    assert matches(job(), criteria, {SearchFilter.KEYWORDS})  # only the filters asked for
