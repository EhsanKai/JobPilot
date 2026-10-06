"""Architectural placeholders for future AI capabilities (V0.5+).

As of V0.2 the real, provider-agnostic AI layer lives in `app.ai`
(`AIProvider` + `AIError` hierarchy, vendor implementations under
`app.ai.providers`), and resume parsing is implemented in
`app.services.resume_intelligence.parser.ResumeParser`.

The interfaces below are still placeholders for later releases and are
intentionally not implemented: no job matching or document generation
exists yet.
"""

from abc import ABC, abstractmethod

from app.ai.provider import AIProvider
from app.services.resume_intelligence.parser import ResumeParser

__all__ = ["AIProvider", "ResumeParser", "JobMatcher", "CoverLetterGenerator"]


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
