"""Request validation of the Application Workspace (V0.7). Pure unit tests:
what the API accepts, normalizes and rejects, and the difference between an
omitted field and an explicit null."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.application import NoticePeriodUnit, WorkAuthorization
from app.schemas.application import (
    MAX_NOTICE_PERIOD,
    MAX_SALARY_EXPECTATION,
    SUPPORTED_APPLICATION_LANGUAGES,
    ApplicationCreate,
    ApplicationUpdate,
    NoticePeriod,
)


def rejected(model: Any, data: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(data)
    return str(caught.value)


def test_everything_is_optional() -> None:
    update = ApplicationUpdate.model_validate({})
    create = ApplicationCreate.model_validate({})

    assert update.model_fields_set == set() and create.model_fields_set == set()
    assert update.salary_expectation is None and create.application_language is None


def test_omitted_and_null_are_different() -> None:
    omitted = ApplicationUpdate.model_validate({"notes": "x"})
    cleared = ApplicationUpdate.model_validate({"notes": "x", "notice_period": None})

    assert "notice_period" not in omitted.model_fields_set
    assert "notice_period" in cleared.model_fields_set and cleared.notice_period is None


def test_a_full_valid_update_is_normalized() -> None:
    update = ApplicationUpdate.model_validate(
        {
            "salary_expectation": "78000.50",
            "salary_currency": "eur",
            "mention_salary_in_letter": False,
            "available_from": "2027-01-01",
            "notice_period": {"value": 3, "unit": "months"},
            "work_authorization": "requires_sponsorship",
            "application_language": "EN",
            "notes": "  hello  ",
        }
    )

    assert update.salary_expectation == Decimal("78000.50")
    assert update.salary_currency == "EUR"
    assert update.mention_salary_in_letter is False
    assert update.available_from == date(2027, 1, 1)
    assert update.notice_period == NoticePeriod(value=3, unit=NoticePeriodUnit.MONTHS)
    assert update.work_authorization is WorkAuthorization.REQUIRES_SPONSORSHIP
    assert update.application_language == "en"
    assert update.notes == "hello"


@pytest.mark.parametrize("value", [1, "0.01", 78000, "9999999.99", MAX_SALARY_EXPECTATION])
def test_valid_salaries(value: Any) -> None:
    assert ApplicationUpdate.model_validate({"salary_expectation": value}).salary_expectation


@pytest.mark.parametrize(
    "value",
    [0, -5, "0", "-0.01", "abc", "", "NaN", "Infinity", 10_000_000.01, "1.234", "1e12", [], {}],
)
def test_invalid_salaries(value: Any) -> None:
    rejected(ApplicationUpdate, {"salary_expectation": value})


def test_salary_messages_say_what_is_wrong() -> None:
    assert "greater than 0" in rejected(ApplicationUpdate, {"salary_expectation": 0})
    assert "at most 10,000,000" in rejected(ApplicationUpdate, {"salary_expectation": 10_000_001})
    assert "2 decimal places" in rejected(ApplicationUpdate, {"salary_expectation": "1.234"})


@pytest.mark.parametrize("value", ["EUR", "eur", " Chf ", "USD"])
def test_valid_currencies(value: str) -> None:
    assert (
        ApplicationUpdate.model_validate({"salary_currency": value}).salary_currency
        == value.strip().upper()
    )


@pytest.mark.parametrize("value", ["EU", "EURO", "E1R", "", "€", 3])
def test_invalid_currencies(value: Any) -> None:
    rejected(ApplicationUpdate, {"salary_currency": value})


def test_availability_bounds() -> None:
    today = datetime.now(UTC).date()

    for valid in (
        today,
        today - timedelta(days=400),
        today + timedelta(days=365),
        date(2000, 1, 1),
    ):
        assert (
            ApplicationUpdate.model_validate({"available_from": valid.isoformat()}).available_from
            == valid
        )
    for invalid in (
        date(1999, 12, 31).isoformat(),
        (today + timedelta(days=5 * 366 + 1)).isoformat(),
        "2027-02-30",
        "tomorrow",
        "",
        20270101,
    ):
        assert "available_from" in rejected(ApplicationUpdate, {"available_from": invalid})


@pytest.mark.parametrize("unit", list(NoticePeriodUnit))
def test_notice_period_limits_per_unit(unit: NoticePeriodUnit) -> None:
    limit = MAX_NOTICE_PERIOD[unit]
    for value in (0, 1, limit):
        assert NoticePeriod(value=value, unit=unit).value == value
    for value in (-1, limit + 1):
        with pytest.raises(ValidationError):
            NoticePeriod(value=value, unit=unit)


def test_notice_period_needs_both_parts_and_nothing_else() -> None:
    assert "unit" in rejected(ApplicationUpdate, {"notice_period": {"value": 3}})
    assert "value" in rejected(ApplicationUpdate, {"notice_period": {"unit": "weeks"}})
    rejected(ApplicationUpdate, {"notice_period": {"value": 3, "unit": "weeks", "legal": "x"}})
    rejected(ApplicationUpdate, {"notice_period": {"value": "three", "unit": "weeks"}})
    rejected(ApplicationUpdate, {"notice_period": {"value": 3, "unit": "decades"}})


def test_languages_are_the_supported_iso_keys() -> None:
    assert SUPPORTED_APPLICATION_LANGUAGES == ("de", "en")
    for language in SUPPORTED_APPLICATION_LANGUAGES:
        assert ApplicationUpdate.model_validate({"application_language": language})
    assert "one of: de, en" in rejected(ApplicationUpdate, {"application_language": "fr"})
    rejected(ApplicationUpdate, {"application_language": "German"})
    rejected(ApplicationUpdate, {"application_language": "d"})


def test_work_authorization_values() -> None:
    assert {a.value for a in WorkAuthorization} == {"authorized", "requires_sponsorship", "unknown"}
    for value in ("authorized", "requires_sponsorship", "unknown"):
        assert ApplicationUpdate.model_validate({"work_authorization": value})
    rejected(ApplicationUpdate, {"work_authorization": "Authorized"})
    rejected(ApplicationUpdate, {"work_authorization": "yes"})
    rejected(ApplicationUpdate, {"work_authorization": True})


def test_notes_are_trimmed_and_blank_means_none() -> None:
    assert ApplicationUpdate.model_validate({"notes": "  a\nb  "}).notes == "a\nb"
    assert ApplicationUpdate.model_validate({"notes": " \n "}).notes is None
    assert ApplicationUpdate.model_validate({"notes": "x" * 10_000}).notes == "x" * 10_000
    rejected(ApplicationUpdate, {"notes": "x" * 10_001})


@pytest.mark.parametrize(
    "field", ["status", "user_id", "job_id", "user_job_id", "context", "id", "created_at"]
)
def test_clients_cannot_set_server_owned_fields(field: str) -> None:
    # Neither when starting an application nor when updating it. (The source
    # posting *can* be chosen when starting: see the test below.)
    assert "extra inputs are not permitted" in rejected(ApplicationUpdate, {field: "x"}).lower()
    assert "extra inputs are not permitted" in rejected(ApplicationCreate, {field: "x"}).lower()


def test_a_posting_can_be_chosen_only_when_starting() -> None:
    posting = str(uuid.uuid4())
    assert ApplicationCreate.model_validate({"source_posting_id": posting}).source_posting_id
    rejected(ApplicationUpdate, {"source_posting_id": posting})


def test_a_resume_can_be_replaced_but_not_cleared() -> None:
    resume = str(uuid.uuid4())
    assert ApplicationUpdate.model_validate({"resume_id": resume}).resume_id == uuid.UUID(resume)
    assert ApplicationUpdate.model_validate({}).resume_id is None  # omitted: unchanged
    assert "can't be removed" in rejected(ApplicationUpdate, {"resume_id": None})
    rejected(ApplicationUpdate, {"resume_id": "not-a-uuid"})
    # When starting, no resume simply means "pick the default".
    assert ApplicationCreate.model_validate({"resume_id": None}).resume_id is None
