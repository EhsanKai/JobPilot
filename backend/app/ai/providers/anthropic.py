"""Anthropic (Claude) implementation of `AIProvider`.

The only module in the codebase that imports the Anthropic SDK. Uses the
Messages API's native structured outputs (`output_config.format` with a JSON
schema derived from the Pydantic model), then re-validates the returned JSON
with Pydantic before handing it back -- constrained decoding is a strong
guarantee, but the application still never trusts unvalidated output.
"""

from typing import Any

import anthropic
from pydantic import ValidationError

from app.ai.provider import (
    AIAuthenticationError,
    AIContextLimitError,
    AIEmptyResponseError,
    AIError,
    AIInvalidResponseError,
    AINotConfiguredError,
    AIProvider,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
    T,
    summarize_validation_error,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_CONTEXT_LIMIT_MARKERS = (
    "prompt is too long",
    "context window",
    "context length",
    "too many tokens",
)


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        max_output_tokens: int = 16000,
        client: Any | None = None,
    ) -> None:
        if not api_key and client is None:
            raise AINotConfiguredError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self._model_id: str = model
        self._max_output_tokens = max_output_tokens
        # The SDK retries connection errors, 408/409/429 and 5xx with backoff.
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
        try:
            response = await self._client.messages.create(
                model=self._model_id,
                max_tokens=self._max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": anthropic.transform_schema(schema),
                    }
                },
            )
        except AIError:
            raise
        except Exception as exc:  # mapped below; never re-raised raw
            raise _map_exception(exc) from None

        if getattr(response, "stop_reason", None) == "max_tokens":
            raise AIInvalidResponseError("structured output truncated at max_tokens")
        if getattr(response, "stop_reason", None) == "refusal":
            raise AIInvalidResponseError("model refused the request")

        text = "".join(
            getattr(block, "text", "")
            for block in getattr(response, "content", None) or []
            if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            raise AIEmptyResponseError("no text content in response")

        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise AIInvalidResponseError(summarize_validation_error(exc)) from None


def _map_exception(exc: Exception) -> AIError:
    """Translate SDK exceptions into the provider-agnostic hierarchy.

    Messages are limited to exception type and HTTP status: SDK error bodies
    are not propagated, so nothing provider-internal reaches users or logs.
    """
    status = getattr(exc, "status_code", None)
    reason = f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")

    if isinstance(exc, anthropic.APITimeoutError):
        return AITimeoutError(reason)
    if isinstance(exc, anthropic.APIConnectionError):
        return AIProviderUnavailableError(reason)
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return AIAuthenticationError(reason)
    if isinstance(exc, anthropic.RateLimitError):
        return AIRateLimitError(reason)
    if isinstance(exc, anthropic.RequestTooLargeError):
        return AIContextLimitError(reason)
    if isinstance(exc, anthropic.BadRequestError):
        message = str(getattr(exc, "message", "")).lower()
        if any(marker in message for marker in _CONTEXT_LIMIT_MARKERS):
            return AIContextLimitError(reason)
        return AIError(reason)
    if isinstance(exc, anthropic.APIStatusError) and status is not None and status >= 500:
        return AIProviderUnavailableError(reason)
    logger.warning("Unexpected AI provider exception type: %s", type(exc).__name__)
    return AIError(reason)
