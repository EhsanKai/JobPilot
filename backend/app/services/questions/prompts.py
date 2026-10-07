"""The prompt of the application-question answer generation (V0.9).

Versioned like the V0.8 prompts: the version and the prompt's SHA-256 are stored
with every AI attempt, a released prompt file is never edited (a behavioral
change gets a new file and a new version), and `PROMPT_FINGERPRINTS` (checked
by a test) makes a silent edit fail loudly. The prompt text itself is not
stored and never returned by the API.
"""

import json

from app.ai.prompts import load_prompt, prompt_hash
from app.services.questions.context import QuestionContext

ANSWER_PROMPT_VERSION = "application_answer_v1"

PROMPT_FINGERPRINTS: dict[str, str] = {
    ANSWER_PROMPT_VERSION: "d96a87209a4cbdbf07b090e05b259f6456d7dd9ce5eb2be2d134901766ba76fe",
}


def system_prompt() -> str:
    return load_prompt(ANSWER_PROMPT_VERSION)


def current_prompt_hash() -> str:
    return prompt_hash(ANSWER_PROMPT_VERSION)


def user_prompt(context: QuestionContext) -> str:
    """The question context, as data between tags."""
    language = context.payload["output_language"]["name"]
    return (
        f"Answer the application question using ONLY the facts between the "
        f"<question_context> tags. Write in {language}.\n\n<question_context>\n"
        f"{json.dumps(context.payload, ensure_ascii=False, indent=2)}\n"
        "</question_context>"
    )
