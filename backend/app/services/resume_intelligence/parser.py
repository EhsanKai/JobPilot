"""Stage 2 of the pipeline: normalized resume text -> `ParsedResume`."""

from abc import ABC, abstractmethod

from app.ai.prompts import load_prompt
from app.ai.provider import AIProvider
from app.schemas.resume_profile import ParsedResume

# Bump whenever the prompt, the ParsedResume contract, or post-processing
# changes behavior. Stored with every result; identical file + identical
# parser version is never re-processed automatically.
AI_RESUME_PARSER_VERSION = "resume_parser_v1"


class ResumeParser(ABC):
    """Contract for anything that turns resume text into a `ParsedResume`."""

    #: Stored with each result so it can be re-run when the parser improves.
    version: str = "unknown"
    #: Provenance (e.g. "anthropic" / model id); None for non-AI parsers.
    provider_name: str | None = None
    model: str | None = None

    @abstractmethod
    async def parse(self, text: str) -> ParsedResume:
        raise NotImplementedError


class AIResumeParser(ResumeParser):
    """Resume parser backed by any `AIProvider`.

    Knows nothing about a specific AI vendor: it supplies the versioned
    prompt and the `ParsedResume` schema, and receives a validated instance
    back (or an `AIError`).
    """

    version = AI_RESUME_PARSER_VERSION

    def __init__(self, provider: AIProvider, prompt_name: str = AI_RESUME_PARSER_VERSION) -> None:
        self._provider = provider
        self._system_prompt = load_prompt(prompt_name)
        self.provider_name = provider.name
        self.model = provider.model

    def build_user_prompt(self, text: str) -> str:
        # The resume is wrapped in explicit delimiters so the model treats it
        # as data; the system prompt tells it to ignore instructions inside.
        return (
            "Extract the structured resume profile from the resume text between the "
            "<resume_text> tags.\n\n<resume_text>\n"
            f"{text}\n"
            "</resume_text>"
        )

    async def parse(self, text: str) -> ParsedResume:
        return await self._provider.generate_structured(
            system=self._system_prompt,
            prompt=self.build_user_prompt(text),
            schema=ParsedResume,
        )
