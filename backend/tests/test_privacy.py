"""V0.13 privacy controls: data inventory, export, account deletion, AI processing choice.

The inventory tests guard the future: a new table must be classified (user-owned and then
exported + deleted with the account, or shared) or these tests fail.
"""

import io
import json
import zipfile
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.job import Job
from app.models.skill import Skill
from app.models.user import User
from app.services.privacy import inventory
from tests.application_helpers import API, make_job, started, upload_resume, user_by_email
from tests.question_helpers import add_question


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Inventory --------------------------------------------------------------------------------


def test_every_table_is_classified_exactly_once() -> None:
    user_owned = inventory.user_tables()
    assert len(user_owned) == len(set(user_owned)), "a table is listed in two categories"
    all_tables = set(Base.metadata.tables)
    classified = set(user_owned) | inventory.SHARED_TABLES
    assert all_tables - classified == set(), "unclassified tables: add them to the inventory"
    assert classified - all_tables == set(), "the inventory lists tables that do not exist"
    assert not set(user_owned) & inventory.SHARED_TABLES


def test_every_user_owned_table_is_deleted_with_its_user() -> None:
    """Each user-owned table reaches `users` through ON DELETE CASCADE foreign keys only."""
    for name in inventory.user_tables():
        if name == "users":
            continue
        table = Base.metadata.tables[name]
        if "user_id" in table.c:
            fks = [fk for fk in table.foreign_keys if fk.column.table.name == "users"]
            assert fks and all(fk.ondelete == "CASCADE" for fk in fks), name
        else:
            column, parent = inventory.CHILD_LINKS[name]
            fks = [fk for fk in table.c[column].foreign_keys if fk.column.table.name == parent]
            assert fks and fks[0].ondelete == "CASCADE", name


def test_shared_tables_never_reference_user_data() -> None:
    user_owned = set(inventory.user_tables())
    for name in inventory.SHARED_TABLES:
        for fk in Base.metadata.tables[name].foreign_keys:
            assert fk.column.table.name not in user_owned, (name, fk.target_fullname)


# --- A user with data in every area -----------------------------------------------------------


def _populate(client: TestClient, db: Session, email: str) -> dict[str, Any]:
    from tests.conftest import TestSessionLocal  # noqa: F401  (same database)

    token = client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": "correct-password",
            "first_name": "Pri",
            "last_name": "Vacy",
        },
    ).json()["access_token"]
    headers = _headers(token)
    client.put(
        f"{API}/profile", headers=headers, json={"phone": "+49 30 1234567", "location": "Berlin"}
    )
    client.post(f"{API}/profile/skills", headers=headers, json={"name": "Python"})
    client.post(
        f"{API}/profile/languages",
        headers=headers,
        json={"name": "German", "proficiency": "native"},
    )
    client.put(
        f"{API}/preferences",
        headers=headers,
        json={
            "desired_job_titles": [{"title": "Engineer"}],
            "preferred_locations": [{"location": "Berlin"}],
        },
    )
    resume = upload_resume(client, headers)
    user = user_by_email(db, email)
    job = make_job(db, user)
    application = started(client, headers, job)
    add_question(client, headers, application["id"])
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    client.put(
        f"{API}/applications/{application['id']}/cv-photo",
        headers=headers,
        files={"file": ("me.png", png, "image/png")},
    )
    client.patch(
        f"{API}/applications/{application['id']}/status",
        headers=headers,
        json={"status": "APPLIED"},
    )
    client.put(
        f"{API}/privacy/settings", headers=headers, json={"external_ai_processing_allowed": False}
    )
    return {
        "headers": headers,
        "user": user,
        "job": job,
        "application": application,
        "resume": resume,
    }


def _rows_for(db: Session, user_id: Any) -> dict[str, int]:
    """Rows per user-owned table that belong to `user_id` (directly or through a parent)."""
    counts: dict[str, int] = {}
    parents: dict[str, list[Any]] = {}
    for name in inventory.user_tables():
        table = Base.metadata.tables[name]
        if name == "users":
            ids = db.execute(select(table.c.id).where(table.c.id == user_id)).scalars().all()
        elif "user_id" in table.c:
            ids = db.execute(select(table.c.id).where(table.c.user_id == user_id)).scalars().all()
        else:
            column, parent = inventory.CHILD_LINKS[name]
            parent_ids = parents.get(parent, [])
            ids = (
                db.execute(select(table.c.id).where(table.c[column].in_(parent_ids)))
                .scalars()
                .all()
                if parent_ids
                else []
            )
        parents[name] = list(ids)
        counts[name] = len(ids)
    return counts


