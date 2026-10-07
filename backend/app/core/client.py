"""Who is calling: the client address used for rate limiting (V0.13)."""

from fastapi import Request

from app.core.config import settings


def client_address(request: Request) -> str | None:
    """The peer address -- or, only when TRUST_PROXY_HEADERS is set (the API runs behind
    a reverse proxy you control), the left-most X-Forwarded-For entry. Never trusted by
    default: anyone can send that header."""
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    return request.client.host if request.client else None
