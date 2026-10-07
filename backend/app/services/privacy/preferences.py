"""The user's privacy choices and their record (V0.13).

Today there is one choice: external AI processing. Every change is appended to
consent_records with the policy version it was made under, so the history of decisions is
provable and the latest decision is the one in force. Revoking is as easy as granting.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.privacy import CONSENT_POLICY_VERSIONS, ConsentCategory, ConsentRecord
from app.models.user import User

logger = get_logger(__name__)


def set_external_ai_processing(db: Session, user: User, allowed: bool) -> ConsentRecord | None:
    """Record the decision and apply it. Commits. Returns the new record, or None when the
    setting already had this value (nothing to record)."""
    if user.external_ai_processing_allowed is allowed:
        return None
    category = ConsentCategory.EXTERNAL_AI_PROCESSING
    record = ConsentRecord(
        user_id=user.id,
        category=category.value,
        policy_version=CONSENT_POLICY_VERSIONS[category],
        granted=allowed,
    )
    user.external_ai_processing_allowed = allowed
    db.add(record)
    db.commit()
    db.refresh(record)
    logger.info(
        "event=privacy_choice_recorded user_id=%s category=%s granted=%s",
        user.id,
        category.value,
        allowed,
    )
    return record


def history(db: Session, user: User) -> list[ConsentRecord]:
    return list(
        db.scalars(
            select(ConsentRecord)
            .where(ConsentRecord.user_id == user.id)
            .order_by(ConsentRecord.decided_at.desc(), ConsentRecord.id)
        )
    )
