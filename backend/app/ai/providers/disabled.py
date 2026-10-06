from app.ai.provider import AINotConfiguredError, AIProvider, T


class DisabledAIProvider(AIProvider):
    """Used when no AI provider is configured: fails fast and gracefully."""

    name = "disabled"

    def __init__(self, reason: str = "AI provider disabled") -> None:
        self._reason = reason

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
        raise AINotConfiguredError(self._reason)
