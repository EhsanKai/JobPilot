"""The preparation checklist (V0.7): complete exactly when the data is
present. Pure unit tests on transient (unsaved) applications."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.application import (
    Application,
    NoticePeriodUnit,
    WorkAuthorization,
)
from app.services.applications.progress import PREPARATION_ITEMS, preparation_progress


def make_application(**values: object) -> Application:
    return Application(**values)


def complete(application: Application) -> dict[str, bool]:
    return {item.key: item.complete for item in preparation_progress(application).items}


def test_a_fresh_application_has_only_its_job() -> None:
    progress = preparation_progress(make_application())

    assert (progress.completed, progress.total) == (1, 6)
    assert [(item.key, item.complete, item.required) for item in progress.items] == [
        ("job", True, True),
        ("resume", False, True),
        ("language", False, True),
        ("salary", False, True),
        ("availability", False, True),
        ("notice_period", False, True),
        ("work_authorization", False, False),
    ]


def test_the_item_order_and_requirements_are_fixed() -> None:
    assert [key for key, _ in PREPARATION_ITEMS] == [
        "job",
        "resume",
        "language",
        "salary",
        "availability",
        "notice_period",
        "work_authorization",
    ]
    assert [key for key, required in PREPARATION_ITEMS if not required] == ["work_authorization"]


@pytest.mark.parametrize(
    ("values", "key"),
    [
        ({"resume_id": uuid.uuid4()}, "resume"),
        ({"application_language": "de"}, "language"),
        ({"salary_expectation": Decimal("70000"), "salary_currency": "EUR"}, "salary"),
        ({"available_from": date(2027, 1, 1)}, "availability"),
        (
            {"notice_period_value": 0, "notice_period_unit": NoticePeriodUnit.DAYS},
            "notice_period",
        ),
        ({"work_authorization": WorkAuthorization.UNKNOWN}, "work_authorization"),
    ],
)
def test_each_item_completes_with_its_own_data(values: dict[str, object], key: str) -> None:
    done = complete(make_application(**values))

    assert done[key] is True
    assert [other for other, is_done in done.items() if is_done and other != "job"] == [key]


def test_a_salary_needs_both_amount_and_currency() -> None:
    assert complete(make_application(salary_expectation=Decimal("70000")))["salary"] is False
    assert complete(make_application(salary_currency="EUR"))["salary"] is False


def test_a_zero_day_notice_period_counts_as_stated() -> None:
    application = make_application(notice_period_value=0, notice_period_unit=NoticePeriodUnit.DAYS)
    assert complete(application)["notice_period"] is True


def test_the_optional_item_is_never_counted() -> None:
    application = make_application(
        resume_id=uuid.uuid4(),
        application_language="en",
        salary_expectation=Decimal("1"),
        salary_currency="EUR",
        available_from=date(2027, 1, 1),
        notice_period_value=1,
        notice_period_unit=NoticePeriodUnit.MONTHS,
    )

    without = preparation_progress(application)
    application.work_authorization = WorkAuthorization.AUTHORIZED
    with_it = preparation_progress(application)

    assert (without.completed, without.total) == (6, 6)
    assert (with_it.completed, with_it.total) == (6, 6)
    assert {i.key: i.complete for i in with_it.items}["work_authorization"] is True
