from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for application errors that map to a consistent API error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.headers = headers
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(
        self, message: str = "Resource not found", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__("NOT_FOUND", message, status.HTTP_404_NOT_FOUND, details)


class UnauthorizedError(AppError):
    def __init__(
        self, message: str = "Not authenticated", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__("UNAUTHORIZED", message, status.HTTP_401_UNAUTHORIZED, details)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Forbidden", details: dict[str, Any] | None = None) -> None:
        super().__init__("FORBIDDEN", message, status.HTTP_403_FORBIDDEN, details)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflict", details: dict[str, Any] | None = None) -> None:
        super().__init__("CONFLICT", message, status.HTTP_409_CONFLICT, details)


class TooManyRequestsError(AppError):
    def __init__(self, message: str, retry_after_seconds: float) -> None:
        retry_after = max(1, int(retry_after_seconds + 0.999))
        super().__init__(
            "TOO_MANY_ATTEMPTS",
            message,
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )


class ValidationAppError(AppError):
    def __init__(
        self, message: str = "Invalid request", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__("VALIDATION_ERROR", message, status.HTTP_422_UNPROCESSABLE_ENTITY, details)


def _error_response(
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc.code, exc.message, exc.status_code, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        # Surface the first field error's own message (e.g. "desired_salary
        # must be greater than or equal to minimum_salary") instead of a
        # generic string, so the frontend can show the user something useful.
        # Pydantic prefixes custom validator messages with "Value error, ".
        message = (
            errors[0]["msg"].removeprefix("Value error, ") if errors else "Invalid request data"
        )
        # Never echo the submitted value back (V0.13): it may be a password, a CV or an
        # answer. Location, type and message are enough for the frontend.
        safe = [
            {key: value for key, value in error.items() if key not in ("input", "url", "ctx")}
            for error in errors
        ]
        return _error_response(
            "VALIDATION_ERROR",
            message,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"errors": jsonable_encoder(safe)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(
            "HTTP_ERROR",
            str(exc.detail),
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled exception while processing %s %s", request.method, request.url.path
        )
        return _error_response(
            "INTERNAL_ERROR",
            "An unexpected error occurred. Please try again later.",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
