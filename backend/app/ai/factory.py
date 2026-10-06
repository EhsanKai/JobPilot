from app.ai.provider import AINotConfiguredError, AIProvider
from app.ai.providers.disabled import DisabledAIProvider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def get_ai_provider() -> AIProvider:
    """Resolve the configured AI provider (FastAPI dependency).

    Never raises: a missing/unknown configuration yields a provider whose
    calls fail with AI_NOT_CONFIGURED, so the rest of the app keeps working
    and the user sees a clear message instead of a 500.
    """
    provider = settings.AI_PROVIDER.strip().lower()
    if provider == "anthropic":
        # Imported lazily so the vendor SDK is only loaded when selected.
        from app.ai.providers.anthropic import AnthropicProvider

        try:
            return AnthropicProvider(
                api_key=settings.ANTHROPIC_API_KEY,
                model=settings.AI_MODEL,
                timeout_seconds=settings.AI_TIMEOUT_SECONDS,
                max_retries=settings.AI_MAX_RETRIES,
                max_output_tokens=settings.AI_MAX_OUTPUT_TOKENS,
            )
        except AINotConfiguredError as exc:
            return DisabledAIProvider(str(exc))
    if provider not in ("disabled", "none", ""):
        logger.warning("Unknown AI_PROVIDER configured; AI features are disabled.")
    return DisabledAIProvider(f"AI_PROVIDER={provider or 'unset'}")
