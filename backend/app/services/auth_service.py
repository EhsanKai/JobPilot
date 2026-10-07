"""Registration, sign-in and sessions.

V0.13 hardening:
- every sign-in is a server-side `AuthSession`; sign-out revokes it, so a copied token
  stops working at once (not only when it expires);
- failed sign-ins are throttled per account and per client address (HTTP 429);
- an unknown e-mail costs the same password-hash time as a wrong password, and both get
  the same message (no account enumeration through sign-in);
- passwords longer than bcrypt's 72 bytes are refused at registration instead of being
  silently truncated;
- nothing here logs an e-mail address, a password or a token.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ConflictError, TooManyRequestsError, UnauthorizedError
from app.core.logging import get_logger
from app.core.rate_limit import auth_limiter
from app.core.security import (
    TokenClaims,
    burn_password_check,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.privacy import AuthSession
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest

logger = get_logger(__name__)

TOO_MANY_ATTEMPTS_MESSAGE = (
    "Too many unsuccessful sign-in attempts. Please wait a few minutes and try again."
)


def _now() -> datetime:
    return datetime.now(UTC)


def _account_key(email: str) -> str:
    # The limiter never holds e-mail addresses in clear text.
    return "login:account:" + hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _client_key(scope: str, client: str | None) -> str:
    return f"{scope}:client:{client or 'unknown'}"


def register_user(db: Session, data: RegisterRequest, *, client: str | None = None) -> User:
    window = settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    client_key = _client_key("register", client)
    wait = auth_limiter.retry_after(
        client_key, limit=settings.REGISTRATION_MAX_PER_IP, window_seconds=window
    )
    if wait is not None:
        logger.warning("event=registration_throttled")
        raise TooManyRequestsError(
            "Too many accounts were created from this network. Please try again later.", wait
        )
    auth_limiter.hit(client_key, window_seconds=window)

    existing = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("An account with this email already exists.")

    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        first_name=data.first_name,
        last_name=data.last_name,
        external_ai_processing_allowed=settings.AI_PROCESSING_DEFAULT_ALLOWED,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("event=user_registered user_id=%s", user.id)
    return user


def authenticate_user(db: Session, data: LoginRequest, *, client: str | None = None) -> User:
    window = settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    account_key = _account_key(data.email)
    client_key = _client_key("login", client)
    wait = auth_limiter.retry_after(
        account_key, limit=settings.LOGIN_MAX_FAILED_ATTEMPTS, window_seconds=window
    ) or auth_limiter.retry_after(
        client_key, limit=settings.LOGIN_MAX_ATTEMPTS_PER_IP, window_seconds=window
    )
    if wait is not None:
        logger.warning("event=login_throttled")
        raise TooManyRequestsError(TOO_MANY_ATTEMPTS_MESSAGE, wait)

    user = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if user is None:
        burn_password_check(data.password)
        valid = False
    else:
        valid = verify_password(data.password, user.password_hash)
    if user is None or not valid:
        auth_limiter.hit(account_key, window_seconds=window)
        auth_limiter.hit(client_key, window_seconds=window)
        logger.info("event=login_failed")
        raise UnauthorizedError("Invalid email or password.")

    auth_limiter.clear(account_key)
    return user


@dataclass(frozen=True)
class IssuedToken:
    token: str
    session: AuthSession


def start_session(db: Session, user: User) -> IssuedToken:
    """A new sign-in session and its access token. Commits. Also clears the user's
    sessions that have expired or were revoked, so the table does not grow forever."""
    now = _now()
    db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id,
            (AuthSession.expires_at <= now) | AuthSession.revoked_at.is_not(None),
        )
    )
    session = AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        expires_at=now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    db.add(session)
    db.commit()
    token = create_access_token(str(user.id), session.id, expires_at=session.expires_at)
    logger.info("event=session_started user_id=%s", user.id)
    return IssuedToken(token=token, session=session)


def resolve_session(db: Session, claims: TokenClaims) -> User:
    """The user of an open session, or UnauthorizedError."""
    row = db.execute(
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            AuthSession.id == claims.session_id,
            AuthSession.user_id == claims.user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > _now(),
        )
    ).scalar_one_or_none()
    if row is None:
        raise UnauthorizedError("Your session has ended. Please sign in again.")
    return row


def revoke_session(db: Session, claims: TokenClaims) -> None:
    db.execute(
        update(AuthSession)
        .where(
            AuthSession.id == claims.session_id,
            AuthSession.user_id == claims.user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    db.commit()
    logger.info("event=session_revoked user_id=%s", claims.user_id)


def revoke_all_sessions(db: Session, user: User) -> int:
    result = db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    db.commit()
    count = int(getattr(result, "rowcount", 0) or 0)
    logger.info("event=sessions_revoked_all user_id=%s count=%d", user.id, count)
    return count
