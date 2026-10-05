from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.job_preferences import JobPreferences
from app.models.user import User
from app.schemas.preferences import JobPreferencesRead, JobPreferencesUpdate, LanguagePreference
from app.services import preferences_service

router = APIRouter(prefix="/preferences", tags=["preferences"])


def _serialize(preferences: JobPreferences) -> JobPreferencesRead:
    return JobPreferencesRead(
        id=preferences.id,
        user_id=preferences.user_id,
        desired_job_titles=[jt.title for jt in preferences.job_titles],
        preferred_locations=[loc.location for loc in preferences.locations],
        employment_types=[et.employment_type for et in preferences.employment_types],
        industries=[ind.industry for ind in preferences.industries],
        languages=[
            LanguagePreference(language=lang.language, proficiency=lang.proficiency)
            for lang in preferences.languages
        ],
        minimum_salary=preferences.minimum_salary,
        desired_salary=preferences.desired_salary,
        salary_currency=preferences.salary_currency,
        seniority=preferences.seniority,
        remote_preference=preferences.remote_preference,
        relocation_willingness=preferences.relocation_willingness,
        travel_willingness=preferences.travel_willingness,
        created_at=preferences.created_at,
        updated_at=preferences.updated_at,
    )


@router.get("", response_model=JobPreferencesRead)
def get_preferences(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> JobPreferencesRead:
    preferences = preferences_service.get_or_create_preferences(db, current_user)
    return _serialize(preferences)


@router.put("", response_model=JobPreferencesRead)
def update_preferences(
    data: JobPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobPreferencesRead:
    preferences = preferences_service.update_preferences(db, current_user, data)
    return _serialize(preferences)
