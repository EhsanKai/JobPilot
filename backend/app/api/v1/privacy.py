"""Privacy & data (V0.13): my data, download, AI processing choice, account deletion.

Everything here acts on the signed-in user only; no endpoint takes a user id.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.ai.provider import AIProvider
from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.privacy import CONSENT_POLICY_VERSIONS, ConsentCategory
from app.models.user import User
from app.schemas.privacy import (
    AccountDeletionRead,
    AccountDeletionRequest,
    BrowserStorageRead,
    ConsentHistoryRead,
    ConsentRecordRead,
    DataCategoryRead,
    PrivacyOverviewRead,
    PrivacySettingsRead,
    PrivacySettingsUpdate,
    ProcessorRead,
)
from app.services.privacy import deletion, export, inventory, preferences
from app.services.storage.base import FileStorage
from app.services.storage.factory import get_file_storage

router = APIRouter(prefix="/privacy", tags=["privacy"])

LEGAL_NOTICE = (
    "These are JobPilot's technical privacy controls. They support, but do not by themselves "
    "establish, compliance with data-protection law (GDPR / DSGVO); that requires a legal "
    "review of this deployment."
)


@router.get("/overview", response_model=PrivacyOverviewRead)
def overview(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> PrivacyOverviewRead:
    counts = export.row_counts(db, current_user)
    return PrivacyOverviewRead(
        categories=[
            DataCategoryRead(
                key=c.key,
                title=c.title,
                purpose=c.purpose,
                retention=c.retention,
                records=counts.get(c.key, {}),
            )
            for c in inventory.CATEGORIES
        ],
        file_storage=inventory.FILE_STORAGE,
        browser_storage=[BrowserStorageRead.model_validate(s) for s in inventory.BROWSER_STORAGE],
        cookies=[],
        optional_tracking=[],
        recipients=[ProcessorRead.model_validate(p) for p in inventory.processors()],
        legal_notice=LEGAL_NOTICE,
    )


@router.get("/export")
def download_my_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> Response:
    """A ZIP with all your data (JSON) and the files you uploaded."""
    archive = export.build_archive(db, current_user, storage)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return Response(
        content=archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="jobpilot-data-{stamp}.zip"',
            "Cache-Control": "private, no-store",
        },
    )


def _settings_read(user: User, provider: AIProvider) -> PrivacySettingsRead:
    return PrivacySettingsRead(
        external_ai_processing_allowed=user.external_ai_processing_allowed,
        ai_provider=settings.AI_PROVIDER,
        ai_model=getattr(provider, "model", None),
        ai_configured=provider.name not in ("disabled", "unknown"),
        policy_version=CONSENT_POLICY_VERSIONS[ConsentCategory.EXTERNAL_AI_PROCESSING],
        contact_details_pseudonymized=settings.AI_PSEUDONYMIZE_CONTACT_DETAILS,
    )


@router.get("/settings", response_model=PrivacySettingsRead)
def get_privacy_settings(
    current_user: User = Depends(get_current_user),
    provider: AIProvider = Depends(get_ai_provider),
) -> PrivacySettingsRead:
    return _settings_read(current_user, provider)


@router.put("/settings", response_model=PrivacySettingsRead)
def update_privacy_settings(
    data: PrivacySettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> PrivacySettingsRead:
    """Allow or stop external AI processing. Takes effect for the next request."""
    preferences.set_external_ai_processing(db, current_user, data.external_ai_processing_allowed)
    return _settings_read(current_user, provider)


@router.get("/consents", response_model=ConsentHistoryRead)
def consent_history(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ConsentHistoryRead:
    return ConsentHistoryRead(
        records=[ConsentRecordRead.model_validate(r) for r in preferences.history(db, current_user)]
    )


@router.post("/delete-account", response_model=AccountDeletionRead)
def delete_my_account(
    data: AccountDeletionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> AccountDeletionRead:
    """Permanently delete your account and everything it owns. Needs your password and the
    confirmation phrase. Cannot be undone."""
    deletion.check_confirmation(
        current_user, password=data.password, confirmation=data.confirmation
    )
    report = deletion.delete_account(db, current_user, storage)
    return AccountDeletionRead(
        deleted=True,
        deleted_records=report.deleted_rows,
        deleted_files=report.deleted_files,
        file_cleanup_complete=report.file_cleanup_complete,
        kept=[
            "Public job advertisements and shared name catalogs (skills, languages, "
            "industries): they belong to no user and other users may see them.",
        ],
    )
