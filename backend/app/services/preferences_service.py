from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.job_preferences import (
    JobPreferenceEmploymentType,
    JobPreferenceIndustry,
    JobPreferenceJobTitle,
    JobPreferenceLanguage,
    JobPreferenceLocation,
    JobPreferences,
)
from app.models.user import User
from app.schemas.preferences import JobPreferencesUpdate

_LOAD_OPTIONS = (
    selectinload(JobPreferences.job_titles),
    selectinload(JobPreferences.locations),
    selectinload(JobPreferences.employment_types),
    selectinload(JobPreferences.industries),
    selectinload(JobPreferences.languages),
)


def get_or_create_preferences(db: Session, user: User) -> JobPreferences:
    preferences = db.execute(
        select(JobPreferences).where(JobPreferences.user_id == user.id).options(*_LOAD_OPTIONS)
    ).scalar_one_or_none()
    if preferences is None:
        preferences = JobPreferences(user_id=user.id)
        db.add(preferences)
        db.commit()
        db.refresh(preferences)
    return preferences


def update_preferences(db: Session, user: User, data: JobPreferencesUpdate) -> JobPreferences:
    preferences = get_or_create_preferences(db, user)

    scalar_fields = {
        "minimum_salary",
        "desired_salary",
        "salary_currency",
        "seniority",
        "remote_preference",
        "relocation_willingness",
        "travel_willingness",
    }
    for field in scalar_fields:
        setattr(preferences, field, getattr(data, field))

    preferences.job_titles.clear()
    preferences.job_titles.extend(
        JobPreferenceJobTitle(title=title) for title in data.desired_job_titles
    )

    preferences.locations.clear()
    preferences.locations.extend(
        JobPreferenceLocation(location=location) for location in data.preferred_locations
    )

    preferences.employment_types.clear()
    preferences.employment_types.extend(
        JobPreferenceEmploymentType(employment_type=employment_type)
        for employment_type in data.employment_types
    )

    preferences.industries.clear()
    preferences.industries.extend(
        JobPreferenceIndustry(industry=industry) for industry in data.industries
    )

    preferences.languages.clear()
    preferences.languages.extend(
        JobPreferenceLanguage(language=lang.language, proficiency=lang.proficiency)
        for lang in data.languages
    )

    db.add(preferences)
    db.commit()
    db.refresh(preferences)
    return preferences
