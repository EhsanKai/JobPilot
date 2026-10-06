"""Helpers for the V0.9 Humanize / document-edit tests.

`FakeHumanizeAI` is the full deterministic stand-in used by tests and by the
browser E2E stub: resumes, V0.8 documents, V0.9 question answers and Humanize.
Its humanizer rewords the fake letter's own sentences (a fixed phrase table, both
languages) and so keeps every fact; tests make it unfaithful on purpose
(`humanize_tamper`) to prove that every kind of change is rejected."""

import json
import re
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.provider import AIInvalidResponseError, T, summarize_validation_error
from app.schemas.document_ai import AIHumanizedLetter
from tests.question_helpers import FakeQuestionAI

_CONTEXT = re.compile(r"<humanize_context>\n(.*)\n</humanize_context>", re.S)

HumanizeTamper = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]

#: The fake letter's sentences and their natural rewordings (same claims).
REWORDINGS: dict[str, str] = {
    "I am writing to apply for the position of": "I would like to apply for the position of",
    "In my work I have taken on the following tasks:": "Day to day, my tasks included:",
    "My skills include:": "I bring skills such as:",
    "I would like to bring this experience to the role:": (
        "I want to bring this experience to the role:"
    ),
    "I look forward to discussing the position with you in person.": (
        "I would be glad to talk it through with you in person."
    ),
    "mit großem Interesse bewerbe ich mich auf die Position": (
        "gerne bewerbe ich mich auf die Position"
    ),
    "In meiner Tätigkeit habe ich folgende Aufgaben übernommen:": (
        "In meinem Arbeitsalltag habe ich folgende Aufgaben erledigt:"
    ),
    "Zu meinen Kenntnissen zählt:": "Zu meinen Stärken zählt:",
    "Diese Erfahrung möchte ich in die Stelle einbringen:": (
        "Diese Erfahrung bringe ich gern in die Stelle ein:"
    ),
    "Ich freue mich auf ein persönliches Gespräch mit Ihnen.": (
        "Über ein persönliches Gespräch würde ich mich freuen."
    ),
}


def humanize_context_of(prompt: str) -> dict[str, Any]:
    match = _CONTEXT.search(prompt)
    assert match, "prompt has no <humanize_context>"
    return json.loads(match.group(1))  # type: ignore[no-any-return]


def reword(text: str) -> str:
    for old, new in REWORDINGS.items():
        text = text.replace(old, new)
    return text


def faithful_humanized(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "language": context["language"],
        "paragraphs": [
            {
                "role": paragraph["role"],
                "text": reword(paragraph["text"]),
                "source_fact_ids": paragraph["source_fact_ids"],
            }
            for paragraph in context["paragraphs"]
        ],
    }


class FakeHumanizeAI(FakeQuestionAI):
    def __init__(self) -> None:
        super().__init__()
        self.humanize_tamper: HumanizeTamper | None = None
        self.humanize_override: dict[str, Any] | None = None
        self.humanize_error: Exception | None = None
        self.humanize_contexts: list[dict[str, Any]] = []
        self.humanize_prompts: list[str] = []

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
        if schema is not AIHumanizedLetter:
            return await super().generate_structured(system=system, prompt=prompt, schema=schema)
        self.calls.append({"system": system, "prompt": prompt})
        self.humanize_prompts.append(prompt)
        context = humanize_context_of(prompt)
        self.humanize_contexts.append(context)
        if self.humanize_error is not None:
            raise self.humanize_error
        output = (
            self.humanize_override
            if self.humanize_override is not None
            else faithful_humanized(context)
        )
        if self.humanize_tamper is not None and self.humanize_override is None:
            output = self.humanize_tamper(output, context) or output
        try:
            return schema.model_validate(output)
        except ValidationError as exc:
            raise AIInvalidResponseError(summarize_validation_error(exc)) from None

    @property
    def humanize_calls(self) -> int:
        return len(self.humanize_contexts)


# --- API shortcuts ----------------------------------------------------------------------------


def humanize(client: TestClient, headers: dict[str, str], application_id: str, **body: Any) -> Any:
    from tests.document_helpers import documents_url

    return client.post(
        f"{documents_url(application_id)}/cover-letter/humanize", headers=headers, json=body or None
    )


def edit(
    client: TestClient,
    headers: dict[str, str],
    application_id: str,
    document_id: str,
    body: dict[str, Any],
) -> Any:
    from tests.document_helpers import documents_url

    return client.post(
        f"{documents_url(application_id)}/{document_id}/edit", headers=headers, json=body
    )


def letter_edit(content: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """An edit request that restates a stored letter's content, with `changes`."""
    body: dict[str, Any] = {
        "kind": "cover_letter",
        "subject": content["subject"],
        "salutation": content["salutation"],
        "paragraphs": [{"role": p["role"], "text": p["text"]} for p in content["paragraphs"]],
        "salary_statement": content["salary_statement"],
        "closing_formula": content["closing_formula"],
        "signature": content["signature"],
    }
    body.update(changes)
    return body


def cv_edit(content: dict[str, Any], **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "tailored_cv",
        "summary": content["summary"]["text"],
        "header": {
            key: content["header"][key]
            for key in ("full_name", "email", "phone", "location", "links")
        },
        "experience": [
            {
                "source_fact_id": entry["source_fact_id"],
                "bullets": [bullet["text"] for bullet in entry["bullets"]],
            }
            for entry in content["experience"]
        ],
    }
    body.update(changes)
    return body
