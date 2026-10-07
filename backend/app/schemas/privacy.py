from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DataCategoryRead(BaseModel):
    key: str
    title: str
    purpose: str
    retention: str
    #: Rows JobPilot holds for you, per table.
    records: dict[str, int]


class BrowserStorageRead(BaseModel):
    name: str
    type: str
    purpose: str
    strictly_necessary: bool


class ProcessorRead(BaseModel):
    name: str
    role: str
    data: str
    purpose: str
    control: str


class PrivacyOverviewRead(BaseModel):
    """`GET /privacy/overview` -- "My data"."""

    categories: list[DataCategoryRead]
    file_storage: str
    browser_storage: list[BrowserStorageRead]
    #: JobPilot sets no cookies and uses no analytics, tracking or marketing tools.
    cookies: list[BrowserStorageRead]
    optional_tracking: list[str]
    recipients: list[ProcessorRead]
    legal_notice: str


class PrivacySettingsRead(BaseModel):
    external_ai_processing_allowed: bool
    ai_provider: str
    ai_model: str | None
    #: Whether an AI provider is configured on this server at all.
    ai_configured: bool
    policy_version: str
    contact_details_pseudonymized: bool


class PrivacySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_ai_processing_allowed: bool


class ConsentRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: str
    policy_version: str
    granted: bool
    decided_at: datetime


class ConsentHistoryRead(BaseModel):
    records: list[ConsentRecordRead]


class AccountDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=128)
    #: Must be exactly the confirmation phrase ("DELETE MY ACCOUNT").
    confirmation: str = Field(min_length=1, max_length=100)


class AccountDeletionRead(BaseModel):
    deleted: bool
    deleted_records: dict[str, int]
    deleted_files: int
    file_cleanup_complete: bool
    kept: list[str]
