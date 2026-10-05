import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.job_preferences import (
    EmploymentTypeValue,
    RemotePreference,
    SeniorityLevel,
    TravelWillingness,
)


class LanguagePreference(BaseModel):
    language: str = Field(min_length=1, max_length=100)
    proficiency: str | None = Field(default=None, max_length=50)


class JobPreferencesBase(BaseModel):
    desired_job_titles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    employment_types: list[EmploymentTypeValue] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    languages: list[LanguagePreference] = Field(default_factory=list)

    minimum_salary: Decimal | None = Field(default=None, ge=0)
    desired_salary: Decimal | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    seniority: SeniorityLevel | None = None
    remote_preference: RemotePreference | None = None
    relocation_willingness: bool = False
    travel_willingness: TravelWillingness | None = None


class JobPreferencesUpdate(JobPreferencesBase):
    pass


class JobPreferencesRead(JobPreferencesBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
