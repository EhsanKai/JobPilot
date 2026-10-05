"""Architectural placeholder for future AI capabilities (V0.5+).

Not implemented in V0. No calls to OpenAI, Anthropic, Google, or a local LLM
happen here. This interface exists so business logic never calls a specific
AI provider's SDK directly; a future `OpenAIProvider`, `AnthropicProvider`,
`GoogleProvider`, or `LocalLLMProvider` would implement it instead.
"""

from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Contract every future AI provider implementation must satisfy."""

    @abstractmethod
    async def complete(self, prompt: str) -> str:
        raise NotImplementedError


class ResumeParser(ABC):
    """Future capability: extract structured data from an uploaded resume."""

    @abstractmethod
    async def parse(self, resume_reference: str) -> dict:
        raise NotImplementedError


class JobMatcher(ABC):
    """Future capability: produce an explainable match score for a job."""

    @abstractmethod
    async def score(self, resume_data: dict, job_data: dict) -> float:
        raise NotImplementedError


class CoverLetterGenerator(ABC):
    """Future capability: generate a tailored cover letter / Anschreiben."""

    @abstractmethod
    async def generate(self, resume_data: dict, job_data: dict, language: str) -> str:
        raise NotImplementedError
