import uuid
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from app.models.professional_profile import SeniorityLevel


def _validate_linkedin_url(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("linkedin_url must be a valid absolute URL (e.g. https://...)")
    return value


class UserProfileUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)

    # Personal profile
    phone: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=255)
    linkedin_url: str | None = Field(default=None, max_length=500)

    # Professional profile
    current_job_title: str | None = Field(default=None, max_length=255)
    years_of_experience: int | None = Field(default=None, ge=0, le=80)
    seniority_level: SeniorityLevel | None = None
    industries: list[str] = Field(default_factory=list)

    @field_validator("linkedin_url")
    @classmethod
    def validate_linkedin_url(cls, value: str | None) -> str | None:
        return _validate_linkedin_url(value)


class ProfileWithUserRead(BaseModel):
    """Combined, flat view of a user's account, personal, and professional
    profile fields. The underlying data is normalized across `users`,
    `user_profiles`, `professional_profiles`, and `user_industries`, but the
    API exposes it as one object for a simpler frontend integration -- the
    same convention `/preferences` already uses for its list fields."""

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str

    phone: str | None
    location: str | None
    linkedin_url: str | None

    current_job_title: str | None
    years_of_experience: int | None
    seniority_level: SeniorityLevel | None
    industries: list[str]
