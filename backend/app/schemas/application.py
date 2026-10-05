import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.application import ApplicationStatus


class ApplicationCreate(BaseModel):
    job_id: uuid.UUID | None = None
    salary_expectation: Decimal | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    start_date: date | None = None


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    salary_expectation: Decimal | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    start_date: date | None = None


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    job_id: uuid.UUID | None
    status: ApplicationStatus
    salary_expectation: Decimal | None
    salary_currency: str | None
    start_date: date | None
    created_at: datetime
    updated_at: datetime
