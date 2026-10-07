"""What the AI may return when asked to match a form field to a saved question (V0.10).

Structured output only. The AI cannot create a question and cannot pick one that
does not exist: the server checks `application_question_id` against the candidates
it offered, and only a HIGH confidence that also passes the deterministic safety
checks is applied automatically.

    {"application_question_id": "5b0c...", "confidence": "HIGH",
     "reason": "Both ask for the notice period."}
    {"application_question_id": null, "confidence": "LOW", "reason": "No saved question asks this."}
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AIFieldMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    application_question_id: str | None = Field(default=None, max_length=64)
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reason: str = Field(default="", max_length=300)
