"""What the AI must return for an application question (V0.9).

Never free text: the provider is asked for this schema through the existing
`AIProvider` abstraction, and the result is validated against it before
anything else happens -- then checked for truthfulness by
`services/questions/validation.py`.

    {"answer": "I have five years of experience with Java.", "answer_type": "TEXT",
     "source_fact_ids": ["exp1.r1"], "confidence": "HIGH", "needs_user_input": false}

    {"answer": null, "answer_type": "NEEDS_INPUT", "source_fact_ids": [],
     "confidence": "LOW", "needs_user_input": true}

    {"selected_options": ["Senior"], "source_fact_ids": ["exp1"], "needs_user_input": false}

An unsupported factual question is answered with `needs_user_input`, never
with a guess. Contradictory output (an answer *and* needs_user_input, a choice
answer without options, ...) is invalid output and is rejected, not repaired.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The longest answer the contract allows (the question's own `max_length` is
#: enforced separately and may be lower).
MAX_ANSWER_CHARS = 5000
MAX_SELECTED_OPTIONS = 30
MAX_FACT_IDS = 12

AnswerType = Literal["TEXT", "NUMBER", "DATE", "YES_NO", "OPTIONS", "NEEDS_INPUT"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]


class AIQuestionAnswer(BaseModel):
    """Root of the application-question answer contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    answer: str | None = Field(
        default=None,
        description=(
            "The answer text, in the output language. Null when the facts do not support an "
            "answer, and for choice questions (use selected_options). Never a guess."
        ),
        max_length=MAX_ANSWER_CHARS,
    )
    answer_type: AnswerType = Field(
        default="TEXT",
        description=(
            "TEXT, NUMBER, DATE, YES_NO or OPTIONS (a choice question), or NEEDS_USER_INPUT-style "
            "'NEEDS_INPUT' when the facts do not support an answer."
        ),
    )
    selected_options: list[str] = Field(
        default_factory=list,
        description=(
            "For single and multiple choice questions only: the chosen options, each copied "
            "exactly from the question's options. Never an option that is not listed."
        ),
        max_length=MAX_SELECTED_OPTIONS,
    )
    source_fact_ids: list[str] = Field(
        default_factory=list,
        description="Ids of EVERY candidate or application fact the answer is based on.",
        max_length=MAX_FACT_IDS,
    )
    job_fact_ids: list[str] = Field(
        default_factory=list,
        description="Ids of the job facts (job.*) the answer refers to, if any.",
        max_length=MAX_FACT_IDS,
    )
    confidence: Confidence = Field(
        default="LOW", description="HIGH only when the cited facts state the answer outright."
    )
    needs_user_input: bool = Field(
        default=False,
        description="true when the facts do not support an answer: the user must answer.",
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        has_content = bool((self.answer or "").strip()) or bool(self.selected_options)
        if self.needs_user_input:
            if has_content or self.answer_type != "NEEDS_INPUT":
                raise ValueError("needs_user_input must come with no answer and type NEEDS_INPUT")
            return self
        if self.answer_type == "NEEDS_INPUT":
            raise ValueError("answer_type NEEDS_INPUT requires needs_user_input")
        if not has_content:
            raise ValueError("an answer or selected_options is required")
        if (self.answer or "").strip() and self.selected_options:
            raise ValueError("give either an answer or selected_options, not both")
        return self
