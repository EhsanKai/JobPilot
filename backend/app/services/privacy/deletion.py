"""Delete my account (V0.13).

Order of operations:
1. the user's live Browser Assistant windows are closed (nothing keeps acting for them);
2. the user row is deleted in one transaction -- every user-owned table references users
   with ON DELETE CASCADE (inventory.CATEGORIES; tests/test_privacy.py checks both), so
   nothing user-owned survives and nothing shared is touched;
3. after the commit, the user's file folder (resume files, CV photos) is removed.

Shared data stays: public job advertisements and name catalogs belong to no user
(inventory.SHARED_TABLES). JobPilot has no legal retention obligation implemented that
would keep any user data back; whether one applies is a legal decision (docs/privacy.md).

Deletion happens only after the user re-entered their password and typed the
confirmation phrase -- never from a single click.
"""

import hmac
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import verify_password
from app.models.automation import AutomationSession
from app.models.user import User
from app.services.automation.registry import registry
from app.services.privacy import export
from app.services.storage.base import FileStorage

logger = get_logger(__name__)

CONFIRMATION_PHRASE = "DELETE MY ACCOUNT"


@dataclass(frozen=True)
class DeletionReport:
    deleted_rows: dict[str, int]
    deleted_files: int
    file_cleanup_complete: bool


def check_confirmation(user: User, *, password: str, confirmation: str) -> None:
    """The two explicit confirmations a deletion needs. Raises 403/422 otherwise."""
    if not hmac.compare_digest(confirmation.strip().encode(), CONFIRMATION_PHRASE.encode()):
        raise AppError(
            "CONFIRMATION_REQUIRED",
            f'Type "{CONFIRMATION_PHRASE}" to confirm that you want to delete your account.',
            422,
        )
    if not verify_password(password, user.password_hash):
        logger.info("event=account_deletion_password_rejected user_id=%s", user.id)
        raise AppError("INVALID_PASSWORD", "The password is not correct.", 403)


def delete_account(db: Session, user: User, storage: FileStorage) -> DeletionReport:
    user_id: uuid.UUID = user.id
    counts = export.row_counts(db, user)
    deleted_rows = {
        f"{category}.{table}": count
        for category, tables in counts.items()
        for table, count in tables.items()
        if count
    }

    session_ids = db.scalars(
        select(AutomationSession.id).where(AutomationSession.user_id == user_id)
    ).all()
    for session_id in session_ids:
        registry.remove(session_id)

    db.execute(delete(User).where(User.id == user_id))
    db.commit()

    try:
        files = storage.delete_owner(owner_id=str(user_id))
        complete = True
    except OSError:
        # The account is gone; the leftover folder is logged (by id only) for cleanup.
        logger.exception("event=account_files_cleanup_failed user_id=%s", user_id)
        files, complete = 0, False
    logger.info(
        "event=account_deleted user_id=%s rows=%d files=%d",
        user_id,
        sum(deleted_rows.values()),
        files,
    )
    return DeletionReport(
        deleted_rows=deleted_rows, deleted_files=files, file_cleanup_complete=complete
    )
