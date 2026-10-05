from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.job_preferences import (
    JobPreferenceJobTitle,
    JobPreferenceLocation,
    JobPreferences,
    JobPreferenceWorkModel,
)
from app.models.language import UserLanguage
from app.models.professional_profile import ProfessionalProfile
from app.models.skill import UserSkill
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.completeness import CompletenessCategory, ProfileCompleteness

# Eight categories, weighted equally. Each is independently documented in
# docs/architecture.md so the weighting can be revisited deliberately rather
# than drifting as fields are added.
_CATEGORY_WEIGHT = 100 / 8


def _personal_information_score(profile: UserProfile | None) -> float:
    if profile is None:
        return 0.0
    fields = [profile.phone, profile.location, profile.linkedin_url]
    filled = sum(1 for f in fields if f)
    return filled / len(fields)


def _professional_information_score(professional_profile: ProfessionalProfile | None) -> float:
    if professional_profile is None:
        return 0.0
    fields = [
        professional_profile.current_job_title,
        professional_profile.years_of_experience,
        professional_profile.seniority_level,
    ]
    filled = sum(1 for f in fields if f is not None)
    return filled / len(fields)


def _salary_score(preferences: JobPreferences | None) -> float:
    if preferences is None:
        return 0.0
    has_min = preferences.minimum_salary is not None
    has_desired = preferences.desired_salary is not None
    if has_min and has_desired:
        return 1.0
    if has_min or has_desired:
        return 0.5
    return 0.0


def calculate_completeness(db: Session, user: User) -> ProfileCompleteness:
    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).scalar_one_or_none()
    professional_profile = db.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    ).scalar_one_or_none()
    preferences = db.execute(
        select(JobPreferences).where(JobPreferences.user_id == user.id)
    ).scalar_one_or_none()

    skill_count = db.execute(
        select(func.count()).select_from(UserSkill).where(UserSkill.user_id == user.id)
    ).scalar_one()
    language_count = db.execute(
        select(func.count()).select_from(UserLanguage).where(UserLanguage.user_id == user.id)
    ).scalar_one()

    job_title_count = 0
    location_count = 0
    work_model_count = 0
    if preferences is not None:
        job_title_count = db.execute(
            select(func.count())
            .select_from(JobPreferenceJobTitle)
            .where(JobPreferenceJobTitle.job_preferences_id == preferences.id)
        ).scalar_one()
        location_count = db.execute(
            select(func.count())
            .select_from(JobPreferenceLocation)
            .where(JobPreferenceLocation.job_preferences_id == preferences.id)
        ).scalar_one()
        work_model_count = db.execute(
            select(func.count())
            .select_from(JobPreferenceWorkModel)
            .where(JobPreferenceWorkModel.job_preferences_id == preferences.id)
        ).scalar_one()

    categories = [
        CompletenessCategory(
            label="Personal information",
            weight=_CATEGORY_WEIGHT,
            score=_personal_information_score(profile),
        ),
        CompletenessCategory(
            label="Professional information",
            weight=_CATEGORY_WEIGHT,
            score=_professional_information_score(professional_profile),
        ),
        CompletenessCategory(
            label="Skills", weight=_CATEGORY_WEIGHT, score=1.0 if skill_count > 0 else 0.0
        ),
        CompletenessCategory(
            label="Languages", weight=_CATEGORY_WEIGHT, score=1.0 if language_count > 0 else 0.0
        ),
        CompletenessCategory(
            label="Job titles", weight=_CATEGORY_WEIGHT, score=1.0 if job_title_count > 0 else 0.0
        ),
        CompletenessCategory(
            label="Locations", weight=_CATEGORY_WEIGHT, score=1.0 if location_count > 0 else 0.0
        ),
        CompletenessCategory(
            label="Salary", weight=_CATEGORY_WEIGHT, score=_salary_score(preferences)
        ),
        CompletenessCategory(
            label="Work model", weight=_CATEGORY_WEIGHT, score=1.0 if work_model_count > 0 else 0.0
        ),
    ]

    percentage = round(sum(c.weight * c.score for c in categories))

    if percentage >= 90:
        message = "Your profile looks great. You're ready to start searching for jobs."
    elif percentage >= 60:
        message = "You're almost ready to start searching for jobs."
    elif percentage >= 30:
        message = "Keep going -- add a few more details to strengthen your profile."
    else:
        message = "Let's get your profile started."

    return ProfileCompleteness(percentage=percentage, categories=categories, message=message)
