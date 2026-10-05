"""Normalization: RawJob -> NormalizedJob. Pure, deterministic, no I/O."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel
from app.services.job_discovery.normalizer import (
    InvalidJobError,
    JobNormalizer,
    build_dedup_key,
    canonicalize_url,
    clean_multiline,
    clean_text,
    extract_technologies,
    infer_seniority,
    normalize_datetime,
    normalize_employment_type,
    normalize_salary,
    normalize_source_url,
    normalize_work_model,
    parse_salary_text,
)
from app.services.job_discovery.raw_job import RawJob, RawSalary

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def raw(**overrides: object) -> RawJob:
    base = RawJob(
        source="greenhouse",
        source_job_id="123",
        title="Backend Engineer",
        company_name="Acme",
        url="https://job-boards.greenhouse.io/acme/jobs/123",
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --- Whitespace & empty values ---------------------------------------------------


def test_clean_text_collapses_whitespace_and_empties_to_none() -> None:
    assert clean_text("  Senior \n\t Engineer\xa0 ") == "Senior Engineer"
    assert clean_text("   ") is None
    assert clean_text(None) is None
    assert clean_text(42) is None


def test_clean_text_truncates_with_ellipsis() -> None:
    assert clean_text("abcdef", max_chars=4) == "abc…"


def test_clean_multiline_keeps_paragraphs_but_collapses_noise() -> None:
    text = "  First   line \n\n\n\n Second\tline  \n"
    assert clean_multiline(text, max_chars=1000) == "First line\n\nSecond line"


def test_normalizer_cleans_fields_and_drops_empty_list_entries() -> None:
    job = JobNormalizer().normalize(
        raw(
            title="  Senior   Backend Engineer ",
            location="  ",
            locations=("Berlin, Germany", "berlin, germany", " "),
            requirements=("Python", "", "python", "  SQL "),
            description="   ",
        ),
        now=NOW,
    )
    assert job.title == "Senior Backend Engineer"
    assert job.location == "Berlin, Germany"  # falls back to the first listed location
    assert job.locations == ["Berlin, Germany"]
    assert job.requirements == ["Python", "SQL"]
    assert job.description is None


# --- URLs --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "HTTPS://Job-Boards.Greenhouse.IO:443/acme/jobs/1?utm_source=x&utm_medium=y#apply",
            "https://job-boards.greenhouse.io/acme/jobs/1",
        ),
        # Required query parameters are kept byte-for-byte.
        ("https://acme.com/careers?gh_jid=4012346", "https://acme.com/careers?gh_jid=4012346"),
        (
            "https://acme.com/careers?gh_jid=1&gclid=abc&lang=de",
            "https://acme.com/careers?gh_jid=1&lang=de",
        ),
        ("http://example.com:8080/jobs", "http://example.com:8080/jobs"),
        ("https://example.com", "https://example.com/"),
    ],
)
def test_canonicalize_url(url: str, expected: str) -> None:
    assert canonicalize_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/job",
        "/relative/path",
        "https://user:secret@example.com/job",
        "https://exa mple.com/job",
        "https://example.com:99999/job",
        "",
        None,
        "https://" + "a" * 2100 + ".com",
    ],
)
def test_unsafe_or_invalid_urls_are_rejected(url: str | None) -> None:
    assert canonicalize_url(url) is None
    assert normalize_source_url(url) is None


def test_source_url_is_kept_as_returned() -> None:
    url = "https://job-boards.greenhouse.io/acme/jobs/1?utm_source=x"
    assert normalize_source_url(url) == url


def test_job_without_valid_url_is_invalid() -> None:
    with pytest.raises(InvalidJobError) as exc:
        JobNormalizer().normalize(raw(url="javascript:alert(1)"))
    assert exc.value.field_name == "url"


# --- Dates ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-20T08:00:00Z", datetime(2026, 9, 20, 8, tzinfo=UTC)),
        ("2026-09-28T10:15:00-04:00", datetime(2026, 9, 28, 14, 15, tzinfo=UTC)),
        ("2026-09-20", datetime(2026, 9, 20, tzinfo=UTC)),
        (1758000000000, datetime(2025, 9, 16, 5, 20, tzinfo=UTC)),  # epoch ms
        (1758000000, datetime(2025, 9, 16, 5, 20, tzinfo=UTC)),  # epoch s
        ("1758000000000", datetime(2025, 9, 16, 5, 20, tzinfo=UTC)),
        ("Wed, 16 Sep 2026 08:00:00 +0000", datetime(2026, 9, 16, 8, tzinfo=UTC)),
        (datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 9, 0, tzinfo=UTC)),  # naive -> UTC
    ],
)
def test_normalize_datetime(value: object, expected: datetime) -> None:
    assert normalize_datetime(value, now=NOW) == expected


@pytest.mark.parametrize(
    "value", ["not a date", "", None, True, "1970-01-01T00:00:00Z", "2030-01-01T00:00:00Z", 1e20]
)
def test_invalid_or_implausible_dates_become_none(value: object) -> None:
    assert normalize_datetime(value, now=NOW) is None


# --- Salary ---------------------------------------------------------------------------------


def test_structured_salary() -> None:
    salary = normalize_salary(RawSalary(70000, 90000, "eur", "per-year-salary"))
    assert salary is not None
    assert (salary.min_amount, salary.max_amount) == (Decimal("70000.00"), Decimal("90000.00"))
    assert salary.currency == "EUR"
    assert salary.period == SalaryPeriod.YEAR


@pytest.mark.parametrize(
    ("text", "low", "high", "currency", "period"),
    [
        ("€60,000 – €75,000 per year", "60000", "75000", "EUR", SalaryPeriod.YEAR),
        ("60k-75k EUR", "60000", "75000", "EUR", None),
        ("EUR 60.000 - 75.000 p.a.", "60000", "75000", "EUR", SalaryPeriod.YEAR),
        ("$45/hour", "45", None, "USD", SalaryPeriod.HOUR),
        ("CHF 9'500 per month", None, None, None, None),  # unsupported format -> partial parse
    ],
)
def test_salary_text_parsing(
    text: str, low: str | None, high: str | None, currency: str | None, period: SalaryPeriod | None
) -> None:
    parsed = parse_salary_text(text)
    if low is None:
        # "9'500" isn't a recognized thousands format; never guess a range.
        assert parsed is None or parsed.max_amount is None or parsed.min_amount is not None
        return
    assert parsed is not None
    assert parsed.min_amount == Decimal(low)
    assert parsed.max_amount == (Decimal(high) if high else None)
    assert parsed.currency == currency
    assert parsed.period == period


def test_text_salary_is_only_used_without_structured_amounts() -> None:
    salary = normalize_salary(RawSalary(min_amount=50000, text="€90,000 – €100,000"))
    assert salary is not None and salary.min_amount == Decimal("50000.00")


@pytest.mark.parametrize(
    "raw_salary",
    [
        RawSalary(90000, 70000, "EUR"),  # min > max: inconsistent, dropped
        RawSalary(-5, 10, "EUR"),  # negative -> min unknown
        RawSalary(None, None, "EUR"),
        RawSalary(0, 0, "EUR"),
        RawSalary(text="Competitive salary"),
        None,
    ],
)
def test_unusable_salaries(raw_salary: RawSalary | None) -> None:
    salary = normalize_salary(raw_salary)
    if raw_salary is not None and raw_salary.min_amount == -5:
        assert salary is not None and salary.min_amount is None
    else:
        assert salary is None


def test_unknown_currency_is_dropped_not_guessed() -> None:
    salary = normalize_salary(RawSalary(1, 2, "BITCOIN"))
    assert salary is not None and salary.currency is None


# --- Classification ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "title", "expected"),
    [
        ("Full-time", None, EmploymentTypeValue.FULL_TIME),
        ("Vollzeit", None, EmploymentTypeValue.FULL_TIME),
        ("Part time", None, EmploymentTypeValue.PART_TIME),
        ("Contract", None, EmploymentTypeValue.CONTRACT),
        ("Intern", None, EmploymentTypeValue.INTERNSHIP),
        ("Fixed-term", None, EmploymentTypeValue.TEMPORARY),
        ("Freelance", None, EmploymentTypeValue.FREELANCE),
        (None, "Werkstudent Data (m/w/d)", EmploymentTypeValue.WORKING_STUDENT),
        (None, "Software Engineering Intern", EmploymentTypeValue.INTERNSHIP),
        (None, "Contract Manager", None),  # "contract" in a title is not a contract job
        (None, "Internal Audit Lead", None),
        ("Something else", None, None),
    ],
)
def test_employment_type(
    value: str | None, title: str | None, expected: EmploymentTypeValue | None
) -> None:
    assert normalize_employment_type(value, title=title) == expected


@pytest.mark.parametrize(
    ("value", "locations", "expected"),
    [
        ("on-site", [], RemotePreference.ONSITE),
        ("remote", [], RemotePreference.REMOTE),
        ("hybrid", [], RemotePreference.HYBRID),
        (None, ["Remote - Europe"], RemotePreference.REMOTE),
        (None, ["Berlin (Hybrid)"], RemotePreference.HYBRID),
        (None, ["Berlin, Germany"], None),  # never inferred as on-site
    ],
)
def test_work_model(
    value: str | None, locations: list[str], expected: RemotePreference | None
) -> None:
    assert normalize_work_model(value, location_texts=locations, title="Engineer") == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Senior Backend Engineer", SeniorityLevel.SENIOR),
        ("Sr. Data Scientist", SeniorityLevel.SENIOR),
        ("Junior Developer", SeniorityLevel.JUNIOR),
        ("Staff Engineer", SeniorityLevel.LEAD),
        ("Tech Lead Payments", SeniorityLevel.LEAD),
        ("Head of Engineering", SeniorityLevel.DIRECTOR),
        ("Working Student Marketing", SeniorityLevel.INTERN),
        ("Lead Generation Specialist", None),
        ("Software Engineer", None),
    ],
)
def test_seniority(title: str, expected: SeniorityLevel | None) -> None:
    assert infer_seniority(title) == expected


def test_technologies_use_whole_words_and_case_for_ambiguous_terms() -> None:
    technologies = extract_technologies(
        [
            "Senior Python Engineer",
            "We use React, PostgreSQL, k8s and JavaScript. Please react quickly; "
            "we trust our team and value internal tooling.",
        ]
    )
    assert "Python" in technologies
    assert "React" in technologies
    assert "PostgreSQL" in technologies
    assert "Kubernetes" in technologies
    assert "JavaScript" in technologies
    assert "Java" not in technologies  # only appears inside "JavaScript"
    assert "Rust" not in technologies  # "trust"
    assert "SQL" not in technologies  # only inside "PostgreSQL"


def test_no_technologies_in_plain_text() -> None:
    assert extract_technologies(["Accountant", "Prepare monthly reports.", None]) == []


# --- Required fields & identity -------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field_name"),
    [
        ({"title": "   "}, "title"),
        ({"title": None}, "title"),
        ({"company_name": ""}, "company_name"),
        ({"url": None}, "url"),
        ({"source": ""}, "source"),
        ({"source": "Bad Source!"}, "source"),
        ({"source_job_id": "x" * 300}, "source_job_id"),
    ],
)
def test_missing_or_invalid_required_fields(overrides: dict[str, object], field_name: str) -> None:
    with pytest.raises(InvalidJobError) as exc:
        JobNormalizer().normalize(raw(**overrides))
    assert exc.value.field_name == field_name
    assert exc.value.reason  # user-safe explanation


def test_dedup_key_prefers_source_id() -> None:
    job = JobNormalizer().normalize(raw(source_job_id=" 4012345 "))
    assert job.source_job_id == "4012345"
    assert job.dedup_key == "greenhouse:id:4012345"


def test_dedup_key_falls_back_to_canonical_url() -> None:
    a = JobNormalizer().normalize(raw(source_job_id=None, url="https://x.com/job/1?utm_source=a"))
    b = JobNormalizer().normalize(raw(source_job_id=None, url="https://X.com/job/1#top"))
    c = JobNormalizer().normalize(raw(source_job_id=None, url="https://x.com/job/2"))
    assert a.dedup_key.startswith("greenhouse:url:")
    assert a.dedup_key == b.dedup_key  # same posting, different tracking noise
    assert a.dedup_key != c.dedup_key


def test_same_title_never_merges_different_jobs() -> None:
    a = JobNormalizer().normalize(raw(source_job_id="1", url="https://x.com/1"))
    b = JobNormalizer().normalize(raw(source_job_id="2", url="https://x.com/2"))
    assert a.title == b.title
    assert a.dedup_key != b.dedup_key


def test_dedup_key_is_scoped_to_source() -> None:
    assert build_dedup_key("lever", "1", "https://x") != build_dedup_key(
        "greenhouse", "1", "https://x"
    )


def test_content_hash_is_stable_and_tracks_content() -> None:
    normalizer = JobNormalizer()
    first = normalizer.normalize(raw(description="Hello"), now=NOW)
    again = normalizer.normalize(raw(description="  Hello "), now=NOW)
    changed = normalizer.normalize(raw(description="Hello world"), now=NOW)
    assert first.content_hash == again.content_hash
    assert first.content_hash != changed.content_hash


def test_raw_payload_is_kept_as_json() -> None:
    job = JobNormalizer().normalize(raw(raw_payload={"id": 1, "when": NOW}))
    assert job.raw_data == {"id": 1, "when": str(NOW)}
