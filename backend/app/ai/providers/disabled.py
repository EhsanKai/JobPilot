from app.ai.provider import AINotConfiguredError, AIProvider, T


class DisabledAIProvider(AIProvider):
    """Used when no AI provider is configured: fails fast and gracefully."""

    name = "disabled"

    def __init__(self, reason: str = "AI provider disabled") -> None:
        self._reason = reason

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
        raise AINotConfiguredError(self._reason)


class AIProcessingDisabledByUserError(AINotConfiguredError):
    code = "AI_DISABLED_BY_USER"
    user_message = (
        "You switched off external AI processing in Settings > Privacy, so JobPilot did not "
        "send anything to the AI provider. Switch it on there to use this feature."
    )


class DisabledByUserAIProvider(AIProvider):
    """The user switched external AI processing off (V0.13): nothing is ever sent."""

    name = "disabled-by-user"

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
        raise AIProcessingDisabledByUserError("external AI processing switched off by the user")
