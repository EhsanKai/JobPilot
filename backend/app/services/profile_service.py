from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.industry import Industry, UserIndustry
from app.models.professional_profile import ProfessionalProfile
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.profile import ProfileWithUserRead, UserProfileUpdate


def get_or_create_profile(db: Session, user: User) -> UserProfile:
    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def get_or_create_professional_profile(db: Session, user: User) -> ProfessionalProfile:
    professional_profile = db.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    ).scalar_one_or_none()
    if professional_profile is None:
        professional_profile = ProfessionalProfile(user_id=user.id)
        db.add(professional_profile)
        db.commit()
        db.refresh(professional_profile)
    return professional_profile


def get_industry_names(db: Session, user: User) -> list[str]:
    rows = db.execute(
        select(Industry.name)
        .join(UserIndustry, UserIndustry.industry_id == Industry.id)
        .where(UserIndustry.user_id == user.id)
        .order_by(Industry.name)
    ).scalars().all()
    return list(rows)


def _get_or_create_industry(db: Session, name: str) -> Industry:
    industry = db.execute(select(Industry).where(Industry.name == name)).scalar_one_or_none()
    if industry is None:
        industry = Industry(name=name)
        db.add(industry)
        db.flush()
    return industry


def _set_industries(db: Session, user: User, names: list[str]) -> None:
    existing = db.execute(
        select(UserIndustry).where(UserIndustry.user_id == user.id)
    ).scalars().all()
    for row in existing:
        db.delete(row)
    db.flush()

    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        industry = _get_or_create_industry(db, name)
        db.add(UserIndustry(user_id=user.id, industry_id=industry.id))


def build_profile_response(db: Session, user: User) -> ProfileWithUserRead:
    profile = get_or_create_profile(db, user)
    professional_profile = get_or_create_professional_profile(db, user)
    industries = get_industry_names(db, user)

    return ProfileWithUserRead(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=profile.phone,
        location=profile.location,
        linkedin_url=profile.linkedin_url,
        current_job_title=professional_profile.current_job_title,
        years_of_experience=professional_profile.years_of_experience,
        seniority_level=professional_profile.seniority_level,
        industries=industries,
    )


def update_profile(db: Session, user: User, data: UserProfileUpdate) -> ProfileWithUserRead:
    profile = get_or_create_profile(db, user)
    professional_profile = get_or_create_professional_profile(db, user)

    if data.first_name is not None:
        user.first_name = data.first_name
    if data.last_name is not None:
        user.last_name = data.last_name

    personal_fields = {"phone", "location", "linkedin_url"}
    for field in personal_fields:
        setattr(profile, field, getattr(data, field))

    professional_fields = {"current_job_title", "years_of_experience", "seniority_level"}
    for field in professional_fields:
        setattr(professional_profile, field, getattr(data, field))

    _set_industries(db, user, data.industries)

    db.add_all([user, profile, professional_profile])
    db.commit()

    return build_profile_response(db, user)
