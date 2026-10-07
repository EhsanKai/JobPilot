import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

# bcrypt's underlying algorithm only uses the first 72 bytes of the input. New passwords
# longer than that are refused at registration (V0.13) instead of being silently cut.
MAX_PASSWORD_BYTES = 72

# A real bcrypt hash of a random throwaway value: verified against when the account does
# not exist, so a failed sign-in takes the same time whether or not the e-mail is known.
_DUMMY_HASH = bcrypt.hashpw(uuid.uuid4().bytes, bcrypt.gensalt()).decode("utf-8")


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")[:MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    password_bytes = plain_password.encode("utf-8")[:MAX_PASSWORD_BYTES]
    return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))


def burn_password_check(plain_password: str) -> None:
    """Spend the time of one password verification without an account (anti-enumeration)."""
    verify_password(plain_password, _DUMMY_HASH)


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID


def create_access_token(
    subject: str, session_id: uuid.UUID, expires_at: datetime | None = None
) -> str:
    now = datetime.now(UTC)
    expire = expires_at or now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": subject,
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> TokenClaims | None:
    """The claims of a correctly signed, unexpired token -- or None. Whether its session is
    still open is checked against the database by the caller."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
    subject, session = payload.get("sub"), payload.get("sid")
    if not isinstance(subject, str) or not isinstance(session, str):
        return None
    try:
        return TokenClaims(user_id=uuid.UUID(subject), session_id=uuid.UUID(session))
    except ValueError:
        return None
