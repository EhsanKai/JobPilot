import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.resume import ResumeStatus


class ResumeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    file_name: str
    mime_type: str
    file_size: int
    status: ResumeStatus
    created_at: datetime
    updated_at: datetime
