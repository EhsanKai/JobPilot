from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class AIError(Exception):
    """Base class for AI provider failures.

    `code` and `user_message` are safe to show to end users. `str(exc)` is a
    short technical reason for logs -- it must never contain prompt or
    document content, API keys, or raw provider responses.
    """

    code = "AI_ERROR"
    user_message = "The AI service failed to process your request. Please try again later."


class AINotConfiguredError(AIError):
    code = "AI_NOT_CONFIGURED"
    user_message = (
        "Resume analysis is not available because no AI provider is configured. "
        "Please contact the administrator."
    )


class AIAuthenticationError(AIError):
    code = "AI_AUTH_FAILED"
    user_message = (
        "The AI service rejected JobPilot's credentials. Please contact the administrator."
    )


class AIRateLimitError(AIError):
    code = "AI_RATE_LIMITED"
    user_message = "The AI service is receiving too many requests. Please try again in a minute."


class AITimeoutError(AIError):
    code = "AI_TIMEOUT"
    user_message = "The AI service took too long to respond. Please try again."


class AIProviderUnavailableError(AIError):
    code = "AI_UNAVAILABLE"
    user_message = "The AI service is temporarily unavailable. Please try again later."


class AIContextLimitError(AIError):
    code = "AI_CONTEXT_LIMIT"
    user_message = (
        "This resume is too long for the AI service to analyze in one pass. "
        "Please upload a shorter version."
    )


class AIEmptyResponseError(AIError):
    code = "AI_EMPTY_RESPONSE"
    user_message = "The AI service returned an empty result. Please try again."


class AIInvalidResponseError(AIError):
    code = "AI_INVALID_RESPONSE"
    user_message = (
        "The AI service returned a result that didn't pass validation, so nothing was saved. "
        "Please try again."
    )


class AIProvider(ABC):
    """Contract every AI provider implementation must satisfy.

    `generate_structured` must return an instance of `schema` validated by
    Pydantic, or raise an `AIError` subclass -- never free-form prose and
    never an unvalidated dict.
    """

    #: Short identifier stored with results for provenance, e.g. "anthropic".
    name: str = "unknown"
    #: Model identifier stored with results for provenance.
    model: str | None = None

    @abstractmethod
    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
    ) -> T:
        raise NotImplementedError


def summarize_validation_error(exc: Exception) -> str:
    """Log-safe summary of a pydantic ValidationError.

    `str(ValidationError)` embeds the offending *input values* -- i.e. resume
    content -- so it must never be logged directly. This keeps only error
    locations and types.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return type(exc).__name__
    parts = []
    for error in errors(include_input=False, include_url=False)[:10]:
        loc = ".".join(str(part) for part in error.get("loc", ()))
        parts.append(f"{loc}:{error.get('type')}")
    return f"{len(errors())} validation error(s): " + ", ".join(parts)
