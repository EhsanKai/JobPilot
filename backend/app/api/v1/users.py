from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.user import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def get_current_user_info(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)
