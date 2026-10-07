from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.ai.provider import AIProvider
from app.ai.providers.disabled import DisabledByUserAIProvider
from app.core.errors import UnauthorizedError
from app.core.security import TokenClaims, decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service
from app.services.resume_intelligence.parser import AIResumeParser, ResumeParser

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_token_claims(token: str | None = Depends(_oauth2_scheme)) -> TokenClaims:
    if token is None:
        raise UnauthorizedError("Not authenticated.")
    claims = decode_access_token(token)
    if claims is None:
        raise UnauthorizedError("Invalid or expired token.")
    return claims


def get_current_user(
    claims: TokenClaims = Depends(get_token_claims),
    db: Session = Depends(get_db),
) -> User:
    """The signed-in user: a valid token whose server-side session is still open."""
    return auth_service.resolve_session(db, claims)


def get_user_ai_provider(
    provider: AIProvider = Depends(get_ai_provider),
    current_user: User = Depends(get_current_user),
) -> AIProvider:
    """The AI provider for this request -- unless the user switched external AI processing
    off (Settings > Privacy): then every AI call fails like an unconfigured provider, so
    nothing is sent and the deterministic paths keep working. Enforced here, server-side,
    for every AI feature."""
    if not current_user.external_ai_processing_allowed:
        return DisabledByUserAIProvider()
    return provider


def get_resume_parser(provider: AIProvider = Depends(get_user_ai_provider)) -> ResumeParser:
    return AIResumeParser(provider)
