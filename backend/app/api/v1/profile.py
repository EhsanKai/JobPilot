from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.profile import ProfileWithUserRead, UserProfileRead, UserProfileUpdate
from app.services import profile_service

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=ProfileWithUserRead)
def get_profile(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProfileWithUserRead:
    profile = profile_service.get_or_create_profile(db, current_user)
    return ProfileWithUserRead(
        id=current_user.id,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        profile=UserProfileRead.model_validate(profile),
    )


@router.put("", response_model=ProfileWithUserRead)
def update_profile(
    data: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileWithUserRead:
    profile = profile_service.update_profile(db, current_user, data)
    return ProfileWithUserRead(
        id=current_user.id,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        profile=UserProfileRead.model_validate(profile),
    )
