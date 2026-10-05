from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.profile import UserProfileUpdate


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


def update_profile(db: Session, user: User, data: UserProfileUpdate) -> UserProfile:
    profile = get_or_create_profile(db, user)

    update_data = data.model_dump(exclude_unset=True, exclude={"first_name", "last_name"})
    for field, value in update_data.items():
        setattr(profile, field, value)

    if data.first_name is not None:
        user.first_name = data.first_name
    if data.last_name is not None:
        user.last_name = data.last_name

    db.add(profile)
    db.add(user)
    db.commit()
    db.refresh(profile)
    db.refresh(user)
    return profile
