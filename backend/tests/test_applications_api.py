"""V0.7 Application Workspace API: start, read, update, list -- and the rules
around them (one application per UserJob, ownership, immutable context,
resume and source references, isolation from profile / job / match)."""

import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationContextSnapshot,
    ApplicationStatus,
    Job,
    JobMatch,
    JobPreferences,
    JobSourcePosting,
    ProfessionalProfile,
    Resume,
    User,
    UserJob,
    UserProfile,
)
from app.models.mixins import ImmutableRecordError
from app.services.applications import service
from app.services.applications.context import hash_document
from tests.application_helpers import (
    API,
    confirmed_resume,
    make_job,
    make_posting,
    patch,
    start,
    started,
    upload_resume,
    user_by_email,
)
from tests.conftest import TestSessionLocal

PREPARATION_FIELDS = (
    "salary_expectation",
    "salary_currency",
    "mention_salary_in_letter",
    "available_from",
    "notice_period",
    "work_authorization",
    "application_language",
    "notes",
)

FULL_PREPARATION: dict[str, Any] = {
    "salary_expectation": "78000",
    "salary_currency": "EUR",
    "mention_salary_in_letter": True,
    "available_from": "2027-01-01",
    "notice_period": {"value": 3, "unit": "months"},
    "work_authorization": "authorized",
    "application_language": "de",
    "notes": "Referral from Alex. Ask about the remote policy.",
}


@pytest.fixture
def auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {register_and_login()['access_token']}"}


@pytest.fixture
def other_auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = register_and_login(email="mallory@example.com")["access_token"]
    return {"Authorization": f"Bearer {token}"}


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def get(client: TestClient, headers: dict[str, str], application_id: str) -> dict[str, Any]:
    response = client.get(f"{API}/applications/{application_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def run_concurrently(*targets: Any) -> list[BaseException]:
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(targets))

    def wrap(target: Any) -> None:
        try:
            barrier.wait(timeout=10)
            target()
        except BaseException as exc:  # noqa: BLE001 -- reported to the test
            errors.append(exc)

    threads = [threading.Thread(target=wrap, args=(t,)) for t in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return errors


# --- Creation -------------------------------------------------------------------------------


def test_start_creates_a_preparing_application_with_its_context(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))

    response = start(client, auth, job)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PREPARING"
    assert body["job_id"] == str(job.id)
    # Nothing is invented for the user.
    assert all(body[field] is None for field in PREPARATION_FIELDS)
    assert body["resume"] is None
    assert body["match"] is None
    # The context is the job as it is now, plus the posting it was found on.
    context = body["context"]
    assert context["schema_version"] == "application_context_v1"
    assert context["origin"] == "created"
    assert len(context["content_hash"]) == 64
    assert context["job"]["job_id"] == str(job.id)
    assert context["job"]["title"] == "Senior Project Manager"
    assert context["job"]["company_name"] == "Example GmbH"
    assert context["job"]["location"] == "Köln"
    assert context["job"]["employment_type"] == "full_time"
    assert context["job"]["work_model"] == "hybrid"
    assert context["job"]["salary_min"] == "70000.00"
    assert context["job"]["salary_max"] == "80000.00"
    assert context["job"]["salary_currency"] == "EUR"
    assert context["job"]["description"].startswith("Requirements")
    assert context["job"]["requirements"] == ["3+ years of experience"]
    assert context["job"]["responsibilities"] == ["Plan and run projects"]
    assert context["source"]["source"] == "greenhouse"
    assert context["source"]["was_preferred"] is True
    assert body["source_is_active"] is True

    rows = db_session.scalars(select(Application)).all()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.id) == body["id"]
    assert row.status is ApplicationStatus.PREPARING
    assert row.user_id == user_by_email(db_session).id
    assert row.job_id == job.id
    assert count(db_session, ApplicationContextSnapshot) == 1


def test_start_with_preparation_fields(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))

    body = started(client, auth, job, FULL_PREPARATION)

    assert body["salary_expectation"] == "78000.00"
    assert body["salary_currency"] == "EUR"
    assert body["mention_salary_in_letter"] is True
    assert body["available_from"] == "2027-01-01"
    assert body["notice_period"] == {"value": 3, "unit": "months"}
    assert body["work_authorization"] == "authorized"
    assert body["application_language"] == "de"
    assert body["notes"] == "Referral from Alex. Ask about the remote policy."
    # The user's own wording is not part of the context an employer-facing
    # document is generated from.
    assert "notes" not in str(body["context"]) and "Referral" not in str(body["context"])


def test_an_empty_body_and_no_body_are_both_fine(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    owner = user_by_email(db_session)
    first, second = make_job(db_session, owner), make_job(db_session, owner)
    assert start(client, auth, first, None).status_code == 201
    assert start(client, auth, second, {}).status_code == 201


def test_only_an_explicit_start_creates_an_application(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))

    assert client.get(f"{API}/jobs", headers=auth).status_code == 200
    assert client.get(f"{API}/jobs/{job.id}", headers=auth).status_code == 200
    assert client.put(f"{API}/jobs/{job.id}/save", headers=auth).status_code == 200
    assert client.get(f"{API}/jobs/{job.id}/match", headers=auth).status_code == 200
    assert client.post(f"{API}/jobs/{job.id}/match", headers=auth).status_code == 200
    assert client.post(f"{API}/jobs/match", headers=auth, json={}).status_code == 200
    assert client.get(f"{API}/applications", headers=auth).json()["total"] == 0

    assert count(db_session, Application) == 0
    assert count(db_session, ApplicationContextSnapshot) == 0


