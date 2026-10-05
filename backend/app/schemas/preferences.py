import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.deal_breaker import DealBreakerType
from app.models.job_preferences import (
    EmploymentTypeValue,
    JobTitlePriority,
    RelocationPreference,
    RemotePreference,
    TravelWillingness,
)


class DesiredJobTitleInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    priority: JobTitlePriority = JobTitlePriority.SECONDARY


class DesiredJobTitleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    priority: JobTitlePriority


class PreferredLocationInput(BaseModel):
    location: str = Field(min_length=1, max_length=255)
    # Reserved for a future version; not used for matching yet.
    radius_km: int | None = Field(default=None, ge=0, le=20000)


class PreferredLocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    location: str
    radius_km: int | None


class DealBreakerInput(BaseModel):
    type: DealBreakerType
    value: str | None = Field(default=None, max_length=255)
    enabled: bool = True


class DealBreakerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: DealBreakerType
    value: str | None
    enabled: bool


class JobPreferencesUpdate(BaseModel):
    desired_job_titles: list[DesiredJobTitleInput] = Field(default_factory=list)
    preferred_locations: list[PreferredLocationInput] = Field(default_factory=list)
    employment_types: list[EmploymentTypeValue] = Field(default_factory=list)
    preferred_industries: list[str] = Field(default_factory=list)
    work_models: list[RemotePreference] = Field(default_factory=list)

    minimum_salary: Decimal | None = Field(default=None, ge=0)
    desired_salary: Decimal | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    relocation: RelocationPreference | None = None
    travel_willingness: TravelWillingness | None = None

    deal_breakers: list[DealBreakerInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_salary_range(self) -> "JobPreferencesUpdate":
        if (
            self.minimum_salary is not None
            and self.desired_salary is not None
            and self.desired_salary < self.minimum_salary
        ):
            raise ValueError("desired_salary must be greater than or equal to minimum_salary")
        return self


class JobPreferencesRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID

    desired_job_titles: list[DesiredJobTitleRead]
    preferred_locations: list[PreferredLocationRead]
    employment_types: list[EmploymentTypeValue]
    preferred_industries: list[str]
    work_models: list[RemotePreference]

    minimum_salary: Decimal | None
    desired_salary: Decimal | None
    salary_currency: str | None
    relocation: RelocationPreference | None
    travel_willingness: TravelWillingness | None

    deal_breakers: list[DealBreakerRead]

    created_at: datetime
    updated_at: datetime
