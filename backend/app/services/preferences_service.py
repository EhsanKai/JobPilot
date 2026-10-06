from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.deal_breaker import DealBreaker
from app.models.job_preferences import (
    JobPreferenceEmploymentType,
    JobPreferenceIndustry,
    JobPreferenceJobTitle,
    JobPreferenceLocation,
    JobPreferences,
    JobPreferenceWorkModel,
)
from app.models.user import User
from app.schemas.preferences import JobPreferencesUpdate

_LOAD_OPTIONS = (
    selectinload(JobPreferences.job_titles),
    selectinload(JobPreferences.locations),
    selectinload(JobPreferences.employment_types),
    selectinload(JobPreferences.industries),
    selectinload(JobPreferences.work_models),
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


def get_deal_breakers(db: Session, user: User) -> list[DealBreaker]:
    return list(
        db.execute(select(DealBreaker).where(DealBreaker.user_id == user.id)).scalars().all()
    )


def update_preferences(db: Session, user: User, data: JobPreferencesUpdate) -> JobPreferences:
    preferences = get_or_create_preferences(db, user)

    scalar_fields = {
        "minimum_salary",
        "desired_salary",
        "salary_currency",
        "relocation",
        "travel_willingness",
    }
    for field in scalar_fields:
        setattr(preferences, field, getattr(data, field))

    preferences.job_titles.clear()
    preferences.job_titles.extend(
        JobPreferenceJobTitle(title=item.title, priority=item.priority)
        for item in data.desired_job_titles
    )

    preferences.locations.clear()
    preferences.locations.extend(
        JobPreferenceLocation(location=item.location, radius_km=item.radius_km)
        for item in data.preferred_locations
    )

    preferences.employment_types.clear()
    preferences.employment_types.extend(
        JobPreferenceEmploymentType(employment_type=employment_type)
        for employment_type in data.employment_types
    )

    preferences.industries.clear()
    preferences.industries.extend(
        JobPreferenceIndustry(industry=industry) for industry in data.preferred_industries
    )

    preferences.work_models.clear()
    preferences.work_models.extend(
        JobPreferenceWorkModel(work_model=work_model) for work_model in data.work_models
    )

    for existing in get_deal_breakers(db, user):
        db.delete(existing)
    db.flush()
    for item in data.deal_breakers:
        db.add(DealBreaker(user_id=user.id, type=item.type, value=item.value, enabled=item.enabled))

    db.add(preferences)
    db.commit()
    db.refresh(preferences)
    return preferences
