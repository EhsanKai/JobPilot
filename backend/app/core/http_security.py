"""HTTP response hardening for the JSON API (V0.13).

Every API response is data for the JobPilot frontend, never a page to render, so the
headers are strict: nothing may be framed, sniffed into another content type, or loaded
by it. Responses carrying personal data are not cached. The interactive docs (/docs,
/redoc -- development only by default) load Swagger/ReDoc assets from a CDN and are left
without the CSP.
"""

from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_CONTENT_SECURITY_POLICY = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), "
    "microphone=(), payment=(), usb=()"
)
HSTS_VALUE = "max-age=31536000; includeSubDomains"
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def security_headers(*, path: str, hsts: bool) -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", PERMISSIONS_POLICY.encode()),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]
    if not path.startswith(_DOCS_PATHS):
        headers.append((b"content-security-policy", API_CONTENT_SECURITY_POLICY.encode()))
    if path.startswith("/api/"):
        # Profiles, CVs, answers, screenshots: never kept by browsers or shared caches.
        headers.append((b"cache-control", b"no-store"))
    if hsts:
        headers.append((b"strict-transport-security", HSTS_VALUE.encode()))
    return headers


class SecurityHeadersMiddleware:
    """Adds the security headers to every HTTP response that does not already set them."""

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = security_headers(path=scope.get("path", ""), hsts=self.hsts)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (name, value) for name, value in extra if name not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)


def header_names(headers: Iterable[tuple[bytes, bytes]]) -> set[str]:
    return {name.decode() for name, _ in headers}


class BodySizeLimitMiddleware:
    """Refuses request bodies over `max_bytes` with 413 before the application reads them
    (V0.13): an upload can no longer make the API buffer an arbitrarily large body. A
    declared Content-Length is checked up front; a chunked body is counted as it streams."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > self.max_bytes:
                    await _send_too_large(send, self.max_bytes)
                    return
        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    # Tell the application the client went away: it stops reading.
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if exceeded and not response_started:
                return  # the 413 below replaces whatever the application answers
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not response_started:
            await _send_too_large(send, self.max_bytes)


async def _send_too_large(send: Send, max_bytes: int) -> None:
    body = (
        b'{"error":{"code":"PAYLOAD_TOO_LARGE","message":"The request is too large.",'
        b'"details":{"max_bytes":' + str(max_bytes).encode() + b"}}}"
    )
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
