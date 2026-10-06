"""V0.4 normalization: URL/text/company/location keys, vocabularies, salary,
content hash. Pure, deterministic, no I/O."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.services.job_discovery.normalization.company import company_key
from app.services.job_discovery.normalization.content import content_hash, normalized_body
from app.services.job_discovery.normalization.employment import normalize_employment_type
from app.services.job_discovery.normalization.location import normalize_location
from app.services.job_discovery.normalization.salary import parse_salary_text
from app.services.job_discovery.normalization.text import fold, title_key, tokens
from app.services.job_discovery.normalization.url import canonicalize_url, url_identity_key
from app.services.job_discovery.normalization.work_model import normalize_work_model
from app.services.job_discovery.normalizer import JobNormalizer
from app.services.job_discovery.raw_job import RawJob, RawSalary

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

# --- URL normalization -------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://example.com/job/123", "https://example.com/job/123/"),  # trailing slash
        ("http://example.com/job/123", "https://example.com/job/123"),  # http vs https
        ("https://example.com/job/123#apply", "https://example.com/job/123"),  # fragment
        (
            "https://example.com/job/123?utm_source=x&utm_medium=y&utm_campaign=z"
            "&utm_term=t&utm_content=c",
            "https://example.com/job/123",
        ),  # tracking parameters
        ("HTTPS://Example.COM:443/job/123", "https://example.com/job/123"),  # case, default port
        ("http://example.com:80/job/123/", "https://example.com/job/123"),
    ],
)
def test_equivalent_urls_share_an_identity_key(a: str, b: str) -> None:
    assert url_identity_key(a) == url_identity_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # Query parameters that aren't known tracking parameters are meaningful.
        ("https://example.com/careers?gh_jid=1", "https://example.com/careers?gh_jid=2"),
        ("https://example.com/careers?lang=de", "https://example.com/careers"),
        ("https://example.com/job/123", "https://example.com/job/1234"),
        ("https://example.com:8443/job", "https://example.com/job"),  # non-default port
        ("https://a.example.com/job", "https://b.example.com/job"),
    ],
)
def test_different_destinations_keep_different_keys(a: str, b: str) -> None:
    assert url_identity_key(a) != url_identity_key(b)


def test_stored_canonical_url_never_changes_the_destination() -> None:
    # Scheme and trailing slash are kept on the clickable URL: an http-only
    # site must stay reachable. Only the comparison key ignores them.
    assert canonicalize_url("http://example.com/job/1/") == "http://example.com/job/1/"
    assert url_identity_key("http://example.com/job/1/") == "https://example.com/job/1"
    assert (
        canonicalize_url("https://example.com/careers?gh_jid=7&utm_source=x&b=2#top")
        == "https://example.com/careers?gh_jid=7&b=2"
    )


def test_unsafe_urls_have_no_identity_key() -> None:
    assert url_identity_key("javascript:alert(1)") is None
    assert url_identity_key("https://user:pw@example.com/job") is None
    assert url_identity_key(None) is None


# --- Text normalization -------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "Senior SAP ABAP Developer",
        "Senior SAP ABAP-Developer",
        "senior sap abap developer",
        "SENIOR  SAP\tABAP   Developer ",
        "Senior SAP ABAP–Developer",  # en dash
        "Senior SAP ABAP Developer (m/w/d)",
        "Senior SAP ABAP Developer (w/m/d)",
        "Senior SAP ABAP Developer (all genders)",
        "Sr. SAP ABAP Developer",
        "Ｓｅｎｉｏｒ SAP ABAP Developer",  # full-width letters (NFKC)
        "Senior SAP ABAP Developer​",  # zero-width space
    ],
)
def test_title_variants_share_a_key(variant: str) -> None:
    assert title_key(variant) == "senior sap abap developer"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Senior SAP Developer", "SAP Developer"),  # a meaningful word
        ("Senior SAP ABAP Developer", "Junior SAP ABAP Developer"),
        ("C++ Developer", "C Developer"),
        ("C# Developer", "C Developer"),
        ("SAP ABAP Developer", "SAP ABAP Consultant"),
    ],
)
def test_meaningful_words_are_never_removed(a: str, b: str) -> None:
    assert title_key(a) != title_key(b)


def test_fold_handles_unicode_html_and_punctuation() -> None:
    assert fold("Köln") == "koln"
    assert fold("Straße") == "strasse"
    assert fold("København") == "kobenhavn"
    assert fold("R&amp;D <br/> team") == "r and d team"
    assert fold("  Python,   Django;  PostgreSQL! ") == "python django postgresql"
    assert tokens("e‑commerce / B2B") == ["e", "commerce", "b2b"]


# --- Company normalization -----------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    ["Example GmbH", "EXAMPLE GMBH", "Example GmbH.", "example gmbh", "Example  GmbH ", "Example"],
)
def test_company_legal_suffix_variants_share_a_key(variant: str) -> None:
    assert company_key(variant) == "example"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Example AG", "example"),
        ("Example SE", "example"),
        ("Example Ltd", "example"),
        ("Example Ltd.", "example"),
        ("Example Inc.", "example"),
        ("Example, Inc.", "example"),
        ("Example LLC", "example"),
        ("Example GmbH & Co. KG", "example"),
        ("SAP SE", "sap"),
        ("Müller GmbH", "muller"),
    ],
)
def test_common_legal_suffixes(name: str, expected: str) -> None:
    assert company_key(name) == expected


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Example GmbH", "Example Systems GmbH"),
        ("Example GmbH", "Example Group"),
        ("Example GmbH", "Examples GmbH"),
        ("Example Digital GmbH", "Example Consulting GmbH"),
        ("Acme GmbH", "Example GmbH"),
    ],
)
def test_unrelated_companies_never_share_a_key(a: str, b: str) -> None:
    assert company_key(a) != company_key(b)


def test_a_legal_form_alone_is_not_stripped_to_nothing() -> None:
    assert company_key("AG") == "ag"
    assert company_key("GmbH") == "gmbh"


# --- Location normalization -----------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "Köln",
        "Koeln",
        "Cologne",
        "Cologne, Germany",
        "Köln, DE",
        "Köln, Deutschland",
        "Cologne, North Rhine-Westphalia, Germany",
        "KÖLN",
        "Cologne (Hybrid)",
        "Hybrid - Köln",
    ],
)
def test_cologne_variants_share_a_key(variant: str) -> None:
    location = normalize_location(variant)
    assert location is not None
    assert location.key == "de:cologne"
    assert location.city == "Cologne"
    assert location.country == "DE"
    assert location.original == " ".join(variant.split())  # original text kept


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Cologne", "Munich"),
        ("Cologne", "Bonn"),  # close, but a different city
        ("Cologne", "Remote"),
        ("Cologne", "Remote - Cologne"),
        ("Remote - Germany", "Germany"),
        ("Frankfurt am Main", "Frankfurt (Oder)"),
        ("Cologne", "Cologne, US"),  # contradicting country: not the Cologne we know
    ],
)
def test_different_places_keep_different_keys(a: str, b: str) -> None:
    location_a, location_b = normalize_location(a), normalize_location(b)
    assert location_a is not None and location_b is not None
    assert location_a.key != location_b.key


@pytest.mark.parametrize(
    ("text", "key", "remote", "hybrid"),
    [
        ("Remote", "remote", True, False),
        ("100% Remote", "remote", True, False),
        ("Remote - Germany", "remote:de", True, False),
        ("Germany (Remote)", "remote:de", True, False),
        ("Home Office, Deutschland", "remote:de", True, False),
        ("Munich, Hybrid", "de:munich", False, True),
        ("München", "de:munich", False, False),
        ("Germany", "de", False, False),
    ],
)
def test_remote_hybrid_and_country(text: str, key: str, remote: bool, hybrid: bool) -> None:
    location = normalize_location(text)
    assert location is not None
    assert (location.key, location.is_remote, location.is_hybrid) == (key, remote, hybrid)


@pytest.mark.parametrize("text", ["Springfield, IL", "Wilmington, DE", "Berlin, NH", "Hybrid"])
def test_unknown_or_ambiguous_locations_fall_back_to_their_text(text: str) -> None:
    location = normalize_location(text)
    assert location is not None
    assert location.key.startswith("text:")
    assert location.city is None and location.country is None
    assert not location.recognized


def test_empty_location() -> None:
    assert normalize_location(None) is None
    assert normalize_location("   ") is None


# --- Employment type & work model ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("full-time", EmploymentTypeValue.FULL_TIME),
        ("Full Time", EmploymentTypeValue.FULL_TIME),
        ("FULL_TIME", EmploymentTypeValue.FULL_TIME),
        ("Vollzeit", EmploymentTypeValue.FULL_TIME),
        ("PART_TIME", EmploymentTypeValue.PART_TIME),
        ("Teilzeit", EmploymentTypeValue.PART_TIME),
        ("Werkstudent", EmploymentTypeValue.WORKING_STUDENT),
        ("WORKING_STUDENT", EmploymentTypeValue.WORKING_STUDENT),
        ("CONTRACT", EmploymentTypeValue.CONTRACT),
        ("INTERNSHIP", EmploymentTypeValue.INTERNSHIP),
        ("Volunteer", None),  # not in JobPilot's vocabulary: stays unknown
        ("Something else", None),
    ],
)
def test_employment_type_vocabulary(value: str, expected: EmploymentTypeValue | None) -> None:
    assert normalize_employment_type(value, title=None) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Remote", RemotePreference.REMOTE),
        ("100% Remote", RemotePreference.REMOTE),
        ("Home Office", RemotePreference.REMOTE),
        ("Hybrid", RemotePreference.HYBRID),
        ("Hybrid work", RemotePreference.HYBRID),
        ("On-site", RemotePreference.ONSITE),
        ("Unknown", None),
    ],
)
def test_work_model_vocabulary(value: str, expected: RemotePreference | None) -> None:
    assert normalize_work_model(value, location_texts=[], title=None) == expected


def test_work_model_is_never_inferred_from_the_description() -> None:
    job = JobNormalizer().normalize(
        RawJob(
            source="greenhouse",
            source_job_id="1",
            title="Engineer",
            company_name="Acme",
            url="https://example.com/1",
            location="Berlin",
            description="Flexible working hours, occasional remote work possible.",
        ),
        now=NOW,
    )
    assert job.work_model is None


# --- Salary ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "low", "high", "currency", "period"),
    [
        ("€70,000 - €90,000 per year", "70000", "90000", "EUR", SalaryPeriod.YEAR),
        ("70.000 € – 90.000 € brutto/Jahr", "70000", "90000", "EUR", SalaryPeriod.YEAR),
        ("up to €90,000 per year", None, "90000", "EUR", SalaryPeriod.YEAR),
        ("bis zu 5.000 € pro Monat", None, "5000", "EUR", SalaryPeriod.MONTH),
        ("from €60,000", "60000", None, "EUR", None),
        ("CHF 9'500 per month", "9500", None, "CHF", SalaryPeriod.MONTH),
    ],
)
def test_salary_text(
    text: str, low: str | None, high: str | None, currency: str, period: SalaryPeriod | None
) -> None:
    parsed = parse_salary_text(text)
    assert parsed is not None
    assert parsed.min_amount == (Decimal(low) if low else None)
    assert parsed.max_amount == (Decimal(high) if high else None)
    assert (parsed.currency, parsed.period) == (currency, period)


@pytest.mark.parametrize(
    "text",
    ["€60k - €70k + €10k bonus", "Competitive salary", "Attractive package", ""],
)
def test_ambiguous_or_missing_salary_is_not_structured(text: str) -> None:
    assert parse_salary_text(text) is None


def test_ambiguous_salary_text_is_kept_as_written() -> None:
    raw = RawJob(
        source="greenhouse",
        source_job_id="1",
        title="Engineer",
        company_name="Acme",
        url="https://example.com/1",
        salary=RawSalary(text="€60k - €70k + €10k bonus"),
    )
    job = JobNormalizer().normalize(raw, now=NOW)
    assert (job.salary_min, job.salary_max, job.salary_currency) == (None, None, None)
    assert job.salary_text == "€60k - €70k + €10k bonus"


def test_missing_salary_stays_null() -> None:
    job = JobNormalizer().normalize(
        RawJob("greenhouse", "1", "Engineer", "Acme", "https://example.com/1"), now=NOW
    )
    assert (job.salary_min, job.salary_max, job.salary_text) == (None, None, None)


# --- Content body & hash -------------------------------------------------------------------


def _job(**overrides: object) -> RawJob:
    base = RawJob(
        source="greenhouse",
        source_job_id="1",
        title="Senior SAP ABAP Developer",
        company_name="Example GmbH",
        url="https://example.com/jobs/1",
        location="Cologne",
        description="We build SAP solutions.\n\nJoin us.",
        requirements=("ABAP",),
        responsibilities=("Build things",),
        posted_at="2026-09-30T08:00:00Z",
        updated_at="2026-10-01T08:00:00Z",
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_normalized_body_removes_formatting() -> None:
    assert normalized_body("<p>We&nbsp;build</p>\n\n• SAP", ["ABAP"], ["Build"]) == (
        "we build sap build abap"
    )


def test_content_hash_ignores_volatile_and_formatting_only_changes() -> None:
    normalizer = JobNormalizer()
    first = normalizer.normalize(_job(), now=NOW)
    for changed in (
        _job(updated_at="2026-10-05T08:00:00Z"),  # source timestamp
        _job(posted_at="2026-09-29T08:00:00Z"),  # source timestamp
        _job(raw_payload={"seen": "again"}),  # raw payload
        _job(description="We  build SAP solutions.\n\n\n\nJoin us. "),  # whitespace
        _job(title="Senior SAP ABAP-Developer"),  # hyphen
    ):
        assert normalizer.normalize(changed, now=NOW).content_hash == first.content_hash


@pytest.mark.parametrize(
    "overrides",
    [
        {"description": "We build SAP solutions.\n\nJoin us in Munich."},
        {"requirements": ("ABAP", "Fiori")},
        {"responsibilities": ("Build other things",)},
        {"title": "SAP ABAP Developer"},
        {"location": "Munich"},
        {"company_name": "Other GmbH"},
        {"salary": RawSalary(70000, 90000, "EUR")},
        {"employment_type": "Part-time"},
    ],
)
def test_content_hash_tracks_meaningful_changes(overrides: dict[str, object]) -> None:
    normalizer = JobNormalizer()
    assert (
        normalizer.normalize(_job(), now=NOW).content_hash
        != normalizer.normalize(_job(**overrides), now=NOW).content_hash
    )


def test_content_hash_is_deterministic() -> None:
    fields = JobNormalizer().normalize(_job(), now=NOW).content_fields()
    assert content_hash(fields) == content_hash(dict(fields))
