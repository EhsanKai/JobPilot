import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models.deal_breaker import DealBreaker
from app.models.job_preferences import JobPreferenceJobTitle, JobPreferenceLocation, JobPreferences
from app.models.user import User
from app.schemas.preferences import (
    DealBreakerRead,
    DesiredJobTitleInput,
    DesiredJobTitleRead,
    JobPreferencesRead,
    JobPreferencesUpdate,
    PreferredLocationInput,
    PreferredLocationRead,
)
from app.services import preferences_service

router = APIRouter(prefix="/preferences", tags=["preferences"])


def _serialize(preferences: JobPreferences, deal_breakers: list[DealBreaker]) -> JobPreferencesRead:
    return JobPreferencesRead(
        id=preferences.id,
        user_id=preferences.user_id,
        desired_job_titles=[
            DesiredJobTitleRead.model_validate(jt) for jt in preferences.job_titles
        ],
        preferred_locations=[
            PreferredLocationRead.model_validate(loc) for loc in preferences.locations
        ],
        employment_types=[et.employment_type for et in preferences.employment_types],
        preferred_industries=[ind.industry for ind in preferences.industries],
        work_models=[wm.work_model for wm in preferences.work_models],
        minimum_salary=preferences.minimum_salary,
        desired_salary=preferences.desired_salary,
        salary_currency=preferences.salary_currency,
        relocation=preferences.relocation,
        travel_willingness=preferences.travel_willingness,
        deal_breakers=[DealBreakerRead.model_validate(db_item) for db_item in deal_breakers],
        created_at=preferences.created_at,
        updated_at=preferences.updated_at,
    )


@router.get("", response_model=JobPreferencesRead)
def get_preferences(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> JobPreferencesRead:
    preferences = preferences_service.get_or_create_preferences(db, current_user)
    deal_breakers = preferences_service.get_deal_breakers(db, current_user)
    return _serialize(preferences, deal_breakers)


@router.put("", response_model=JobPreferencesRead)
def update_preferences(
    data: JobPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobPreferencesRead:
    preferences = preferences_service.update_preferences(db, current_user, data)
    deal_breakers = preferences_service.get_deal_breakers(db, current_user)
    return _serialize(preferences, deal_breakers)


@router.get("/job-titles", response_model=list[DesiredJobTitleRead])
def list_job_titles(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[DesiredJobTitleRead]:
    preferences = preferences_service.get_or_create_preferences(db, current_user)
    return [DesiredJobTitleRead.model_validate(jt) for jt in preferences.job_titles]


@router.post(
    "/job-titles", response_model=DesiredJobTitleRead, status_code=status.HTTP_201_CREATED
)
def add_job_title(
    data: DesiredJobTitleInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DesiredJobTitleRead:
    preferences = preferences_service.get_or_create_preferences(db, current_user)
    job_title = JobPreferenceJobTitle(
        job_preferences_id=preferences.id, title=data.title, priority=data.priority
    )
    db.add(job_title)
    db.commit()
    db.refresh(job_title)
    return DesiredJobTitleRead.model_validate(job_title)


@router.delete("/job-titles/{job_title_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job_title(
    job_title_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    preferences = preferences_service.get_or_create_preferences(db, current_user)
    job_title = db.execute(
        select(JobPreferenceJobTitle).where(
            JobPreferenceJobTitle.id == job_title_id,
            JobPreferenceJobTitle.job_preferences_id == preferences.id,
        )
    ).scalar_one_or_none()
    if job_title is None:
        raise NotFoundError("Desired job title not found.")
    db.delete(job_title)
    db.commit()


@router.get("/locations", response_model=list[PreferredLocationRead])
def list_locations(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[PreferredLocationRead]:
    preferences = preferences_service.get_or_create_preferences(db, current_user)
    return [PreferredLocationRead.model_validate(loc) for loc in preferences.locations]


@router.post(
    "/locations", response_model=PreferredLocationRead, status_code=status.HTTP_201_CREATED
)
def add_location(
    data: PreferredLocationInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PreferredLocationRead:
    preferences = preferences_service.get_or_create_preferences(db, current_user)
    location = JobPreferenceLocation(
        job_preferences_id=preferences.id, location=data.location, radius_km=data.radius_km
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    return PreferredLocationRead.model_validate(location)


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(
    location_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    preferences = preferences_service.get_or_create_preferences(db, current_user)
    location = db.execute(
        select(JobPreferenceLocation).where(
            JobPreferenceLocation.id == location_id,
            JobPreferenceLocation.job_preferences_id == preferences.id,
        )
    ).scalar_one_or_none()
    if location is None:
        raise NotFoundError("Preferred location not found.")
    db.delete(location)
    db.commit()
