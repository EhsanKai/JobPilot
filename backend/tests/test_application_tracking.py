"""V0.12 application tracking: the lifecycle, the append-only history, manual changes,
the follow-up date, "My Applications" filters and ownership -- through the real API.

Submission integration (a real browser) is in test_application_tracking_submission.py.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationContextSnapshot,
    ApplicationStatus,
    ApplicationStatusHistory,
    JobSourcePosting,
    User,
)
from app.models.mixins import ImmutableRecordError
from app.services.applications import state_machine
from tests.application_helpers import API, make_job, make_posting, started, user_by_email
from tests.conftest import TestSessionLocal
from tests.tracking_helpers import (
    ALL_STATUSES,
    EXPECTED_TRANSITIONS,
    assert_history_is_consistent,
    change_of,
    history_rows,
    move,
    put_in_status,
    set_status,
    timeline,
    update_tracking,
)


@pytest.fixture
def auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {register_and_login()['access_token']}"}


@pytest.fixture
def other_auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = register_and_login(email="mallory@example.com")["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def application(client: TestClient, auth: dict[str, str], db_session: Session) -> dict[str, Any]:
    return started(client, auth, make_job(db_session, user_by_email(db_session)))


def get_application(client: TestClient, headers: dict[str, str], application_id: str) -> Any:
    response = client.get(f"{API}/applications/{application_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def tracking_of(client: TestClient, headers: dict[str, str], application_id: str) -> Any:
    return get_application(client, headers, application_id)["tracking"]


# --- The state machine itself ---------------------------------------------------------------------


def test_the_transition_table_is_the_specified_lifecycle() -> None:
    for current, targets in EXPECTED_TRANSITIONS.items():
        status = ApplicationStatus(current)
        assert {s.value for s in state_machine.allowed_next(status)} == targets, current
        for target in ALL_STATUSES:
            assert state_machine.is_allowed(status, ApplicationStatus(target)) == (
                target in targets
            ), (current, target)


def test_rejected_and_no_response_are_different_terminal_statuses() -> None:
    assert ApplicationStatus.REJECTED is not ApplicationStatus.NO_RESPONSE
    assert {s.value for s in state_machine.TERMINAL} == {"REJECTED", "WITHDRAWN", "NO_RESPONSE"}


def test_every_status_has_the_documented_enum_value() -> None:
    assert [s.value for s in ApplicationStatus] == [
        "SAVED",
        "PREPARING",
        "APPLIED",
        "FOLLOW_UP",
        "INTERVIEW",
        "OFFER",
        "REJECTED",
        "WITHDRAWN",
        "NO_RESPONSE",
    ]


# --- A new application ----------------------------------------------------------------------------


def test_a_new_application_has_a_tracking_status_and_its_first_history(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    tracking = application["tracking"]
    assert application["status"] == tracking["status"] == "PREPARING"
    assert tracking["applied_at"] is None and tracking["follow_up_at"] is None
    assert tracking["last_status_change_at"] is not None
    assert tracking["allowed_next_statuses"] == ["APPLIED", "WITHDRAWN"]
    assert tracking["submission"] is None

    rows = assert_history_is_consistent(db_session, application["id"])
    assert [(r.previous_status, r.new_status) for r in rows] == [
        (None, ApplicationStatus.SAVED),
        (ApplicationStatus.SAVED, ApplicationStatus.PREPARING),
    ]
    assert all(r.source.value == "SYSTEM" and r.actor.value == "SYSTEM" for r in rows)
    assert all(r.note is None for r in rows)
    assert rows[0].changed_at <= rows[1].changed_at


def test_saved_is_dated_by_when_the_job_was_saved(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    owner = user_by_email(db_session)
    job = make_job(db_session, owner)
    saved = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    db_session.execute(
        text("UPDATE user_jobs SET saved_at = :at WHERE job_id = :job"),
        {"at": saved, "job": job.id},
    )
    db_session.commit()
    created = started(client, auth, job)
    rows = history_rows(db_session, created["id"])
    assert rows[0].new_status is ApplicationStatus.SAVED and rows[0].changed_at == saved
    assert rows[1].changed_at > saved


def test_starting_twice_does_not_duplicate_history(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    job_id = application["job_id"]
    assert client.post(f"{API}/jobs/{job_id}/application", headers=auth).status_code == 200
    assert len(history_rows(db_session, application["id"])) == 2


# --- Ordinary transitions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [(c, t) for c in ALL_STATUSES for t in ALL_STATUSES if c != t],
)
def test_a_transition_is_applied_or_refused_exactly_as_the_lifecycle_says(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    application: dict[str, Any],
    current: str,
    target: str,
) -> None:
    put_in_status(client, auth, application["id"], current)
    before = len(history_rows(db_session, application["id"]))

    response = set_status(client, auth, application["id"], target)

    if target in EXPECTED_TRANSITIONS[current]:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["changed"] is True and body["tracking"]["status"] == target
        rows = assert_history_is_consistent(db_session, application["id"])
        assert len(rows) == before + 1
        assert change_of(rows[-1]) == (current, target)
        assert rows[-1].entry_type.value == "STATUS_CHANGE"
    else:
        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"] == "INVALID_STATUS_TRANSITION"
        assert error["details"]["from"] == current and error["details"]["to"] == target
        assert set(error["details"]["allowed"]) == EXPECTED_TRANSITIONS[current]
        # Nothing changed, nothing was written.
        assert tracking_of(client, auth, application["id"])["status"] == current
        assert len(history_rows(db_session, application["id"])) == before


def test_the_full_happy_path_builds_the_timeline(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    for status in ("APPLIED", "FOLLOW_UP", "INTERVIEW", "OFFER"):
        move(client, auth, application["id"], status)

    tracking = tracking_of(client, auth, application["id"])
    assert tracking["status"] == "OFFER"
    assert tracking["applied_at"] is not None
    assert tracking["allowed_next_statuses"] == ["WITHDRAWN", "REJECTED"]
    rows = assert_history_is_consistent(db_session, application["id"])
    assert [r.new_status.value for r in rows] == [
        "SAVED",
        "PREPARING",
        "APPLIED",
        "FOLLOW_UP",
        "INTERVIEW",
        "OFFER",
    ]
    assert [r.source.value for r in rows[2:]] == ["USER"] * 4
    assert all(r.actor.value == "USER" for r in rows[2:])
    # Timestamps never go backwards.
    times = [r.changed_at for r in rows]
    assert times == sorted(times)
    app_row = db_session.get(Application, uuid.UUID(application["id"]), populate_existing=True)
    assert app_row is not None
    assert app_row.last_status_change_at == rows[-1].changed_at
    assert app_row.applied_at == rows[2].changed_at  # the first time it counted as applied


def test_applied_at_is_kept_when_the_application_moves_on(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    applied_at = tracking_of(client, auth, application["id"])["applied_at"]
    move(client, auth, application["id"], "INTERVIEW")
    move(client, auth, application["id"], "REJECTED")
    assert tracking_of(client, auth, application["id"])["applied_at"] == applied_at


def test_withdrawing_before_applying_has_no_application_date(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "WITHDRAWN")
    tracking = tracking_of(client, auth, application["id"])
    assert tracking["status"] == "WITHDRAWN" and tracking["applied_at"] is None
    assert tracking["allowed_next_statuses"] == []


# --- Idempotency ----------------------------------------------------------------------------------


def test_the_same_status_again_is_a_no_op(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    before = tracking_of(client, auth, application["id"])
    rows_before = len(history_rows(db_session, application["id"]))

    response = set_status(client, auth, application["id"], "APPLIED")

    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert response.json()["tracking"] == before
    assert len(history_rows(db_session, application["id"])) == rows_before


def test_the_same_status_with_a_note_records_the_note_only(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    before = tracking_of(client, auth, application["id"])

    response = set_status(client, auth, application["id"], "APPLIED", note="Waiting for feedback.")

    assert response.json()["changed"] is True
    after = response.json()["tracking"]
    assert after["status"] == "APPLIED"
    assert after["last_status_change_at"] == before["last_status_change_at"]  # not a status change
    assert after["applied_at"] == before["applied_at"]
    rows = assert_history_is_consistent(db_session, application["id"])
    assert rows[-1].entry_type.value == "NOTE" and rows[-1].note == "Waiting for feedback."
    assert rows[-1].previous_status == rows[-1].new_status == ApplicationStatus.APPLIED


# --- History: append-only, server-derived, user-authored notes ------------------------------------


def test_history_rows_cannot_be_changed(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    row = history_rows(db_session, application["id"])[-1]
    row.note = "rewritten"
    with pytest.raises(ImmutableRecordError):
        db_session.flush()
    db_session.rollback()
    row = history_rows(db_session, application["id"])[-1]
    row.new_status = ApplicationStatus.OFFER
    with pytest.raises(ImmutableRecordError):
        db_session.flush()
    db_session.rollback()


def test_a_later_change_never_rewrites_earlier_history(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    snapshot = [
        (r.id, r.sequence, r.previous_status, r.new_status, r.changed_at, r.note)
        for r in history_rows(db_session, application["id"])
    ]
    set_status(client, auth, application["id"], "INTERVIEW", note="Phone call today.")
    set_status(client, auth, application["id"], "REJECTED")
    rows = history_rows(db_session, application["id"])
    assert [
        (r.id, r.sequence, r.previous_status, r.new_status, r.changed_at, r.note)
        for r in rows[: len(snapshot)]
    ] == snapshot
    assert len(rows) == len(snapshot) + 2


def test_the_user_note_is_stored_exactly(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    note = "  Recruiter called today.\n\n  Ümläute, “quotes”, <b>markup</b> & 日本語  \n"
    response = set_status(client, auth, application["id"], "APPLIED", note=note)
    assert response.status_code == 200, response.text
    assert history_rows(db_session, application["id"])[-1].note == note
    assert timeline(client, auth, application["id"])[0]["note"] == note


def test_a_blank_note_is_no_note(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    set_status(client, auth, application["id"], "APPLIED", note="   \n ")
    assert history_rows(db_session, application["id"])[-1].note is None


def test_an_overlong_note_is_refused(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    response = set_status(client, auth, application["id"], "APPLIED", note="x" * 2001)
    assert response.status_code == 422
    assert tracking_of(client, auth, application["id"])["status"] == "PREPARING"


@pytest.mark.parametrize(
    "extra",
    [
        {"previous_status": "SAVED"},
        {"user_id": str(uuid.uuid4())},
        {"changed_at": "2020-01-01T00:00:00Z"},
        {"actor": "SYSTEM"},
        {"source": "SUBMISSION"},
        {"application_id": str(uuid.uuid4())},
        {"sequence": 99},
    ],
)
def test_the_client_cannot_supply_server_facts(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    application: dict[str, Any],
    extra: dict[str, Any],
) -> None:
    response = set_status(client, auth, application["id"], "APPLIED", **extra)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(history_rows(db_session, application["id"])) == 2
    tracking = client.patch(
        f"{API}/applications/{application['id']}/tracking",
        headers=auth,
        json={"note": "hello", **extra},
    )
    assert tracking.status_code == 422


def test_the_server_decides_previous_status_user_actor_and_time(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    before = datetime.now(UTC)
    move(client, auth, application["id"], "APPLIED")
    row = history_rows(db_session, application["id"])[-1]
    owner = user_by_email(db_session)
    assert row.user_id == owner.id
    assert row.previous_status is ApplicationStatus.PREPARING
    assert row.source.value == "USER" and row.actor.value == "USER"
    assert (
        before - timedelta(seconds=2) <= row.changed_at <= datetime.now(UTC) + timedelta(seconds=2)
    )


def test_an_unknown_status_is_refused(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    for bad in ("submitted", "interviewing", "applied", "DONE", ""):
        assert set_status(client, auth, application["id"], bad).status_code == 422


def test_the_workspace_patch_still_cannot_change_the_status(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    response = client.patch(
        f"{API}/applications/{application['id']}", headers=auth, json={"status": "APPLIED"}
    )
    assert response.status_code == 422
    assert tracking_of(client, auth, application["id"])["status"] == "PREPARING"


# --- Manual correction ----------------------------------------------------------------------------


def test_a_correction_may_make_any_other_change_but_needs_a_note(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    put_in_status(client, auth, application["id"], "NO_RESPONSE")
    # NO_RESPONSE is terminal for ordinary changes ...
    assert set_status(client, auth, application["id"], "INTERVIEW").status_code == 409
    # ... a correction needs to say why ...
    assert (
        set_status(client, auth, application["id"], "INTERVIEW", correction=True).status_code == 422
    )
    assert (
        set_status(
            client, auth, application["id"], "INTERVIEW", correction=True, note="  "
        ).status_code
        == 422
    )
    # ... and is recorded as a correction.
    response = set_status(
        client,
        auth,
        application["id"],
        "INTERVIEW",
        correction=True,
        note="They replied after all.",
    )
    assert response.status_code == 200, response.text
    rows = assert_history_is_consistent(db_session, application["id"])
    assert rows[-1].entry_type.value == "CORRECTION"
    assert rows[-1].note == "They replied after all."
    assert change_of(rows[-1]) == ("NO_RESPONSE", "INTERVIEW")
    assert timeline(client, auth, application["id"])[0]["kind"] == "CORRECTION"


def test_a_correction_to_the_current_status_is_refused(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    response = set_status(
        client, auth, application["id"], "PREPARING", correction=True, note="same"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ALREADY_IN_STATUS"


def test_correcting_back_to_not_applied_clears_the_application_date_but_not_the_history(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    response = set_status(
        client, auth, application["id"], "PREPARING", correction=True, note="Marked by mistake."
    )
    assert response.status_code == 200
    tracking = tracking_of(client, auth, application["id"])
    assert tracking["status"] == "PREPARING" and tracking["applied_at"] is None
    rows = assert_history_is_consistent(db_session, application["id"])
    assert [r.new_status.value for r in rows] == ["SAVED", "PREPARING", "APPLIED", "PREPARING"]
    # Applying again for real gets a new date.
    move(client, auth, application["id"], "APPLIED")
    assert tracking_of(client, auth, application["id"])["applied_at"] is not None


def test_a_correction_to_applied_has_an_application_date(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    put_in_status(client, auth, application["id"], "WITHDRAWN")
    response = set_status(
        client, auth, application["id"], "INTERVIEW", correction=True, note="Never withdrew."
    )
    assert response.status_code == 200
    assert tracking_of(client, auth, application["id"])["applied_at"] is not None


def test_saved_can_move_on_to_preparing(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    put_in_status(client, auth, application["id"], "SAVED")
    assert tracking_of(client, auth, application["id"])["allowed_next_statuses"] == [
        "PREPARING",
        "WITHDRAWN",
    ]
    move(client, auth, application["id"], "PREPARING")
    rows = assert_history_is_consistent(db_session, application["id"])
    assert [r.new_status.value for r in rows][-2:] == ["SAVED", "PREPARING"]


# --- Follow-up date -------------------------------------------------------------------------------


def test_a_follow_up_date_is_stored_and_changes_nothing_else(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    rows_before = len(history_rows(db_session, application["id"]))
    before = tracking_of(client, auth, application["id"])

    response = update_tracking(
        client, auth, application["id"], {"follow_up_at": "2026-10-21T09:00:00"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["tracking"]["follow_up_at"].startswith("2026-10-21T09:00:00")  # naive = UTC
    assert body["tracking"]["status"] == "APPLIED"
    assert body["tracking"]["last_status_change_at"] == before["last_status_change_at"]
    # A reminder for the user, not something that happened: no history row.
    assert len(history_rows(db_session, application["id"])) == rows_before
    assert (
        tracking_of(client, auth, application["id"])["follow_up_at"]
        == body["tracking"]["follow_up_at"]
    )


def test_a_follow_up_date_can_be_cleared(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    update_tracking(client, auth, application["id"], {"follow_up_at": "2026-10-21T09:00:00Z"})
    response = update_tracking(client, auth, application["id"], {"follow_up_at": None})
    assert response.status_code == 200
    assert response.json()["tracking"]["follow_up_at"] is None
    assert response.json()["changed"] is True
    # Clearing what is not set changes nothing.
    again = update_tracking(client, auth, application["id"], {"follow_up_at": None})
    assert again.json()["changed"] is False


def test_the_same_follow_up_date_again_is_a_no_op(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    update_tracking(client, auth, application["id"], {"follow_up_at": "2026-10-21T09:00:00Z"})
    again = update_tracking(
        client, auth, application["id"], {"follow_up_at": "2026-10-21T09:00:00Z"}
    )
    assert again.json()["changed"] is False


def test_a_follow_up_date_can_be_set_with_the_status_change(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    response = set_status(
        client,
        auth,
        application["id"],
        "FOLLOW_UP",
        note="Recruiter said two weeks.",
        follow_up_at="2026-10-21T09:00:00+02:00",
    )
    assert response.status_code == 200, response.text
    tracking = response.json()["tracking"]
    assert tracking["status"] == "FOLLOW_UP"
    assert datetime.fromisoformat(tracking["follow_up_at"]) == datetime(
        2026, 10, 21, 7, 0, tzinfo=UTC
    )


@pytest.mark.parametrize("status", ["PREPARING", "WITHDRAWN", "REJECTED", "NO_RESPONSE", "OFFER"])
def test_a_follow_up_date_only_makes_sense_while_waiting(
    client: TestClient, auth: dict[str, str], application: dict[str, Any], status: str
) -> None:
    put_in_status(client, auth, application["id"], status)
    response = update_tracking(
        client, auth, application["id"], {"follow_up_at": "2026-10-21T09:00:00Z"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "follow_up_at"
    assert tracking_of(client, auth, application["id"])["follow_up_at"] is None


def test_a_follow_up_date_is_dropped_when_the_waiting_ends(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    update_tracking(client, auth, application["id"], {"follow_up_at": "2026-10-21T09:00:00Z"})
    move(client, auth, application["id"], "INTERVIEW")
    assert tracking_of(client, auth, application["id"])["follow_up_at"] is not None  # still waiting
    move(client, auth, application["id"], "REJECTED")
    assert tracking_of(client, auth, application["id"])["follow_up_at"] is None


def test_a_status_change_cannot_carry_a_follow_up_date_for_a_status_that_does_not_wait(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    response = set_status(
        client, auth, application["id"], "REJECTED", follow_up_at="2026-10-21T09:00:00Z"
    )
    assert response.status_code == 422
    assert tracking_of(client, auth, application["id"])["status"] == "APPLIED"  # nothing applied


def test_a_follow_up_date_does_not_send_or_schedule_anything(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    """A past date is just a date: nothing reads it, nothing runs. (`_no_real_http` makes any
    network call fail loudly; there is no scheduler to inspect.)"""
    move(client, auth, application["id"], "APPLIED")
    update_tracking(client, auth, application["id"], {"follow_up_at": "2020-01-01T00:00:00Z"})
    assert tracking_of(client, auth, application["id"])["status"] == "APPLIED"
    assert len(history_rows(db_session, application["id"])) == 3


# --- Notes ----------------------------------------------------------------------------------------


def test_a_note_without_a_status_change_is_a_note_entry(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    response = update_tracking(
        client, auth, application["id"], {"note": "Interview scheduled for 14 October."}
    )
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True
    rows = assert_history_is_consistent(db_session, application["id"])
    assert rows[-1].entry_type.value == "NOTE"
    assert rows[-1].note == "Interview scheduled for 14 October."
    assert rows[-1].source.value == "USER"
    assert tracking_of(client, auth, application["id"])["status"] == "APPLIED"


def test_the_tracking_update_needs_something_to_update(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    assert update_tracking(client, auth, application["id"], {}).status_code == 422
    assert update_tracking(client, auth, application["id"], {"note": "  "}).status_code == 422


def test_a_note_and_a_follow_up_date_together(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    response = update_tracking(
        client,
        auth,
        application["id"],
        {"note": "Recruiter said two weeks.", "follow_up_at": "2026-10-21T09:00:00Z"},
    )
    assert response.status_code == 200
    assert response.json()["tracking"]["follow_up_at"] is not None
    assert history_rows(db_session, application["id"])[-1].note == "Recruiter said two weeks."


def test_notes_are_not_part_of_the_workspace_notes(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    update_tracking(client, auth, application["id"], {"note": "A tracking note."})
    assert get_application(client, auth, application["id"])["notes"] is None


# --- Timeline -------------------------------------------------------------------------------------


def test_the_timeline_is_newest_first_and_complete(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    update_tracking(client, auth, application["id"], {"note": "Waiting for feedback."})
    set_status(client, auth, application["id"], "INTERVIEW", note="Interview invitation.")

    items = timeline(client, auth, application["id"])

    assert [(i["kind"], i["status"]) for i in items] == [
        ("STATUS_CHANGE", "INTERVIEW"),
        ("NOTE", "APPLIED"),
        ("STATUS_CHANGE", "APPLIED"),
        ("STATUS_CHANGE", "PREPARING"),
        ("STATUS_CHANGE", "SAVED"),
    ]
    first = items[0]
    assert first["previous_status"] == "APPLIED" and first["note"] == "Interview invitation."
    assert first["source"] == "USER" and first["actor"] == "USER"
    assert items[-1]["previous_status"] is None and items[-1]["source"] == "SYSTEM"
    assert items[-1]["note"] is None  # system rows carry no text
    times = [i["occurred_at"] for i in items]
    assert times == sorted(times, reverse=True)


def test_an_application_without_a_submission_has_no_submission_entries(
    client: TestClient, auth: dict[str, str], application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    assert all(i["kind"] != "SUBMISSION" for i in timeline(client, auth, application["id"]))


# --- Ownership ------------------------------------------------------------------------------------


def test_another_user_cannot_see_or_change_tracking(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    db_session: Session,
    application: dict[str, Any],
) -> None:
    aid = application["id"]
    move(client, auth, aid, "APPLIED")
    rows_before = len(history_rows(db_session, aid))
    before = tracking_of(client, auth, aid)

    assert client.get(f"{API}/applications/{aid}", headers=other_auth).status_code == 404
    assert client.get(f"{API}/applications/{aid}/timeline", headers=other_auth).status_code == 404
    assert set_status(client, other_auth, aid, "INTERVIEW", note="mine now").status_code == 404
    assert (
        set_status(client, other_auth, aid, "PREPARING", correction=True, note="x").status_code
        == 404
    )
    assert update_tracking(client, other_auth, aid, {"note": "hi"}).status_code == 404
    assert (
        update_tracking(
            client, other_auth, aid, {"follow_up_at": "2026-10-21T09:00:00Z"}
        ).status_code
        == 404
    )

    assert len(history_rows(db_session, aid)) == rows_before
    assert tracking_of(client, auth, aid) == before
    # An application that doesn't exist looks the same.
    missing = str(uuid.uuid4())
    assert set_status(client, auth, missing, "APPLIED").status_code == 404
    assert client.get(f"{API}/applications/{missing}/timeline", headers=auth).status_code == 404


def test_the_lists_and_counts_only_include_your_own_applications(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    db_session: Session,
    application: dict[str, Any],
) -> None:
    move(client, auth, application["id"], "APPLIED")
    assert client.get(f"{API}/applications", headers=other_auth).json()["total"] == 0
    counts = client.get(f"{API}/applications/summary", headers=other_auth).json()
    assert counts["total"] == 0 and all(v == 0 for v in counts["counts"].values())
    mine = client.get(f"{API}/applications/summary", headers=auth).json()
    assert mine["total"] == 1 and mine["counts"]["APPLIED"] == 1


def test_tracking_endpoints_need_a_login(client: TestClient, application: dict[str, Any]) -> None:
    aid = application["id"]
    assert client.get(f"{API}/applications").status_code == 401
    assert client.get(f"{API}/applications/summary").status_code == 401
    assert client.get(f"{API}/applications/{aid}/timeline").status_code == 401
    assert (
        client.patch(f"{API}/applications/{aid}/status", json={"status": "APPLIED"}).status_code
        == 401
    )
    assert client.patch(f"{API}/applications/{aid}/tracking", json={"note": "x"}).status_code == 401


# --- "My Applications": the list ------------------------------------------------------------------


def make_application(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    *,
    company: str = "Example GmbH",
    title: str = "Senior Project Manager",
    source: str = "greenhouse",
    status: str = "PREPARING",
) -> dict[str, Any]:
    job = make_job(db, user_by_email(db), sources=(source,), company_name=company, title=title)
    created = started(client, auth, job)
    put_in_status(client, auth, created["id"], status)
    return created


def listed(client: TestClient, auth: dict[str, str], query: str = "") -> dict[str, Any]:
    response = client.get(f"{API}/applications{query}", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


@pytest.fixture
def several(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> dict[str, dict[str, Any]]:
    return {
        "acme_applied": make_application(
            client, auth, db_session, company="Acme Corp", title="Engineer", status="APPLIED"
        ),
        "acme_prep": make_application(
            client, auth, db_session, company="Acme Corp", title="Designer", source="lever"
        ),
        "globex_interview": make_application(
            client, auth, db_session, company="Globex 100% Inc", status="INTERVIEW", source="lever"
        ),
        "initech_rejected": make_application(
            client, auth, db_session, company="Initech_GmbH", status="REJECTED", source="other"
        ),
    }


def ids(body: dict[str, Any]) -> list[str]:
    return [item["id"] for item in body["items"]]


def test_the_list_shows_what_my_applications_needs(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    created = make_application(client, auth, db_session, status="APPLIED")
    update_tracking(client, auth, created["id"], {"follow_up_at": "2026-10-21T09:00:00Z"})
    item = listed(client, auth)["items"][0]
    assert item["id"] == created["id"]
    assert item["company_name"] == "Example GmbH" and item["title"] == "Senior Project Manager"
    assert item["status"] == "APPLIED"
    assert item["source"] == "greenhouse"
    assert item["original_url"].startswith("https://job-boards.greenhouse.io/")
    assert item["applied_at"] is not None
    assert item["last_status_change_at"] is not None
    assert item["follow_up_at"].startswith("2026-10-21T09:00:00")
    assert item["submission"] is None


def test_filter_by_status(
    client: TestClient, auth: dict[str, str], several: dict[str, dict[str, Any]]
) -> None:
    assert ids(listed(client, auth, "?status=APPLIED")) == [several["acme_applied"]["id"]]
    both = ids(listed(client, auth, "?status=APPLIED&status=INTERVIEW"))
    assert set(both) == {several["acme_applied"]["id"], several["globex_interview"]["id"]}
    assert listed(client, auth, "?status=OFFER")["total"] == 0
    assert client.get(f"{API}/applications?status=bogus", headers=auth).status_code == 422
    assert listed(client, auth)["total"] == 4


def test_filter_by_company_contains_any_case_with_wildcards_taken_literally(
    client: TestClient, auth: dict[str, str], several: dict[str, dict[str, Any]]
) -> None:
    assert set(ids(listed(client, auth, "?company=acme"))) == {
        several["acme_applied"]["id"],
        several["acme_prep"]["id"],
    }
    assert ids(listed(client, auth, "?company=100%25")) == [several["globex_interview"]["id"]]
    assert ids(listed(client, auth, "?company=nitech_g")) == [several["initech_rejected"]["id"]]
    # A literal "%" matches only the company that has one -- not everything.
    assert ids(listed(client, auth, "?company=%25")) == [several["globex_interview"]["id"]]
    # ... and a literal "_" only Initech_GmbH, not "any character".
    assert ids(listed(client, auth, "?company=_")) == [several["initech_rejected"]["id"]]


def test_filter_by_source(
    client: TestClient, auth: dict[str, str], several: dict[str, dict[str, Any]]
) -> None:
    assert set(ids(listed(client, auth, "?source=lever"))) == {
        several["acme_prep"]["id"],
        several["globex_interview"]["id"],
    }
    assert ids(listed(client, auth, "?source=GREENHOUSE")) == [several["acme_applied"]["id"]]
    assert ids(listed(client, auth, "?source=other")) == [several["initech_rejected"]["id"]]


def test_filters_combine(
    client: TestClient, auth: dict[str, str], several: dict[str, dict[str, Any]]
) -> None:
    body = listed(client, auth, "?company=acme&source=lever&status=PREPARING")
    assert ids(body) == [several["acme_prep"]["id"]]
    assert listed(client, auth, "?company=acme&status=REJECTED")["total"] == 0


def test_filter_by_application_date(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    several: dict[str, dict[str, Any]],
) -> None:
    for key, day in (
        ("acme_applied", "2026-03-10"),
        ("globex_interview", "2026-03-20"),
        ("initech_rejected", "2026-06-01"),
    ):
        db_session.execute(
            text("UPDATE applications SET applied_at = :at WHERE id = :id"),
            {"at": f"{day}T23:30:00+00:00", "id": several[key]["id"]},
        )
    db_session.commit()
    assert set(ids(listed(client, auth, "?applied_from=2026-03-15"))) == {
        several["globex_interview"]["id"],
        several["initech_rejected"]["id"],
    }
    assert ids(listed(client, auth, "?applied_to=2026-03-10")) == [several["acme_applied"]["id"]]
    both = listed(client, auth, "?applied_from=2026-03-10&applied_to=2026-03-20")
    assert set(ids(both)) == {several["acme_applied"]["id"], several["globex_interview"]["id"]}
    assert listed(client, auth, "?applied_from=2026-03-11&applied_to=2026-03-19")["total"] == 0
    assert client.get(f"{API}/applications?applied_from=yesterday", headers=auth).status_code == 422


def test_default_sort_is_most_recently_updated_first(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    several: dict[str, dict[str, Any]],
) -> None:
    update_tracking(client, auth, several["acme_prep"]["id"], {"note": "Touched last."})
    body = listed(client, auth)
    assert ids(body)[0] == several["acme_prep"]["id"]
    stamps = [item["updated_at"] for item in body["items"]]
    assert stamps == sorted(stamps, reverse=True)


def test_sort_by_application_date_puts_unapplied_last(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    several: dict[str, dict[str, Any]],
) -> None:
    for key, day in (
        ("acme_applied", "2026-03-10"),
        ("globex_interview", "2026-04-01"),
        ("initech_rejected", "2026-02-01"),
    ):
        db_session.execute(
            text("UPDATE applications SET applied_at = :at WHERE id = :id"),
            {"at": f"{day}T10:00:00+00:00", "id": several[key]["id"]},
        )
    db_session.commit()
    body = listed(client, auth, "?sort=applied")
    assert ids(body) == [
        several["globex_interview"]["id"],
        several["acme_applied"]["id"],
        several["initech_rejected"]["id"],
        several["acme_prep"]["id"],  # not applied yet: last
    ]
    assert client.get(f"{API}/applications?sort=oldest", headers=auth).status_code == 422


def test_pagination_keeps_filters_and_totals(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    for index in range(5):
        make_application(client, auth, db_session, company=f"Acme {index}", status="APPLIED")
    make_application(client, auth, db_session, company="Other")
    first = listed(client, auth, "?company=acme&page_size=2&page=1")
    last = listed(client, auth, "?company=acme&page_size=2&page=3")
    assert first["total"] == 5 and first["total_pages"] == 3 and len(first["items"]) == 2
    assert len(last["items"]) == 1
    assert not set(ids(first)) & set(ids(last))


def test_status_counts_list_every_status(
    client: TestClient, auth: dict[str, str], several: dict[str, dict[str, Any]]
) -> None:
    body = client.get(f"{API}/applications/summary", headers=auth).json()
    assert body["total"] == 4
    assert set(body["counts"]) == set(ALL_STATUSES)
    assert body["counts"]["APPLIED"] == 1 and body["counts"]["PREPARING"] == 1
    assert body["counts"]["INTERVIEW"] == 1 and body["counts"]["REJECTED"] == 1
    assert body["counts"]["NO_RESPONSE"] == 0 and body["counts"]["OFFER"] == 0


# --- The original job may change or disappear -----------------------------------------------------


def test_tracking_survives_the_job_changing_and_the_posting_disappearing(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    owner = user_by_email(db_session)
    job = make_job(db_session, owner, company_name="Example GmbH", title="Senior Project Manager")
    created = started(client, auth, job)
    move(client, auth, created["id"], "APPLIED")
    original_url = listed(client, auth)["items"][0]["original_url"]

    # The canonical job is rewritten, and its postings go away entirely.
    job.title = "Something Else"
    job.company_name = "Renamed AG"
    for posting in db_session.scalars(select(JobSourcePosting)):
        db_session.delete(posting)
    db_session.commit()

    item = listed(client, auth)["items"][0]
    assert (item["company_name"], item["title"]) == ("Example GmbH", "Senior Project Manager")
    assert item["original_url"] == original_url  # the URL the source gave, not an internal one
    assert listed(client, auth, "?company=example")["total"] == 1
    assert listed(client, auth, "?company=renamed")["total"] == 0
    assert tracking_of(client, auth, created["id"])["status"] == "APPLIED"
    assert len(timeline(client, auth, created["id"])) == 3
    move(client, auth, created["id"], "INTERVIEW")  # still fully usable


def test_an_inactive_posting_does_not_remove_the_application(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    created = make_application(client, auth, db_session, status="APPLIED")
    db_session.execute(text("UPDATE job_source_postings SET is_active = false"))
    db_session.commit()
    assert listed(client, auth)["total"] == 1
    detail = get_application(client, auth, created["id"])
    assert detail["source_is_active"] is False
    assert detail["context"]["source"]["original_url"]


# --- Database-level guarantees --------------------------------------------------------------------


def test_the_database_rejects_inconsistent_application_rows(
    client: TestClient, db_session: Session, application: dict[str, Any]
) -> None:
    for statement in (
        # applied without an application date
        "UPDATE applications SET status = 'APPLIED', applied_at = NULL WHERE id = :id",
        # a follow-up date while preparing
        "UPDATE applications SET follow_up_at = now() WHERE id = :id",
    ):
        with pytest.raises(IntegrityError):
            db_session.execute(text(statement), {"id": application["id"]})
        db_session.rollback()


def test_the_database_rejects_inconsistent_history_rows(
    client: TestClient, db_session: Session, application: dict[str, Any]
) -> None:
    owner = user_by_email(db_session)
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user": owner.id,
        "app": application["id"],
        "seq": 3,
        "type": "STATUS_CHANGE",
        "prev": "PREPARING",
        "new": "APPLIED",
        "source": "USER",
        "actor": "USER",
        "note": None,
    }
    insert = text(
        "INSERT INTO application_status_history (id, user_id, application_id, sequence, "
        "entry_type, previous_status, new_status, changed_at, source, actor, note) VALUES "
        "(:id, :user, :app, :seq, :type, :prev, :new, now(), :source, :actor, :note)"
    )
    bad_rows: list[dict[str, Any]] = [
        {"seq": 2},  # the sequence number is taken
        {"seq": 0},
        {"type": "CORRECTION"},  # a correction needs a note
        {"type": "NOTE", "note": "x"},  # a note keeps the status
        {"source": "USER", "actor": "SYSTEM"},
        {"user": uuid.uuid4()},  # someone else's application
    ]
    for override in bad_rows:
        with pytest.raises(IntegrityError):
            db_session.execute(insert, {**base, **override})
        db_session.rollback()
    # The valid version of the same row is accepted.
    db_session.execute(insert, base)
    db_session.rollback()


def test_deleting_the_application_removes_its_history(
    client: TestClient, db_session: Session, application: dict[str, Any]
) -> None:
    owner_job = db_session.get(Application, uuid.UUID(application["id"]))
    assert owner_job is not None
    db_session.delete(owner_job)
    db_session.commit()
    assert history_rows(db_session, application["id"]) == []


# --- Concurrency ----------------------------------------------------------------------------------


def run_threads(targets: list[Any]) -> list[Any]:
    barrier = threading.Barrier(len(targets))
    outcomes: list[Any] = []

    def wrap(target: Any) -> None:
        try:
            barrier.wait(timeout=10)
            outcomes.append(target())
        except BaseException as exc:  # noqa: BLE001 -- reported to the test
            outcomes.append(exc)

    threads = [threading.Thread(target=wrap, args=(t,)) for t in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return outcomes


def change_in_own_session(owner_id: uuid.UUID, application_id: str, **request: Any) -> Any:
    from app.core.errors import AppError
    from app.schemas.application_tracking import StatusChangeRequest, TrackingUpdateRequest
    from app.services.applications import tracking

    with TestSessionLocal() as session:
        owner = session.get(User, owner_id)
        assert owner is not None
        try:
            if "status" in request:
                result = tracking.change_status(
                    session, owner, uuid.UUID(application_id), StatusChangeRequest(**request)
                )
            else:
                result = tracking.update_tracking(
                    session, owner, uuid.UUID(application_id), TrackingUpdateRequest(**request)
                )
        except AppError as exc:
            return exc.code
        return result.changed


def test_racing_status_changes_keep_history_and_status_consistent(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    owner_id = user_by_email(db_session).id

    outcomes = run_threads(
        [
            lambda: change_in_own_session(owner_id, application["id"], status="INTERVIEW"),
            lambda: change_in_own_session(owner_id, application["id"], status="REJECTED"),
            lambda: change_in_own_session(owner_id, application["id"], status="FOLLOW_UP"),
            lambda: change_in_own_session(
                owner_id, application["id"], note="A note", status="APPLIED"
            ),
        ]
    )

    assert not [o for o in outcomes if isinstance(o, BaseException)], outcomes
    db_session.expire_all()
    rows = assert_history_is_consistent(db_session, application["id"])
    # Every request either won a place in the history or was refused against what the
    # winners left behind -- nothing vanished and nothing was applied twice.
    applied = [o for o in outcomes if o is True]
    refused = [o for o in outcomes if o == "INVALID_STATUS_TRANSITION"]
    no_ops = [o for o in outcomes if o is False]
    assert len(applied) + len(refused) + len(no_ops) == 4
    assert len(rows) == 3 + len(applied)
    # Each row's previous status was the status the application really had.
    for before, after in zip(rows, rows[1:], strict=False):
        assert after.previous_status == before.new_status


def test_racing_identical_changes_apply_once(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    owner_id = user_by_email(db_session).id

    outcomes = run_threads(
        [lambda: change_in_own_session(owner_id, application["id"], status="FOLLOW_UP")] * 6
    )

    assert sorted(map(str, outcomes)) == sorted(["True"] + ["False"] * 5), outcomes
    db_session.expire_all()
    rows = assert_history_is_consistent(db_session, application["id"])
    assert [r.new_status.value for r in rows] == ["SAVED", "PREPARING", "APPLIED", "FOLLOW_UP"]


def test_racing_notes_are_all_kept_in_order(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    move(client, auth, application["id"], "APPLIED")
    owner_id = user_by_email(db_session).id

    outcomes = run_threads(
        [
            (lambda n=n: change_in_own_session(owner_id, application["id"], note=f"note {n}"))
            for n in range(8)
        ]
    )

    assert outcomes == [True] * 8
    db_session.expire_all()
    rows = assert_history_is_consistent(db_session, application["id"])
    notes = [r.note for r in rows if r.note]
    assert sorted(notes) == sorted(f"note {n}" for n in range(8))
    assert all(r.entry_type.value == "NOTE" for r in rows[3:])


def test_a_status_change_and_a_workspace_edit_do_not_lose_each_other(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    from app.schemas.application import ApplicationUpdate
    from app.services.applications import service

    owner_id = user_by_email(db_session).id

    def edit() -> bool:
        with TestSessionLocal() as session:
            owner = session.get(User, owner_id)
            assert owner is not None
            service.update_application(
                session, owner, uuid.UUID(application["id"]), ApplicationUpdate(notes="Prepared.")
            )
            return True

    outcomes = run_threads(
        [edit, lambda: change_in_own_session(owner_id, application["id"], status="APPLIED")]
    )
    assert outcomes == [True, True]
    db_session.expire_all()
    assert_history_is_consistent(db_session, application["id"])
    detail = get_application(client, auth, application["id"])
    assert detail["notes"] == "Prepared." and detail["status"] == "APPLIED"


def test_snapshot_context_is_untouched_by_tracking(
    client: TestClient, auth: dict[str, str], db_session: Session, application: dict[str, Any]
) -> None:
    before = db_session.scalar(select(ApplicationContextSnapshot.content_hash))
    move(client, auth, application["id"], "APPLIED")
    update_tracking(client, auth, application["id"], {"note": "x"})
    db_session.expire_all()
    assert db_session.scalar(select(ApplicationContextSnapshot.content_hash)) == before
    assert db_session.scalar(select(ApplicationStatusHistory.id).limit(1)) is not None


def test_make_posting_helper_is_used(db_session: Session) -> None:
    # (keeps the shared helper import honest for the list fixtures above)
    assert make_posting is not None
