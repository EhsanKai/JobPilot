"""User review of an extracted Resume Profile.

AI-extracted data is never trusted automatically: every item starts as
"pending" and must be accepted, edited, or rejected before the profile can
be confirmed. Only a CONFIRMED profile is the trusted Resume Profile for
future features. The parser's original output (`raw_data`) is never
modified, so "what the AI said" and "what the user confirmed" stay
distinguishable.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.models.resume import ResumeStatus
from app.models.resume_profile import ResumeProfile, ResumeProfileStatus
from app.models.user import User
from app.schemas.resume_profile import (
    EDIT_MODELS,
    SECTION_NAMES,
    ProfileSummary,
    ResumeProfileData,
    ResumeProfileRead,
    ReviewItemRequest,
    SectionCounts,
    normalize_partial_date,
)
from app.services.resume_intelligence.conflicts import compute_derived, detect_conflicts
from app.services.resume_intelligence.processing_service import (
    get_current_profile,
    get_owned_resume,
    is_processing,
    profile_warnings,
)

_DATE_FIELDS = {"start_date", "end_date", "date"}
_LIST_FIELDS = {"responsibilities", "technologies", "achievements"}


def load_data(profile: ResumeProfile, *, raw: bool = False) -> ResumeProfileData:
    document = profile.raw_data if raw else profile.reviewed_data
    return ResumeProfileData.model_validate(document or {})


def summarize(data: ResumeProfileData) -> ProfileSummary:
    sections: dict[str, SectionCounts] = {name: SectionCounts() for name in SECTION_NAMES}
    for section, item in data.iter_items():
        counts = sections[section]
        counts.total += 1
        setattr(counts, item.review_status, getattr(counts, item.review_status) + 1)
    return ProfileSummary(
        sections=sections,
        total_items=sum(c.total for c in sections.values()),
        pending_items=sum(c.pending for c in sections.values()),
    )


def build_profile_read(profile: ResumeProfile, user: User) -> ResumeProfileRead:
    data = load_data(profile)
    derived = compute_derived(data)
    return ResumeProfileRead(
        id=profile.id,
        resume_id=profile.resume_id,
        status=profile.status.value,
        parser_version=profile.parser_version,
        schema_version=profile.schema_version,
        ai_provider=profile.ai_provider,
        ai_model=profile.ai_model,
        source_file_hash=profile.source_file_hash,
        reused_from_profile_id=profile.reused_from_profile_id,
        extraction_method=profile.extraction_method,
        page_count=profile.page_count,
        created_at=profile.created_at,
        processed_at=profile.processed_at,
        confirmed_at=profile.confirmed_at,
        data=data,
        raw_data=load_data(profile, raw=True),
        warnings=profile_warnings(profile),
        summary=summarize(data),
        conflicts=detect_conflicts(data, user, derived),
        derived=derived,
    )


def _editable_profile(db: Session, user: User, resume_id: uuid.UUID) -> ResumeProfile:
    resume = get_owned_resume(db, user, resume_id)
    if is_processing(resume):
        raise ConflictError("This resume is being analyzed. Please wait until it finishes.")
    profile = get_current_profile(db, resume)
    if profile is None:
        raise NotFoundError("This resume hasn't been analyzed yet.")
    return profile


def _save(db: Session, profile: ResumeProfile, data: ResumeProfileData) -> None:
    profile.reviewed_data = data.model_dump(mode="json")
    flag_modified(profile, "reviewed_data")
    db.commit()
    db.refresh(profile)


def _clean_changes(section: str, changes: dict[str, Any]) -> dict[str, Any]:
    edit_model = EDIT_MODELS[section]  # type: ignore[index]
    try:
        validated = edit_model.model_validate(changes)
    except ValidationError as exc:
        first = exc.errors(include_input=False)[0]
        field = ".".join(str(p) for p in first.get("loc", ()))
        raise ValidationAppError(
            f"Invalid value for '{field}': {first.get('msg')}", {"field": field}
        ) from None

    cleaned: dict[str, Any] = {}
    for name in validated.model_fields_set:
        value = getattr(validated, name)
        if name in _DATE_FIELDS and value is not None:
            normalized = normalize_partial_date(value)
            if normalized is None:
                raise ValidationAppError(
                    f"'{name}' must be a year (YYYY) or year and month (YYYY-MM).",
                    {"field": name},
                )
            value = normalized
        if name in _LIST_FIELDS and value is not None:
            value = [v.strip() for v in value if v and v.strip()]
        cleaned[name] = value
    if not cleaned:
        raise ValidationAppError("No changes were provided.")
    return cleaned


def review_item(
    db: Session, user: User, resume_id: uuid.UUID, item_id: str, request: ReviewItemRequest
) -> ResumeProfile:
    profile = _editable_profile(db, user, resume_id)
    if profile.status == ResumeProfileStatus.CONFIRMED:
        raise ConflictError("This resume profile is confirmed. Reopen the review to change it.")

    data = load_data(profile)
    found = data.find_item(item_id)
    if found is None:
        raise NotFoundError("Item not found in this resume profile.")
    section, item = found

    if request.action == "accept":
        # Accepting an edited item keeps it marked as edited (the values are
        # the user's, not the parser's).
        updated = item.model_copy(
            update={"review_status": "edited" if item.user_edited_fields else "accepted"}
        )
    elif request.action == "reject":
        updated = item.model_copy(update={"review_status": "rejected"})
    elif request.action == "reset":
        original = load_data(profile, raw=True).find_item(item_id)
        if original is None:
            raise NotFoundError("Original item not found.")
        updated = original[1].model_copy(
            update={"review_status": "pending", "user_edited_fields": []}
        )
    else:  # edit
        if not request.changes:
            raise ValidationAppError("Provide the fields to change.")
        changes = _clean_changes(section, request.changes)
        changed = {k for k, v in changes.items() if getattr(item, k) != v}
        try:
            updated = type(item).model_validate(
                {
                    **item.model_dump(),
                    **changes,
                    "review_status": "edited",
                    "user_edited_fields": sorted(set(item.user_edited_fields) | changed),
                }
            )
        except ValidationError as exc:
            first = exc.errors(include_input=False)[0]
            raise ValidationAppError(f"Invalid edit: {first.get('msg')}") from None

    data.replace_item(section, updated)
    _save(db, profile, data)
    return profile


def accept_pending(db: Session, user: User, resume_id: uuid.UUID) -> ResumeProfile:
    """Bulk-accept every item still pending (an explicit user action)."""
    profile = _editable_profile(db, user, resume_id)
    if profile.status == ResumeProfileStatus.CONFIRMED:
        raise ConflictError("This resume profile is already confirmed.")
    data = load_data(profile)
    for section, item in list(data.iter_items()):
        if item.review_status == "pending":
            data.replace_item(section, item.model_copy(update={"review_status": "accepted"}))
    _save(db, profile, data)
    return profile


def confirm_profile(db: Session, user: User, resume_id: uuid.UUID) -> ResumeProfile:
    profile = _editable_profile(db, user, resume_id)
    if profile.status == ResumeProfileStatus.CONFIRMED:
        return profile
    pending = summarize(load_data(profile)).pending_items
    if pending:
        raise ConflictError(
            f"{pending} item(s) still need review. Accept, edit, or reject them first.",
            {"pending_items": pending},
        )
    profile.status = ResumeProfileStatus.CONFIRMED
    profile.confirmed_at = datetime.now(UTC)
    profile.resume.status = ResumeStatus.CONFIRMED
    db.commit()
    db.refresh(profile)
    return profile


def reopen_profile(db: Session, user: User, resume_id: uuid.UUID) -> ResumeProfile:
    profile = _editable_profile(db, user, resume_id)
    if profile.status != ResumeProfileStatus.CONFIRMED:
        return profile
    profile.status = ResumeProfileStatus.NEEDS_REVIEW
    profile.confirmed_at = None
    profile.resume.status = ResumeStatus.NEEDS_REVIEW
    db.commit()
    db.refresh(profile)
    return profile
