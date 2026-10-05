import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.resume import ResumeStatus
from app.schemas.resume_profile import ProcessingAttemptRead, ResumeProfileRead


class ResumeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    version_number: int
    file_name: str
    mime_type: str
    file_size: int
    status: ResumeStatus
    created_at: datetime
    updated_at: datetime


class ResumeProcessResponse(BaseModel):
    resume: ResumeRead
    profile_id: uuid.UUID
    #: True when an existing result for the same file + parser version was
    #: reused, so no new (paid) AI processing happened.
    reused: bool
    message: str


class ResumeIntelligenceRead(BaseModel):
    """Everything the Resume Intelligence UI needs in one response."""

    resume: ResumeRead
    #: The most recent processing attempt (may be a failure even when an
    #: older successful `profile` exists).
    latest_attempt: ProcessingAttemptRead | None
    is_processing: bool
    #: The current usable profile (needs_review or confirmed), if any.
    profile: ResumeProfileRead | None