# --- Overview and export --------------------------------------------------------------------------


def test_the_overview_reports_the_users_own_data_and_no_tracking(
    client: TestClient, db_session: Session
) -> None:
    alice = _populate(client, db_session, "alice@example.com")
    _populate(client, db_session, "bob@example.com")
    body = client.get(f"{API}/privacy/overview", headers=alice["headers"]).json()
    counts = {c["key"]: c["records"] for c in body["categories"]}
    assert counts["account"]["users"] == 1
    assert counts["applications"]["applications"] == 1  # Bob's is not counted
    assert counts["resumes"]["resumes"] == 1
    assert body["cookies"] == [] and body["optional_tracking"] == []
    assert body["browser_storage"][0]["strictly_necessary"] is True
    assert "legal review" in body["legal_notice"]
    assert all(c["retention"] for c in body["categories"])


def test_the_export_contains_all_and_only_the_users_own_data(
    client: TestClient, db_session: Session
) -> None:
    alice = _populate(client, db_session, "alice@example.com")
    bob = _populate(client, db_session, "bob@example.com")
    response = client.get(f"{API}/privacy/export", headers=alice["headers"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store"

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert "README.txt" in names and "jobpilot-data.json" in names
    assert any(n.startswith("files/resumes/v1-") for n in names)
    assert any(n.startswith("files/photos/") for n in names)
    raw = archive.read("jobpilot-data.json").decode()
    document = json.loads(raw)
    data = document["data"]

    # Every user-owned table is present, with exactly the user's rows.
    expected = _rows_for(db_session, alice["user"].id)
    for category in inventory.CATEGORIES:
        for name in category.tables:
            assert len(data[category.key][name]) == expected[name], name
    assert data["account"]["users"][0]["email"] == "alice@example.com"
    assert data["profile"]["user_skills"][0]["skill_name"] == "Python"
    assert data["jobs"]["jobs_in_your_list"][0]["id"] == str(alice["job"].id)
    assert data["privacy_and_security"]["consent_records"][0]["granted"] is False

    # Nothing of Bob's, no secrets, no internal paths.
    assert "bob@example.com" not in raw
    assert str(bob["user"].id) not in raw
    assert str(bob["application"]["id"]) not in raw
    assert "password_hash" not in raw and "$2b$" not in raw
    assert "storage_reference" not in raw
    session_rows = data["privacy_and_security"]["auth_sessions"]
    assert session_rows and all("id" not in row for row in session_rows)


def test_the_export_requires_a_signed_in_user(client: TestClient) -> None:
    assert client.get(f"{API}/privacy/export").status_code == 401


# --- Account deletion -----------------------------------------------------------------------------


def test_deletion_needs_the_password_and_the_exact_phrase(
    client: TestClient, db_session: Session
) -> None:
    alice = _populate(client, db_session, "alice@example.com")
    url = f"{API}/privacy/delete-account"
    wrong_password = client.post(
        url,
        headers=alice["headers"],
        json={"password": "nope", "confirmation": "DELETE MY ACCOUNT"},
    )
    assert wrong_password.status_code == 403
    assert wrong_password.json()["error"]["code"] == "INVALID_PASSWORD"
    wrong_phrase = client.post(
        url,
        headers=alice["headers"],
        json={"password": "correct-password", "confirmation": "delete"},
    )
    assert wrong_phrase.status_code == 422
    missing = client.post(url, headers=alice["headers"], json={"password": "correct-password"})
    assert missing.status_code == 422
    assert client.get(f"{API}/users/me", headers=alice["headers"]).status_code == 200
    assert sum(_rows_for(db_session, alice["user"].id).values()) > 10


def test_deleting_an_account_removes_everything_it_owns_and_nothing_else(
    client: TestClient, db_session: Session, file_storage: Any
) -> None:
    alice = _populate(client, db_session, "alice@example.com")
    bob = _populate(client, db_session, "bob@example.com")
    alice_id, bob_id = alice["user"].id, bob["user"].id
    bob_before = _rows_for(db_session, bob_id)
    jobs_before = db_session.scalar(select(func.count()).select_from(Job))
    skills_before = db_session.scalar(select(func.count()).select_from(Skill))
    alice_folder = file_storage._base_path / str(alice_id)  # noqa: SLF001
    assert alice_folder.is_dir() and any(alice_folder.rglob("*.*"))

    response = client.post(
        f"{API}/privacy/delete-account",
        headers=alice["headers"],
        json={"password": "correct-password", "confirmation": "DELETE MY ACCOUNT"},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["deleted"] is True and report["file_cleanup_complete"] is True
    assert report["deleted_files"] == 2  # resume + photo
    assert report["deleted_records"]["applications.applications"] == 1
    assert report["kept"]

    db_session.expire_all()
    assert all(count == 0 for count in _rows_for(db_session, alice_id).values())
    assert db_session.get(User, alice_id) is None
    assert not alice_folder.exists()
    # The token is dead, the e-mail address can register again.
    assert client.get(f"{API}/users/me", headers=alice["headers"]).status_code == 401
    # Bob and the shared data are untouched.
    assert _rows_for(db_session, bob_id) == bob_before
    assert db_session.scalar(select(func.count()).select_from(Job)) == jobs_before
    assert db_session.scalar(select(func.count()).select_from(Skill)) == skills_before
    assert (file_storage._base_path / str(bob_id)).is_dir()  # noqa: SLF001
    assert client.get(f"{API}/applications", headers=bob["headers"]).json()["total"] == 1
    assert (
        client.post(
            f"{API}/auth/register",
            json={
                "email": "alice@example.com",
                "password": "new-password-1",
                "first_name": "A",
                "last_name": "B",
            },
        ).status_code
        == 201
    )


def test_one_user_cannot_delete_another(client: TestClient, db_session: Session) -> None:
    alice = _populate(client, db_session, "alice@example.com")
    bob = _populate(client, db_session, "bob@example.com")
    alice_id, bob_id = alice["user"].id, bob["user"].id
    # The endpoint takes no user id: Bob's request can only ever delete Bob's account.
    response = client.post(
        f"{API}/privacy/delete-account",
        headers=bob["headers"],
        json={"password": "correct-password", "confirmation": "DELETE MY ACCOUNT"},
    )
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(User, alice_id) is not None
    assert db_session.get(User, bob_id) is None
    assert client.get(f"{API}/users/me", headers=alice["headers"]).status_code == 200


def test_no_orphans_remain_after_deletion(client: TestClient, db_session: Session) -> None:
    alice = _populate(client, db_session, "alice@example.com")
    client.post(
        f"{API}/privacy/delete-account",
        headers=alice["headers"],
        json={"password": "correct-password", "confirmation": "DELETE MY ACCOUNT"},
    )
    for name in inventory.user_tables():
        table = Base.metadata.tables[name]
        if "user_id" in table.c:
            orphans = db_session.execute(
                text(
                    f'SELECT count(*) FROM "{name}" t WHERE NOT EXISTS '  # noqa: S608
                    f"(SELECT 1 FROM users u WHERE u.id = t.user_id)"
                )
            ).scalar_one()
            assert orphans == 0, name


# --- AI processing choice and its record ----------------------------------------------------------


def test_ai_choice_is_recorded_versioned_and_revocable(
    client: TestClient, register_and_login: Any
) -> None:
    headers = _headers(register_and_login(email="choice@example.com")["access_token"])
    settings_url = f"{API}/privacy/settings"
    assert (
        client.get(settings_url, headers=headers).json()["external_ai_processing_allowed"] is True
    )
    assert client.get(f"{API}/privacy/consents", headers=headers).json()["records"] == []

    client.put(settings_url, headers=headers, json={"external_ai_processing_allowed": False})
    client.put(
        settings_url, headers=headers, json={"external_ai_processing_allowed": False}
    )  # no-op
    client.put(settings_url, headers=headers, json={"external_ai_processing_allowed": True})
    records = client.get(f"{API}/privacy/consents", headers=headers).json()["records"]
    assert [r["granted"] for r in records] == [True, False]  # newest first, no duplicate
    assert all(r["category"] == "external_ai_processing" and r["policy_version"] for r in records)
    assert set(records[0]) == {"category", "policy_version", "granted", "decided_at"}


def test_ai_choice_rejects_unknown_fields(client: TestClient, register_and_login: Any) -> None:
    headers = _headers(register_and_login(email="strict@example.com")["access_token"])
    response = client.put(
        f"{API}/privacy/settings",
        headers=headers,
        json={"external_ai_processing_allowed": True, "user_id": "someone-else"},
    )
    assert response.status_code == 422
