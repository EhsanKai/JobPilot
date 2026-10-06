"""The immutable application context document (V0.7): what goes in it, what
never does, and how it is checksummed. Pure unit tests, no database."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.models import Job, JobSourcePosting
from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel
from app.schemas.application_context import (
    APPLICATION_CONTEXT_SCHEMA_VERSION,
    ApplicationContextDocument,
    ContextJob,
    ContextSource,
)
from app.services.applications.context import build_context, content_hash, hash_document
from app.services.job_discovery.deduplication.snapshot import CANONICAL_CONTENT_FIELDS

CAPTURED_AT = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def make_job(**overrides: Any) -> Job:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "canonical_url": "https://jobs.example.com/1",
        "apply_url": None,
        "title": "Registered Nurse",
        "company_name": "City Hospital",
        "location": "Hamburg",
        "locations": ["Hamburg", "Remote"],
        "country": "DE",
        "department": "Cardiology",
        "description": "Care for patients.",
        "requirements": ["Nursing degree"],
        "responsibilities": ["Patient care"],
        "technologies": [],
        "salary_min": Decimal("48000"),
        "salary_max": Decimal("56000.5"),
        "salary_currency": "EUR",
        "salary_period": SalaryPeriod.YEAR,
        "salary_text": "48-56k",
        "employment_type": EmploymentTypeValue.PART_TIME,
        "work_model": RemotePreference.ONSITE,
        "seniority": SeniorityLevel.MID,
        "industry": "Healthcare",
        "posted_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return Job(**values)


def make_posting(job: Job, **overrides: Any) -> JobSourcePosting:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "job_id": job.id,
        "source": "greenhouse",
        "source_job_id": "123",
        "source_company_key": "hospital",
        "source_url": "https://boards.example.com/hospital/jobs/123?utm_source=x",
        "canonical_source_url": "https://boards.example.com/hospital/jobs/123",
        "apply_url": None,
        "title": "Nurse (m/f/d)",
        "company_name": "City Hospital gGmbH",
        "location": "Hamburg, Germany",
        "posted_at": datetime(2026, 9, 1, tzinfo=UTC),
        "source_updated_at": datetime(2026, 9, 20, tzinfo=UTC),
    }
    values.update(overrides)
    return JobSourcePosting(**values)


def test_the_job_part_has_exactly_the_canonical_content_fields() -> None:
    declared = set(ContextJob.model_fields)
    # The same fields V0.4 builds a canonical Job from, plus the identity and URLs.
    assert set(CANONICAL_CONTENT_FIELDS) <= declared
    assert declared - set(CANONICAL_CONTENT_FIELDS) == {"job_id", "canonical_url", "apply_url"}


def test_context_captures_the_job_and_the_posting() -> None:
    job = make_job()
    posting = make_posting(job)
    job.preferred_source_posting_id = posting.id

    document = build_context(job, posting, captured_at=CAPTURED_AT)

    assert document.schema_version == APPLICATION_CONTEXT_SCHEMA_VERSION
    assert document.origin == "created"
    assert document.captured_at == CAPTURED_AT
    assert document.job.job_id == job.id
    assert (document.job.title, document.job.company_name) == ("Registered Nurse", "City Hospital")
    assert document.job.locations == ["Hamburg", "Remote"]
    assert document.job.employment_type is EmploymentTypeValue.PART_TIME
    assert document.job.work_model is RemotePreference.ONSITE
    assert document.job.salary_period is SalaryPeriod.YEAR
    # Money has two decimals, so equal amounts hash identically.
    assert (document.job.salary_min, document.job.salary_max) == (
        Decimal("48000.00"),
        Decimal("56000.50"),
    )
    source = document.source
    assert source.posting_id == posting.id
    assert (source.source, source.source_job_id, source.source_company_key) == (
        "greenhouse",
        "123",
        "hospital",
    )
    # What this source said, as written (not the canonical wording).
    assert (source.title, source.company_name) == ("Nurse (m/f/d)", "City Hospital gGmbH")
    assert source.original_url.endswith("?utm_source=x")
    assert source.canonical_url == "https://boards.example.com/hospital/jobs/123"
    assert source.was_preferred is True


def test_a_posting_other_than_the_preferred_one_is_marked() -> None:
    job = make_job()
    chosen = make_posting(job)
    job.preferred_source_posting_id = uuid.uuid4()

    assert build_context(job, chosen, captured_at=CAPTURED_AT).source.was_preferred is False


@pytest.mark.parametrize(
    ("source", "apply_url", "expected"),
    [
        # An explicit application URL always wins.
        ("greenhouse", "https://apply.example.com/go", "https://apply.example.com/go"),
        ("other", "https://apply.example.com/go", "https://apply.example.com/go"),
        # ATS boards host the form on the posting page.
        ("greenhouse", None, "https://boards.example.com/hospital/jobs/123"),
        ("lever", None, "https://boards.example.com/hospital/jobs/123"),
        # Anywhere else it is unknown -- never assumed to be the job's own URL.
        ("other", None, None),
    ],
)
def test_where_applying_happens(source: str, apply_url: str | None, expected: str | None) -> None:
    job = make_job()
    posting = make_posting(job, source=source, apply_url=apply_url)

    document = build_context(job, posting, captured_at=CAPTURED_AT)

    assert document.source.application_url == expected
    assert document.source.apply_url == apply_url


def test_a_job_without_a_posting_falls_back_to_its_own_data() -> None:
    job = make_job(apply_url="https://apply.example.com/direct")

    source = build_context(job, None, captured_at=CAPTURED_AT).source

    assert source.source == "unknown"
    assert source.posting_id is None
    assert (source.title, source.company_name) == ("Registered Nurse", "City Hospital")
    assert source.original_url == job.canonical_url
    assert source.application_url == "https://apply.example.com/direct"
    assert source.was_preferred is False


def test_missing_optional_job_data_stays_missing() -> None:
    job = make_job(
        location=None,
        locations=[],
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        salary_period=None,
        salary_text=None,
        employment_type=None,
        work_model=None,
        seniority=None,
        description=None,
        posted_at=None,
    )

    document = build_context(job, None, captured_at=CAPTURED_AT)

    assert document.job.salary_min is None and document.job.work_model is None
    assert document.job.description is None and document.job.locations == []


def test_the_checksum_is_stable_and_sensitive() -> None:
    job = make_job()
    posting = make_posting(job)
    first = build_context(job, posting, captured_at=CAPTURED_AT)

    same = build_context(job, posting, captured_at=CAPTURED_AT)
    other_time = build_context(job, posting, captured_at=CAPTURED_AT.replace(hour=13))
    job.title = "Senior Nurse"
    changed = build_context(job, posting, captured_at=CAPTURED_AT)

    assert content_hash(first) == content_hash(same)
    assert len(content_hash(first)) == 64
    assert len({content_hash(d) for d in (first, other_time, changed)}) == 3
    # It is the hash of the stored JSON, in canonical form.
    stored = first.model_dump(mode="json")
    assert content_hash(first) == hash_document(stored)
    assert hash_document({"b": 1, "a": 2}) == hash_document({"a": 2, "b": 1})


def test_a_stored_document_validates_back_to_the_same_document() -> None:
    job = make_job()
    document = build_context(job, make_posting(job), captured_at=CAPTURED_AT)

    stored = document.model_dump(mode="json")
    again = ApplicationContextDocument.model_validate(stored)

    # Re-reading and re-dumping is stable, so a checksum can always be verified.
    assert again.model_dump(mode="json") == stored
    assert hash_document(again.model_dump(mode="json")) == content_hash(document)


@pytest.mark.parametrize(
    "extra",
    [
        {"notes": "private"},
        {"salary_expectation": "78000"},
        {"resume_id": str(uuid.uuid4())},
        {"user_id": str(uuid.uuid4())},
        {"raw_data": {"id": 1}},
    ],
)
def test_the_document_can_hold_nothing_the_user_wrote(extra: dict[str, Any]) -> None:
    job = make_job()
    stored = build_context(job, make_posting(job), captured_at=CAPTURED_AT).model_dump(mode="json")

    for part in (None, "job", "source"):
        candidate = (
            {**stored, **extra} if part is None else {**stored, part: {**stored[part], **extra}}
        )
        with pytest.raises(ValidationError):
            ApplicationContextDocument.model_validate(candidate)


def test_the_document_rejects_a_foreign_schema_version_and_origin() -> None:
    job = make_job()
    stored = build_context(job, None, captured_at=CAPTURED_AT).model_dump(mode="json")

    with pytest.raises(ValidationError):
        ApplicationContextDocument.model_validate(
            {**stored, "schema_version": "application_context_v2"}
        )
    with pytest.raises(ValidationError):
        ApplicationContextDocument.model_validate({**stored, "origin": "guessed"})
    assert (
        ApplicationContextDocument.model_validate({**stored, "origin": "migrated"}).origin
        == "migrated"
    )


def test_required_parts_are_required() -> None:
    with pytest.raises(ValidationError):
        ContextSource.model_validate({"source": "x", "title": "t", "company_name": "c"})
    with pytest.raises(ValidationError):
        ContextJob.model_validate({"job_id": str(uuid.uuid4())})
