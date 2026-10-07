from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.client import client_address
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserRead
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_optional_token = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    data: RegisterRequest, request: Request, db: Session = Depends(get_db)
) -> TokenResponse:
    user = auth_service.register_user(db, data, client=client_address(request))
    issued = auth_service.start_session(db, user)
    return TokenResponse(access_token=issued.token, user=UserRead.model_validate(user))


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    user = auth_service.authenticate_user(db, data, client=client_address(request))
    issued = auth_service.start_session(db, user)
    return TokenResponse(access_token=issued.token, user=UserRead.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: str | None = Depends(_optional_token), db: Session = Depends(get_db)) -> None:
    """End this sign-in on the server: the token stops working immediately. Idempotent --
    an expired, revoked or missing token is simply nothing left to end."""
    claims = decode_access_token(token) if token else None
    if claims is not None:
        auth_service.revoke_session(db, claims)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """End every sign-in of this account, on every device (including this one)."""
    auth_service.revoke_all_sessions(db, current_user)
