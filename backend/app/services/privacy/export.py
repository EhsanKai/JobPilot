"""Download my data (V0.13): everything JobPilot stores about the signed-in user.

A ZIP archive with
- `jobpilot-data.json`: every user-owned row, grouped by category (inventory.CATEGORIES),
  plus the shared job advertisements that are in the user's job list;
- `files/`: the user's own uploaded resume files and CV photos, byte for byte;
- `README.txt`: what the archive contains.

Only rows owned by this user are read (every query is scoped by user_id, or by a parent
row that is). Password hashes and internal storage paths are never included.
"""

import datetime as dt
import decimal
import enum
import io
import json
import uuid
import zipfile
from collections.abc import Iterable
from typing import Any

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.base import Base
from app.models.user import User
from app.services.privacy import inventory
from app.services.storage.base import FileStorage
from app.services.storage.uploads import safe_file_name

logger = get_logger(__name__)

EXPORT_FORMAT = "jobpilot-export/1"

#: Names resolved from the shared catalogs, so the export is readable on its own.
_CATALOG_NAMES = {
    "user_skills": ("skill_id", "skills", "skill_name"),
    "user_languages": ("language_id", "languages", "language_name"),
    "user_industries": ("industry_id", "industries", "industry_name"),
}
_JOB_COLUMNS = (
    "id",
    "title",
    "company_name",
    "location",
    "work_model",
    "employment_type",
    "canonical_url",
    "apply_url",
    "posted_at",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes):
        return None  # no binary columns are user data; never dump raw bytes into JSON
    return value


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _rows(
    db: Session, name: str, user_id: uuid.UUID, parents: dict[str, set[Any]]
) -> list[dict[str, Any]]:
    table = _table(name)
    if "user_id" in table.c:
        statement = select(table).where(table.c.user_id == user_id)
    elif name == "users":
        statement = select(table).where(table.c.id == user_id)
    else:
        column, parent = inventory.CHILD_LINKS[name]
        ids = parents.get(parent, set())
        if not ids:
            return []
        statement = select(table).where(table.c[column].in_(ids))
    excluded = inventory.EXCLUDED_COLUMNS.get(name, frozenset())
    order = [c for c in ("created_at", "id") if c in table.c]
    if order:
        statement = statement.order_by(*(table.c[c] for c in order))
    return [
        {key: _json_value(value) for key, value in row._mapping.items() if key not in excluded}
        for row in db.execute(statement)
    ]


def _resolve_catalog_names(db: Session, name: str, rows: list[dict[str, Any]]) -> None:
    if name not in _CATALOG_NAMES or not rows:
        return
    column, catalog, label = _CATALOG_NAMES[name]
    table = _table(catalog)
    ids = {uuid.UUID(r[column]) for r in rows if r.get(column)}
    names = {
        str(row.id): row.name
        for row in db.execute(select(table.c.id, table.c.name).where(table.c.id.in_(ids)))
    }
    for row in rows:
        row[label] = names.get(str(row.get(column)))


def _referenced_jobs(db: Session, user_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = {uuid.UUID(r["job_id"]) for r in user_jobs if r.get("job_id")}
    if not ids:
        return []
    table = _table("jobs")
    columns = [table.c[c] for c in _JOB_COLUMNS if c in table.c]
    return [
        {key: _json_value(value) for key, value in row._mapping.items()}
        for row in db.execute(select(*columns).where(table.c.id.in_(ids)).order_by(table.c.id))
    ]


def collect(db: Session, user: User) -> dict[str, Any]:
    """The export document (also used by the overview for its counts)."""
    parents: dict[str, set[Any]] = {}
    data: dict[str, Any] = {}
    for category in inventory.CATEGORIES:
        section: dict[str, Any] = {}
        for name in category.tables:
            rows = _rows(db, name, user.id, parents)
            _resolve_catalog_names(db, name, rows)
            parents[name] = {uuid.UUID(r["id"]) for r in rows if r.get("id")}
            section[name] = rows
        data[category.key] = section
    data["jobs"]["jobs_in_your_list"] = _referenced_jobs(db, data["jobs"]["user_jobs"])
    return {
        "export_format": EXPORT_FORMAT,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "user_id": str(user.id),
        "about": (
            "Everything JobPilot stores about your account, grouped by category. Job "
            "advertisements are shared public data: only the ones in your list are included. "
            "Your uploaded resume files and CV photos are in the files/ folder."
        ),
        "categories": {
            c.key: {"title": c.title, "purpose": c.purpose, "retention": c.retention}
            for c in inventory.CATEGORIES
        },
        "data": data,
    }


def _unique(name: str, taken: set[str]) -> str:
    candidate, counter = name, 1
    while candidate in taken:
        stem, dot, extension = name.rpartition(".")
        counter += 1
        candidate = f"{stem}-{counter}.{extension}" if dot else f"{name}-{counter}"
    taken.add(candidate)
    return candidate


def _files(db: Session, user: User) -> Iterable[tuple[str, str]]:
    """(archive path, storage reference) of every file the user uploaded."""
    resumes = _table("resumes")
    for row in db.execute(
        select(resumes.c.version_number, resumes.c.file_name, resumes.c.storage_reference)
        .where(resumes.c.user_id == user.id)
        .order_by(resumes.c.version_number)
    ):
        name = safe_file_name(row.file_name, "resume")
        yield f"files/resumes/v{row.version_number}-{name}", row.storage_reference
    photos = _table("application_photos")
    for row in db.execute(
        select(photos.c.application_id, photos.c.file_name, photos.c.storage_reference).where(
            photos.c.user_id == user.id
        )
    ):
        name = safe_file_name(row.file_name, "photo")
        yield f"files/photos/{row.application_id}-{name}", row.storage_reference


_README = """JobPilot data export
====================

jobpilot-data.json   Everything JobPilot stores about your account, grouped by category
                     ("categories" explains each one). Machine-readable JSON (UTF-8).
files/resumes/       Your uploaded resume files, exactly as uploaded.
files/photos/        Your CV photos, exactly as uploaded.

Not included: your password (JobPilot stores only a one-way hash of it) and internal
storage locations. Job advertisements are public data shared by all users; the ones in
your list are included under data.jobs.jobs_in_your_list.
"""


def build_archive(db: Session, user: User, storage: FileStorage) -> bytes:
    document = collect(db, user)
    buffer = io.BytesIO()
    missing = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", _README)
        archive.writestr("jobpilot-data.json", json.dumps(document, ensure_ascii=False, indent=2))
        taken: set[str] = set()
        for path, reference in _files(db, user):
            try:
                content = storage.read(reference=reference)
            except (FileNotFoundError, ValueError):
                missing += 1
                continue
            archive.writestr(_unique(path, taken), content)
    logger.info("event=data_export user_id=%s missing_files=%d", user.id, missing)
    return buffer.getvalue()


def row_counts(db: Session, user: User) -> dict[str, dict[str, int]]:
    """Rows per table per category, for the "My data" overview."""
    document = collect(db, user)["data"]
    return {
        category: {name: len(rows) for name, rows in tables.items() if isinstance(rows, list)}
        for category, tables in document.items()
    }