def test_jobs_report_your_application(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    owner = user_by_email(db_session)
    with_application = make_job(db_session, owner)
    without = make_job(db_session, owner)
    assert (
        client.get(f"{API}/jobs/{with_application.id}", headers=auth).json()["application"] is None
    )

    application = started(client, auth, with_application)

    expected = {"id": application["id"], "status": "PREPARING"}
    detail = client.get(f"{API}/jobs/{with_application.id}", headers=auth).json()
    assert detail["application"] == expected
    items = {i["id"]: i for i in client.get(f"{API}/jobs", headers=auth).json()["items"]}
    assert items[str(with_application.id)]["application"] == expected
    assert items[str(without.id)]["application"] is None
    # Saving a job returns the same detail, application included.
    saved = client.put(f"{API}/jobs/{with_application.id}/save", headers=auth).json()
    assert saved["application"] == expected


# --- One application per UserJob ----------------------------------------------------------------


def test_starting_twice_returns_the_existing_application_unchanged(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    first = start(client, auth, job, {"application_language": "en"})
    second = start(client, auth, job, {"application_language": "de", "notes": "ignored"})

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["id"] == first.json()["id"]
    # Starting never changes an existing application.
    assert second.json()["application_language"] == "en"
    assert second.json()["notes"] is None
    assert second.json() == get(client, auth, first.json()["id"])
    assert count(db_session, Application) == 1
    assert count(db_session, ApplicationContextSnapshot) == 1


def test_repeated_rapid_starts_create_one_application(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    responses = [start(client, auth, job) for _ in range(5)]

    assert [r.status_code for r in responses] == [201, 200, 200, 200, 200]
    assert len({r.json()["id"] for r in responses}) == 1
    assert count(db_session, Application) == 1


def test_concurrent_starts_cannot_create_duplicates(db_session: Session) -> None:
    owner = User(email="jane@example.com", password_hash="x", first_name="Jane", last_name="Doe")
    db_session.add(owner)
    db_session.flush()
    job = make_job(db_session, owner)
    owner_id, job_id = owner.id, job.id
    results: list[tuple[uuid.UUID, bool]] = []

    def attempt() -> None:
        with TestSessionLocal() as session:
            me = session.get(User, owner_id)
            assert me is not None
            result = service.start_application(session, me, job_id)
            results.append((result.application.id, result.created))

    errors = run_concurrently(*[attempt] * 8)

    assert errors == []
    assert len(results) == 8
    assert len({application_id for application_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1  # exactly one winner
    db_session.expire_all()
    assert count(db_session, Application) == 1
    assert count(db_session, ApplicationContextSnapshot) == 1


def test_the_database_allows_one_application_per_user_job(db_session: Session) -> None:
    owner = User(email="jane@example.com", password_hash="x", first_name="Jane", last_name="Doe")
    db_session.add(owner)
    db_session.flush()
    job = make_job(db_session, owner)
    user_job = db_session.scalar(select(UserJob).where(UserJob.job_id == job.id))
    assert user_job is not None
    db_session.add(Application(user_id=owner.id, user_job_id=user_job.id, job_id=job.id))
    db_session.commit()

    db_session.add(Application(user_id=owner.id, user_job_id=user_job.id, job_id=job.id))
    with pytest.raises(IntegrityError, match="uq_applications_user_job_id"):
        db_session.commit()


def test_users_with_the_same_global_job_have_separate_applications(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    db_session.add(
        UserJob(user_id=user_by_email(db_session, "mallory@example.com").id, job_id=job.id)
    )
    db_session.commit()

    mine = started(client, auth, job, {"notes": "mine"})
    theirs = started(client, other_auth, job, {"notes": "theirs"})

    assert mine["id"] != theirs["id"]
    assert get(client, auth, mine["id"])["notes"] == "mine"
    assert get(client, other_auth, theirs["id"])["notes"] == "theirs"
    assert count(db_session, Application) == 2


# --- Ownership -------------------------------------------------------------------------------


def test_another_user_cannot_read_an_application(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job, FULL_PREPARATION)

    foreign = client.get(f"{API}/applications/{application['id']}", headers=other_auth)
    missing = client.get(f"{API}/applications/{uuid.uuid4()}", headers=other_auth)

    # Exactly like an application that doesn't exist: nothing to infer from it.
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "NOT_FOUND"
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
    assert "Referral" not in foreign.text and "78000" not in foreign.text


def test_another_user_cannot_modify_an_application(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job, FULL_PREPARATION)

    response = patch(client, other_auth, application["id"], {"notes": "hijacked"})

    assert response.status_code == 404
    assert get(client, auth, application["id"])["notes"] == FULL_PREPARATION["notes"]


def test_another_user_cannot_start_an_application_for_your_job(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    unknown = start(client, other_auth, str(uuid.uuid4()))

    foreign = start(client, other_auth, job)

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert count(db_session, Application) == 0


def test_your_application_is_not_visible_through_the_job_endpoints_of_another_user(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    db_session.add(
        UserJob(user_id=user_by_email(db_session, "mallory@example.com").id, job_id=job.id)
    )
    db_session.commit()
    application = started(client, auth, job)

    mine = client.get(f"{API}/jobs/{job.id}", headers=auth).json()
    theirs = client.get(f"{API}/jobs/{job.id}", headers=other_auth).json()

    assert mine["application"] == {"id": application["id"], "status": "PREPARING"}
    assert theirs["application"] is None  # the job is shared; the application is not
    assert client.get(f"{API}/jobs", headers=other_auth).json()["items"][0]["application"] is None
    assert application["id"] not in client.get(f"{API}/jobs/{job.id}", headers=other_auth).text


def test_the_list_only_contains_your_own_applications(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    mine = make_job(db_session, user_by_email(db_session), title="Mine")
    theirs = make_job(db_session, user_by_email(db_session, "mallory@example.com"), title="Theirs")
    started(client, auth, mine)
    started(client, other_auth, theirs)

    listed = client.get(f"{API}/applications", headers=auth).json()

    assert listed["total"] == 1
    assert [item["title"] for item in listed["items"]] == ["Mine"]
    assert "Theirs" not in str(listed)


def test_every_endpoint_requires_authentication(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)

    assert client.post(f"{API}/jobs/{job.id}/application").status_code == 401
    assert client.get(f"{API}/applications").status_code == 401
    assert client.get(f"{API}/applications/{application['id']}").status_code == 401
    assert client.patch(f"{API}/applications/{application['id']}", json={}).status_code == 401


def test_the_database_rejects_an_application_for_another_users_user_job(
    db_session: Session,
) -> None:
    alice = User(email="alice@example.com", password_hash="x", first_name="A", last_name="A")
    mallory = User(email="mallory@example.com", password_hash="x", first_name="M", last_name="M")
    db_session.add_all([alice, mallory])
    db_session.flush()
    job = make_job(db_session, alice)
    alices = db_session.scalar(select(UserJob).where(UserJob.job_id == job.id))
    assert alices is not None

    db_session.add(Application(user_id=mallory.id, user_job_id=alices.id, job_id=job.id))
    with pytest.raises(IntegrityError, match="fk_applications_user_job"):
        db_session.commit()


# --- Updating preparation data ---------------------------------------------------------------


def test_each_preparation_field_can_be_updated(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)
    application_id = application["id"]

    salary = patch(
        client, auth, application_id, {"salary_expectation": 78000, "salary_currency": "eur"}
    ).json()
    assert (salary["salary_expectation"], salary["salary_currency"]) == ("78000.00", "EUR")

    mention = patch(client, auth, application_id, {"mention_salary_in_letter": True}).json()
    assert mention["mention_salary_in_letter"] is True
    mention = patch(client, auth, application_id, {"mention_salary_in_letter": False}).json()
    assert mention["mention_salary_in_letter"] is False

    availability = patch(client, auth, application_id, {"available_from": "2027-01-01"}).json()
    assert availability["available_from"] == "2027-01-01"

    notice = patch(
        client, auth, application_id, {"notice_period": {"value": 3, "unit": "months"}}
    ).json()
    assert notice["notice_period"] == {"value": 3, "unit": "months"}

    for authorization in ("authorized", "requires_sponsorship", "unknown"):
        updated = patch(client, auth, application_id, {"work_authorization": authorization}).json()
        assert updated["work_authorization"] == authorization

    for language in ("de", "en"):
        updated = patch(client, auth, application_id, {"application_language": language}).json()
        assert updated["application_language"] == language

    notes = patch(client, auth, application_id, {"notes": "Mention the energy projects."}).json()
    assert notes["notes"] == "Mention the energy projects."

    row = db_session.scalar(select(Application))
    db_session.refresh(row)
    assert row is not None
    assert row.salary_expectation is not None and str(row.salary_expectation) == "78000.00"
    assert (row.notice_period_value, row.notice_period_unit.value) == (3, "months")  # type: ignore[union-attr]
    assert row.available_from == date(2027, 1, 1)
    # Status is not something the user edits in V0.7.
    assert row.status is ApplicationStatus.PREPARING


def test_updates_are_partial_and_null_clears_a_field(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job, FULL_PREPARATION)

    after = patch(client, auth, application["id"], {"notes": "Only the note changes."}).json()
    assert after["notes"] == "Only the note changes."
    assert after["salary_expectation"] == "78000.00"  # omitted fields are untouched
    assert after["notice_period"] == {"value": 3, "unit": "months"}

    cleared = patch(
        client,
        auth,
        application["id"],
        {"notice_period": None, "notes": None, "mention_salary_in_letter": None},
    ).json()
    assert cleared["notice_period"] is None
    assert cleared["notes"] is None
    assert cleared["mention_salary_in_letter"] is None
    assert cleared["salary_expectation"] == "78000.00"
    row = db_session.scalar(select(Application))
    db_session.refresh(row)
    assert row is not None and row.notice_period_value is None and row.notice_period_unit is None


def test_an_empty_update_changes_nothing(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job, FULL_PREPARATION)

    response = patch(client, auth, application["id"], {})

    assert response.status_code == 200
    assert response.json() == application


def test_values_are_normalized(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)

    body = patch(
        client,
        auth,
        application["id"],
        {
            "salary_expectation": "78000.5",
            "salary_currency": " chf ",
            "application_language": "DE",
            "notes": "  padded  ",
        },
    ).json()
    assert (body["salary_expectation"], body["salary_currency"]) == ("78000.50", "CHF")
    assert body["application_language"] == "de"
    assert body["notes"] == "padded"
    assert patch(client, auth, application["id"], {"notes": "   "}).json()["notes"] is None


def test_availability_may_be_today_or_in_the_past_and_notice_may_be_zero(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)
    today = datetime.now(UTC).date()

    for day in (today, today - timedelta(days=30)):
        response = patch(client, auth, application["id"], {"available_from": day.isoformat()})
        assert response.status_code == 200, response.text
        assert response.json()["available_from"] == day.isoformat()
    immediate = patch(
        client, auth, application["id"], {"notice_period": {"value": 0, "unit": "days"}}
    )
    assert immediate.json()["notice_period"] == {"value": 0, "unit": "days"}


def test_updating_an_application_never_touches_profile_preferences_job_or_match(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    assert (
        client.put(
            f"{API}/profile",
            json={
                "current_job_title": "Project Manager",
                "years_of_experience": 6,
                "location": "Köln",
            },
            headers=auth,
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"{API}/preferences",
            json={"minimum_salary": 70000, "desired_salary": 80000, "salary_currency": "EUR"},
            headers=auth,
        ).status_code
        == 200
    )
    job = make_job(db_session, user_by_email(db_session))
    assert client.post(f"{API}/jobs/{job.id}/match", headers=auth).status_code == 200

    def state() -> dict[str, Any]:
        db_session.expire_all()
        return {
            "preferences": db_session.execute(
                select(
                    JobPreferences.minimum_salary,
                    JobPreferences.desired_salary,
                    JobPreferences.salary_currency,
                    JobPreferences.updated_at,
                )
            ).all(),
            "profile": db_session.execute(
                select(UserProfile.location, UserProfile.updated_at)
            ).all(),
            "professional": db_session.execute(
                select(ProfessionalProfile.current_job_title, ProfessionalProfile.updated_at)
            ).all(),
            "job": db_session.execute(
                select(Job.title, Job.salary_min, Job.salary_max, Job.description, Job.updated_at)
            ).all(),
            "postings": db_session.execute(
                select(JobSourcePosting.title, JobSourcePosting.updated_at)
            ).all(),
            "user_jobs": db_session.execute(select(UserJob.saved_at, UserJob.updated_at)).all(),
            "matches": db_session.execute(
                select(JobMatch.id, JobMatch.score, JobMatch.is_current)
            ).all(),
        }

    before = state()
    application = started(client, auth, job)
    patch(
        client,
        auth,
        application["id"],
        {**FULL_PREPARATION, "salary_expectation": 99000, "salary_currency": "CHF"},
    )
    client.get(f"{API}/applications/{application['id']}", headers=auth)
    client.get(f"{API}/applications", headers=auth)

    assert state() == before


def test_the_status_and_other_fields_cannot_be_set_by_the_client(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    other = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)
    posting = make_posting(db_session, other)
    db_session.commit()

    for body in (
        {"status": "submitted"},
        {"status": "preparing"},
        {"user_id": str(uuid.uuid4())},
        {"job_id": str(other.id)},
        {"user_job_id": str(uuid.uuid4())},
        {"source_posting_id": str(posting.id)},
        {"context": {"job": {"title": "Changed"}}},
        {"created_at": "2020-01-01T00:00:00Z"},
        {"unknown_field": 1},
    ):
        response = patch(client, auth, application["id"], body)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    # Creating can't set them either.
    for body in (
        {"status": "submitted"},
        {"user_id": str(uuid.uuid4())},
        {"job_id": str(other.id)},
    ):
        assert start(client, auth, other, body).status_code == 422
    assert get(client, auth, application["id"]) == application
    assert count(db_session, Application) == 1


# --- Validation ------------------------------------------------------------------------------

INVALID_PREPARATION = [
    # salary
    {"salary_expectation": -1, "salary_currency": "EUR"},
    {"salary_expectation": 0, "salary_currency": "EUR"},
    {"salary_expectation": "abc", "salary_currency": "EUR"},
    {"salary_expectation": 10_000_001, "salary_currency": "EUR"},
    {"salary_expectation": "78000.123", "salary_currency": "EUR"},
    {"salary_expectation": [78000]},
    {"salary_currency": "EURO"},
    {"salary_currency": "E1R"},
    {"salary_currency": "12"},
    # dates
    {"available_from": "not-a-date"},
    {"available_from": "2027-13-45"},
    {"available_from": "1999-12-31"},
    {"available_from": "0001-01-01"},
    {"available_from": "2999-01-01"},
    # notice period
    {"notice_period": {"value": -1, "unit": "months"}},
    {"notice_period": {"value": 3, "unit": "fortnights"}},
    {"notice_period": {"value": 25, "unit": "months"}},
    {"notice_period": {"value": 105, "unit": "weeks"}},
    {"notice_period": {"value": 731, "unit": "days"}},
    {"notice_period": {"value": 1.5, "unit": "months"}},
    {"notice_period": {"value": 3}},
    {"notice_period": {"unit": "months"}},
    {"notice_period": {"value": 3, "unit": "months", "extra": 1}},
    {"notice_period": 3},
    # enums and flags
    {"work_authorization": "citizen"},
    {"work_authorization": "AUTHORIZED "},
    {"application_language": "fr"},
    {"application_language": "german"},
    {"application_language": 7},
    {"mention_salary_in_letter": "maybe"},
    # notes
    {"notes": "x" * 10_001},
    {"notes": ["a"]},
]


@pytest.mark.parametrize("body", INVALID_PREPARATION)
def test_invalid_updates_are_rejected_and_change_nothing(
    client: TestClient, auth: dict[str, str], db_session: Session, body: dict[str, Any]
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(
        client, auth, job, {"salary_expectation": 50000, "salary_currency": "EUR"}
    )

    response = patch(client, auth, application["id"], body)

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"]
    assert get(client, auth, application["id"]) == application


@pytest.mark.parametrize("body", INVALID_PREPARATION[:14])
def test_invalid_values_are_rejected_when_starting_too(
    client: TestClient, auth: dict[str, str], db_session: Session, body: dict[str, Any]
) -> None:
    job = make_job(db_session, user_by_email(db_session))

    assert start(client, auth, job, body).status_code == 422
    assert count(db_session, Application) == 0
    assert count(db_session, ApplicationContextSnapshot) == 0


def test_a_salary_needs_its_currency(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)

    alone = patch(client, auth, application["id"], {"salary_expectation": 78000})
    assert alone.status_code == 422
    assert alone.json()["error"]["details"]["field"] == "salary_currency"
    assert "currency" in alone.json()["error"]["message"]
    assert get(client, auth, application["id"])["salary_expectation"] is None  # nothing half-saved

    # With one already stored, later updates may send just the amount...
    assert (
        patch(
            client, auth, application["id"], {"salary_expectation": 78000, "salary_currency": "EUR"}
        ).status_code
        == 200
    )
    assert patch(client, auth, application["id"], {"salary_expectation": 80000}).status_code == 200
    # ...but the currency can't be removed from under it,
    assert patch(client, auth, application["id"], {"salary_currency": None}).status_code == 422
    # unless the amount goes too.
    cleared = patch(
        client, auth, application["id"], {"salary_expectation": None, "salary_currency": None}
    )
    assert cleared.status_code == 200
    assert (
        start(
            client, auth, make_job(db_session, user_by_email(db_session)), {"salary_expectation": 5}
        ).status_code
        == 422
    )


def test_pagination_limits_are_validated(client: TestClient, auth: dict[str, str]) -> None:
    for query in ("page=0", "page_size=0", "page_size=101", "page=abc"):
        assert client.get(f"{API}/applications?{query}", headers=auth).status_code == 422


def test_malformed_ids_are_rejected(client: TestClient, auth: dict[str, str]) -> None:
    assert client.get(f"{API}/applications/not-a-uuid", headers=auth).status_code == 422
    assert patch(client, auth, "not-a-uuid", {}).status_code == 422
    assert start(client, auth, "not-a-uuid").status_code == 422


# --- Resume reference ------------------------------------------------------------------------


def test_without_a_resume_the_application_has_none(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))

    application = started(client, auth, job)

    assert application["resume"] is None
    progress = {item["key"]: item["complete"] for item in application["progress"]["items"]}
    assert progress["resume"] is False


def test_a_new_application_builds_on_the_confirmed_resume(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    confirmed = confirmed_resume(client, auth, "confirmed.pdf")
    upload_resume(client, auth, "newer-unreviewed.pdf")
    job = make_job(db_session, user_by_email(db_session))

    application = started(client, auth, job)

    # The same resume the match uses: the confirmed one, not merely the latest upload.
    assert application["resume"]["id"] == confirmed["id"]
    assert application["resume"]["version_number"] == 1
    assert application["resume"]["file_name"] == "confirmed.pdf"
    assert application["resume"]["is_confirmed"] is True
    row = db_session.scalar(select(Application))
    assert row is not None and str(row.resume_id) == confirmed["id"]


def test_without_a_confirmed_resume_the_latest_upload_is_used(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    upload_resume(client, auth, "first.pdf")
    latest = upload_resume(client, auth, "second.pdf")
    job = make_job(db_session, user_by_email(db_session))

    application = started(client, auth, job)

    assert application["resume"]["id"] == latest["id"]
    assert application["resume"]["version_number"] == 2
    assert application["resume"]["is_confirmed"] is False


def test_a_resume_can_be_chosen_when_starting(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    first = upload_resume(client, auth, "first.pdf")
    upload_resume(client, auth, "second.pdf")
    job = make_job(db_session, user_by_email(db_session))

    application = started(client, auth, job, {"resume_id": first["id"]})

    assert application["resume"]["id"] == first["id"]
    assert application["resume"]["version_number"] == 1


def test_a_later_upload_does_not_change_the_applications_resume(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    original = confirmed_resume(client, auth, "original.pdf")
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)

    newer = upload_resume(client, auth, "newer.pdf")
    confirmed_resume(client, auth, "newest.pdf")
    patch(client, auth, application["id"], {"notes": "unrelated edit"})

    refreshed = get(client, auth, application["id"])
    assert refreshed["resume"]["id"] == original["id"]
    assert refreshed["resume"]["file_name"] == "original.pdf"
    assert refreshed["resume"]["id"] != newer["id"]
    db_session.expire_all()
    row = db_session.scalar(select(Application))
    assert row is not None and str(row.resume_id) == original["id"]


def test_the_resume_can_be_replaced_explicitly(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    first = upload_resume(client, auth, "first.pdf")
    second = upload_resume(client, auth, "second.pdf")
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job, {"resume_id": first["id"]})

    changed = patch(client, auth, application["id"], {"resume_id": second["id"]})

    assert changed.status_code == 200
    assert changed.json()["resume"]["id"] == second["id"]
    # Choosing the one it already has is a no-op.
    before = get(client, auth, application["id"])
    again = patch(client, auth, application["id"], {"resume_id": second["id"]})
    assert again.json() == before


def test_a_resume_must_be_the_users_own(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    mine = upload_resume(client, auth, "mine.pdf")
    theirs = upload_resume(client, other_auth, "theirs.pdf")
    job = make_job(db_session, user_by_email(db_session))

    foreign = start(client, auth, job, {"resume_id": theirs["id"]})
    unknown = start(client, auth, job, {"resume_id": str(uuid.uuid4())})
    assert foreign.status_code == unknown.status_code == 422
    assert foreign.json()["error"]["details"]["field"] == "resume_id"
    assert foreign.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert start(client, auth, job, {"resume_id": "not-a-uuid"}).status_code == 422
    assert count(db_session, Application) == 0

    application = started(client, auth, job, {"resume_id": mine["id"]})
    assert patch(client, auth, application["id"], {"resume_id": theirs["id"]}).status_code == 422
    assert (
        patch(client, auth, application["id"], {"resume_id": str(uuid.uuid4())}).status_code == 422
    )
    assert get(client, auth, application["id"])["resume"]["id"] == mine["id"]


def test_a_resume_can_be_replaced_but_not_removed(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    upload_resume(client, auth)
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)

    response = patch(client, auth, application["id"], {"resume_id": None})

    assert response.status_code == 422
    assert get(client, auth, application["id"])["resume"] is not None


def test_deleting_the_resume_keeps_the_application(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    resume = upload_resume(client, auth)
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job, {"notes": "keep me"})
    assert application["resume"]["id"] == resume["id"]

    assert client.delete(f"{API}/resumes/{resume['id']}", headers=auth).status_code == 204

    refreshed = get(client, auth, application["id"])
    assert refreshed["resume"] is None
    assert refreshed["notes"] == "keep me"
    progress = {item["key"]: item["complete"] for item in refreshed["progress"]["items"]}
    assert progress["resume"] is False
    # And another one can be chosen.
    replacement = upload_resume(client, auth, "replacement.pdf")
    patched = patch(client, auth, application["id"], {"resume_id": replacement["id"]})
    assert patched.json()["resume"]["id"] == replacement["id"]
    assert db_session.scalar(select(func.count()).select_from(Resume)) == 1


# --- The context snapshot --------------------------------------------------------------------


def test_refreshing_the_job_never_changes_the_applications_context(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job, {"notes": "private"})
    snapshot = db_session.scalar(select(ApplicationContextSnapshot))
    assert snapshot is not None
    stored_document, stored_hash = dict(snapshot.document), snapshot.content_hash

    # The company edits the posting and discovery refreshes the canonical job.
    job.title = "Principal Project Manager"
    job.salary_min = Decimal(65000)
    job.salary_max = Decimal(75000)
    job.description = "A completely different text."
    job.requirements = ["Something else"]
    job.work_model = None
    job.canonical_url = "https://jobs.example.com/moved"
    job.company_name = "Renamed AG"
    db_session.commit()

    refreshed = get(client, auth, application["id"])
    assert refreshed["context"] == application["context"]
    assert refreshed["context"]["job"]["title"] == "Senior Project Manager"
    assert refreshed["context"]["job"]["salary_min"] == "70000.00"
    assert refreshed["context"]["job"]["work_model"] == "hybrid"
    assert refreshed["notes"] == "private"
    # The live job is what it is now; the application still points at it.
    live = client.get(f"{API}/jobs/{job.id}", headers=auth).json()
    assert live["title"] == "Principal Project Manager"
    assert refreshed["job_id"] == live["id"]
    db_session.expire_all()
    snapshot = db_session.scalar(select(ApplicationContextSnapshot))
    assert snapshot is not None
    assert snapshot.document == stored_document and snapshot.content_hash == stored_hash


def test_the_context_is_write_once(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    started(client, auth, job)
    snapshot = db_session.scalar(select(ApplicationContextSnapshot))
    assert snapshot is not None

    snapshot.document = {**snapshot.document, "origin": "migrated"}
    with pytest.raises(ImmutableRecordError):
        db_session.commit()
    db_session.rollback()

    snapshot = db_session.scalar(select(ApplicationContextSnapshot))
    assert snapshot is not None
    snapshot.content_hash = "0" * 64
    with pytest.raises(ImmutableRecordError):
        db_session.commit()


def test_the_context_checksum_matches_the_stored_document(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session), sources=("greenhouse", "lever"))
    application = started(client, auth, job)

    snapshot = db_session.scalar(select(ApplicationContextSnapshot))
    assert snapshot is not None
    assert snapshot.content_hash == hash_document(snapshot.document)
    assert snapshot.content_hash == application["context"]["content_hash"]
    assert (
        snapshot.schema_version == snapshot.document["schema_version"] == "application_context_v1"
    )
    # Contents only describe the job and where it was found.
    assert set(snapshot.document) == {"schema_version", "origin", "captured_at", "job", "source"}


def test_the_context_survives_its_posting_and_job_disappearing_from_the_source(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)
    posting = db_session.scalar(select(JobSourcePosting))
    assert posting is not None

    posting.is_active = False
    db_session.commit()
    assert get(client, auth, application["id"])["source_is_active"] is False

    db_session.delete(posting)
    job.preferred_source_posting_id = None
    db_session.commit()
    refreshed = get(client, auth, application["id"])
    assert refreshed["source_is_active"] is None
    assert refreshed["context"] == application["context"]
    assert refreshed["context"]["source"]["original_url"].startswith(
        "https://job-boards.greenhouse.io/"
    )


# --- Source posting --------------------------------------------------------------------------


def test_the_preferred_posting_is_the_default_target(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session), sources=("lever", "greenhouse"))

    application = started(client, auth, job)

    source = application["context"]["source"]
    assert source["source"] == "lever"
    assert source["was_preferred"] is True
    row = db_session.scalar(select(Application))
    assert row is not None and row.source_posting_id == job.preferred_source_posting_id


def test_the_original_posting_and_application_urls_are_preserved(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session), sources=("lever",))
    posting = db_session.scalar(select(JobSourcePosting))
    assert posting is not None

    source = started(client, auth, job)["context"]["source"]

    assert source["posting_id"] == str(posting.id)
    assert source["source_job_id"] == posting.source_job_id
    assert source["source_company_key"] == "acme"
    # As the source returned it, with its tracking parameter...
    assert source["original_url"] == posting.source_url
    assert "utm_source=feed" in source["original_url"]
    # ...and normalized.
    assert source["canonical_url"] == posting.canonical_source_url
    # Lever states where to apply; that is the application target.
    assert source["apply_url"] == posting.apply_url
    assert source["application_url"] == posting.apply_url
    assert source["application_url"] != source["canonical_url"]


@pytest.mark.parametrize(
    ("source", "hosted"), [("greenhouse", True), ("lever", True), ("other", False)]
)
def test_where_applying_happens_follows_the_source(
    client: TestClient, auth: dict[str, str], db_session: Session, source: str, hosted: bool
) -> None:
    job = make_job(db_session, user_by_email(db_session), sources=(source,))
    posting = db_session.scalar(select(JobSourcePosting))
    assert posting is not None

    context = started(client, auth, job)["context"]["source"]

    if posting.apply_url:
        assert context["application_url"] == posting.apply_url
    elif hosted:
        # The posting page itself hosts the form.
        assert context["application_url"] == posting.canonical_source_url
    else:
        # Never assumed to be the job's canonical URL.
        assert context["application_url"] is None


def test_another_posting_of_the_job_can_be_chosen(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session), sources=("greenhouse", "lever"))
    lever = db_session.scalar(select(JobSourcePosting).where(JobSourcePosting.source == "lever"))
    assert lever is not None

    application = started(client, auth, job, {"source_posting_id": str(lever.id)})

    source = application["context"]["source"]
    assert source["source"] == "lever"
    assert source["was_preferred"] is False
    assert source["posting_id"] == str(lever.id)
    # The job's canonical content is unchanged by the choice.
    assert application["context"]["job"]["canonical_url"] == job.canonical_url
    row = db_session.scalar(select(Application))
    assert row is not None and row.source_posting_id == lever.id


def test_a_posting_of_another_job_is_rejected(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    owner = user_by_email(db_session)
    job = make_job(db_session, owner)
    other_job = make_job(db_session, owner)
    foreign = db_session.scalar(
        select(JobSourcePosting).where(JobSourcePosting.job_id == other_job.id)
    )
    assert foreign is not None

    for posting_id in (str(foreign.id), str(uuid.uuid4())):
        response = start(client, auth, job, {"source_posting_id": posting_id})
        assert response.status_code == 422
        assert response.json()["error"]["details"]["field"] == "source_posting_id"
    assert start(client, auth, job, {"source_posting_id": "nope"}).status_code == 422
    assert count(db_session, Application) == 0


def test_a_job_without_postings_still_gets_a_context(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session), sources=())

    source = started(client, auth, job)["context"]["source"]

    assert source["source"] == "unknown"
    assert source["posting_id"] is None
    assert source["original_url"] == job.canonical_url


# --- Match integration (read-only) -----------------------------------------------------------


def test_the_workspace_shows_the_current_match_without_recalculating_it(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    calculated = client.post(f"{API}/jobs/{job.id}/match", headers=auth).json()["result"]
    matches_before = db_session.execute(select(JobMatch.id, JobMatch.score)).all()

    application = started(client, auth, job)
    patch(client, auth, application["id"], {"notes": "edited"})
    fetched = get(client, auth, application["id"])

    match = fetched["match"]
    assert match is not None
    for key in ("overall_match", "job_match", "preference_match", "recommendation", "status"):
        assert match[key] == calculated[key]
    assert match == client.get(f"{API}/jobs/{job.id}", headers=auth).json()["match"]
    assert db_session.execute(select(JobMatch.id, JobMatch.score)).all() == matches_before


# --- Listing ---------------------------------------------------------------------------------


def test_applications_are_listed_newest_activity_first_with_pagination(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    owner = user_by_email(db_session)
    jobs = [make_job(db_session, owner, title=f"Role {i}") for i in range(3)]
    ids = [started(client, auth, job)["id"] for job in jobs]
    patch(client, auth, ids[0], {"notes": "touched last"})  # moves it to the front

    first = client.get(f"{API}/applications?page_size=2", headers=auth).json()
    second = client.get(f"{API}/applications?page_size=2&page=2", headers=auth).json()

    assert (first["total"], first["page"], first["page_size"], first["total_pages"]) == (3, 1, 2, 2)
    assert [item["id"] for item in first["items"]] == [ids[0], ids[2]]
    assert [item["id"] for item in second["items"]] == [ids[1]]
    item = first["items"][0]
    assert item["title"] == "Role 0"
    assert item["company_name"] == "Example GmbH"
    assert item["location"] == "Köln"
    assert item["source"] == "greenhouse"
    assert item["status"] == "PREPARING"
    assert item["job_id"] == str(jobs[0].id)
    assert item["original_url"].startswith("https://job-boards.greenhouse.io/")
    assert item["progress"]["total"] == 6
    assert "description" not in item and "notes" not in item


def test_an_empty_list(client: TestClient, auth: dict[str, str]) -> None:
    body = client.get(f"{API}/applications", headers=auth).json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20, "total_pages": 0}


# --- Preparation progress --------------------------------------------------------------------


def test_progress_follows_the_data(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    upload_resume(client, auth)
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)

    def progress(body: dict[str, Any]) -> tuple[int, int, set[str]]:
        p = body["progress"]
        return p["completed"], p["total"], {i["key"] for i in p["items"] if i["complete"]}

    assert progress(application) == (2, 6, {"job", "resume"})  # job + the resume found for it
    body = patch(client, auth, application["id"], {"application_language": "en"}).json()
    assert progress(body) == (3, 6, {"job", "resume", "language"})
    body = patch(
        client, auth, application["id"], {"salary_expectation": 70000, "salary_currency": "EUR"}
    ).json()
    assert progress(body)[2] == {"job", "resume", "language", "salary"}
    body = patch(client, auth, application["id"], {"available_from": "2027-01-01"}).json()
    body = patch(
        client, auth, application["id"], {"notice_period": {"value": 3, "unit": "months"}}
    ).json()
    assert progress(body) == (
        6,
        6,
        {"job", "resume", "language", "salary", "availability", "notice_period"},
    )
    # Work authorization is optional: shown, never counted.
    body = patch(client, auth, application["id"], {"work_authorization": "authorized"}).json()
    assert progress(body)[:2] == (6, 6)
    optional = [i for i in body["progress"]["items"] if not i["required"]]
    assert [(i["key"], i["complete"]) for i in optional] == [("work_authorization", True)]


# --- Deleting things around an application ---------------------------------------------------


def test_deleting_the_user_removes_their_applications_and_contexts(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    owner = user_by_email(db_session)
    job = make_job(db_session, owner)
    started(client, auth, job, FULL_PREPARATION)

    db_session.delete(owner)
    db_session.commit()

    assert count(db_session, Application) == 0
    assert count(db_session, ApplicationContextSnapshot) == 0
    assert db_session.get(Job, job.id) is not None  # jobs are global and stay


def test_deleting_a_job_removes_the_applications_for_it(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    kept = make_job(db_session, user_by_email(db_session))
    started(client, auth, job)
    started(client, auth, kept)

    db_session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job.id})
    db_session.commit()

    assert [a.job_id for a in db_session.scalars(select(Application))] == [kept.id]
    assert count(db_session, ApplicationContextSnapshot) == 1


def test_a_user_job_removed_through_the_orm_takes_its_application_along(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    started(client, auth, job)
    user_job = db_session.scalar(select(UserJob))
    assert user_job is not None

    db_session.delete(user_job)
    db_session.commit()

    assert count(db_session, Application) == 0
    assert count(db_session, ApplicationContextSnapshot) == 0


def test_a_later_status_is_readable_and_the_preparation_patch_cannot_change_it(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user_by_email(db_session))
    application = started(client, auth, job)
    db_session.execute(
        text("UPDATE applications SET status = 'APPLIED', applied_at = now() WHERE id = :id"),
        {"id": application["id"]},
    )
    db_session.commit()

    assert get(client, auth, application["id"])["status"] == "APPLIED"
    # ...and the preparation PATCH cannot move an application between statuses
    # (V0.12: that is `PATCH /applications/{id}/status`).
    assert patch(client, auth, application["id"], {"status": "PREPARING"}).status_code == 422
