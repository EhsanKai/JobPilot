"""Helpers for the V0.12 application tracking tests."""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, ApplicationStatusHistory
from tests.application_helpers import API

#: How to reach each status from a new (PREPARING) application using only ordinary
#: transitions -- written out here, not computed from the code under test. SAVED is only
#: reachable by correction.
PATH_TO: dict[str, list[str]] = {
    "SAVED": [],
    "PREPARING": [],
    "APPLIED": ["APPLIED"],
    "FOLLOW_UP": ["APPLIED", "FOLLOW_UP"],
    "INTERVIEW": ["APPLIED", "INTERVIEW"],
    "OFFER": ["APPLIED", "OFFER"],
    "REJECTED": ["APPLIED", "REJECTED"],
    "WITHDRAWN": ["WITHDRAWN"],
    "NO_RESPONSE": ["APPLIED", "NO_RESPONSE"],
}

#: The lifecycle of the V0.12 specification, written out independently of the code.
EXPECTED_TRANSITIONS: dict[str, set[str]] = {
    "SAVED": {"PREPARING", "WITHDRAWN"},
    "PREPARING": {"APPLIED", "WITHDRAWN"},
    "APPLIED": {"FOLLOW_UP", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"},
    "FOLLOW_UP": {"INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"},
    "INTERVIEW": {"OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"},
    "OFFER": {"WITHDRAWN", "REJECTED"},
    "REJECTED": set(),
    "WITHDRAWN": set(),
    "NO_RESPONSE": set(),
}
ALL_STATUSES = list(EXPECTED_TRANSITIONS)


def set_status(
    client: TestClient, headers: dict[str, str], app_id: str, status: str, **body: Any
) -> Any:
    return client.patch(
        f"{API}/applications/{app_id}/status",
        headers=headers,
        json={"status": status, **body},
    )


def move(client: TestClient, headers: dict[str, str], application_id: str, status: str) -> None:
    """An ordinary, allowed status change that must succeed."""
    response = set_status(client, headers, application_id, status)
    assert response.status_code == 200, response.text


def put_in_status(
    client: TestClient, headers: dict[str, str], application_id: str, status: str
) -> None:
    """Bring a new (PREPARING) application to `status` along allowed transitions."""
    if status == "SAVED":
        response = set_status(
            client, headers, application_id, "SAVED", correction=True, note="Back to saved."
        )
        assert response.status_code == 200, response.text
        return
    for step in PATH_TO[status]:
        move(client, headers, application_id, step)


def update_tracking(
    client: TestClient, headers: dict[str, str], application_id: str, body: dict[str, Any]
) -> Any:
    return client.patch(f"{API}/applications/{application_id}/tracking", headers=headers, json=body)


def timeline(
    client: TestClient, headers: dict[str, str], application_id: str
) -> list[dict[str, Any]]:
    response = client.get(f"{API}/applications/{application_id}/timeline", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]  # type: ignore[no-any-return]


def history_rows(db: Session, application_id: str) -> list[ApplicationStatusHistory]:
    return list(
        db.scalars(
            select(ApplicationStatusHistory)
            .where(ApplicationStatusHistory.application_id == uuid.UUID(application_id))
            .order_by(ApplicationStatusHistory.sequence)
            .execution_options(populate_existing=True)
        )
    )


def assert_history_is_consistent(
    db: Session, application_id: str
) -> list[ApplicationStatusHistory]:
    """The invariants: sequence 1..n without gaps, each row's previous status is the row
    before's new status, and the application's status is the last row's new status."""
    rows = history_rows(db, application_id)
    assert rows, "an application always has history"
    assert [r.sequence for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0].previous_status is None
    for before, after in zip(rows, rows[1:], strict=False):
        assert after.previous_status == before.new_status, (before.sequence, after.sequence)
    application = db.get(Application, uuid.UUID(application_id), populate_existing=True)
    assert application is not None
    assert application.status == rows[-1].new_status
    return rows


def change_of(row: ApplicationStatusHistory) -> tuple[str | None, str]:
    """(previous status, new status) of a history row, as the API spells them."""
    return (row.previous_status.value if row.previous_status else None, row.new_status.value)
