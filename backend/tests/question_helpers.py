"""Helpers for the V0.9 application-question tests.

No test ever calls a real AI API. `FakeQuestionAI` answers a document request
like the V0.8 `FakeDocumentAI` and an application question by building an
output from the very context it was shown (the same facts, ids and options the
real model would get) -- or by returning exactly what a test tells it to, to
prove that invented answers are rejected.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.provider import AIInvalidResponseError, T, summarize_validation_error
from app.schemas.question_ai import AIQuestionAnswer
from tests.application_helpers import API
from tests.document_helpers import FakeDocumentAI

_CONTEXT = re.compile(r"<question_context>\n(.*)\n</question_context>", re.S)

Responder = Callable[[dict[str, Any]], dict[str, Any]]


def question_context_of(prompt: str) -> dict[str, Any]:
    match = _CONTEXT.search(prompt)
    assert match, "prompt has no <question_context>"
    return json.loads(match.group(1))  # type: ignore[no-any-return]


def all_facts(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Every candidate fact the AI was shown, flattened (id, text, kind)."""
    facts = context["candidate_facts"]
    found: list[dict[str, Any]] = []
    if facts["summary"]:
        found.append({**facts["summary"], "kind": "summary"})
    found.extend({**fact, "kind": "derived"} for fact in facts["derived"])
    for entry in facts["experience"]:
        found.append({"id": entry["id"], "text": entry["position"], "kind": "experience"})
        found.extend(entry["facts"])
    for group, kind in (
        ("education", "education"),
        ("skills", "skill"),
        ("languages", "language"),
        ("certifications", "certification"),
        ("projects", "project"),
        ("additional_information", "additional"),
    ):
        found.extend({**fact, "kind": kind} for fact in facts[group])
    found.extend({**fact, "kind": "application"} for fact in context.get("application_facts") or [])
    return found


def fact_with(context: dict[str, Any], needle: str) -> dict[str, Any]:
    for fact in all_facts(context):
        if needle.lower() in fact["text"].lower():
            return fact
    raise AssertionError(f"no fact mentions {needle!r}")


def needs_input() -> dict[str, Any]:
    return {
        "answer": None,
        "answer_type": "NEEDS_INPUT",
        "selected_options": [],
        "source_fact_ids": [],
        "confidence": "LOW",
        "needs_user_input": True,
    }


def text_answer(text: str, *fact_ids: str, answer_type: str = "TEXT") -> dict[str, Any]:
    return {
        "answer": text,
        "answer_type": answer_type,
        "source_fact_ids": list(fact_ids),
        "confidence": "HIGH",
        "needs_user_input": False,
    }


def option_answer(*options: str, facts: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "selected_options": list(options),
        "answer_type": "OPTIONS",
        "source_fact_ids": list(facts),
        "confidence": "HIGH",
        "needs_user_input": False,
    }


def faithful_answer(context: dict[str, Any]) -> dict[str, Any]:
    """What a truthful model does with the context: restate a supported fact,
    pick a listed option that a fact names, or ask for input."""
    question = context["question"]
    facts = all_facts(context)
    if question["options"]:
        for option in question["options"]:
            for fact in facts:
                if option.lower() in fact["text"].lower():
                    return option_answer(option, facts=(fact["id"],))
        return needs_input()
    if question["type"] in ("NUMERIC", "DATE", "SALARY", "YES_NO"):
        return needs_input()
    work = next((f for f in facts if f["kind"] == "responsibility"), None)
    if work is None:
        return needs_input()
    language = context["output_language"]["code"]
    prefix = "In meiner Arbeit: " if language == "de" else "In my work: "
    return text_answer(prefix + work["text"], work["id"])


class FakeQuestionAI(FakeDocumentAI):
    """Documents and resumes as `FakeDocumentAI`; question answers faithful by
    default, or as `respond` / `answer_override` say. `question_error` makes
    the call raise."""

    def __init__(self) -> None:
        super().__init__()
        self.respond: Responder | None = None
        self.answer_override: dict[str, Any] | None = None
        self.question_error: Exception | None = None
        self.question_contexts: list[dict[str, Any]] = []
        self.question_prompts: list[str] = []

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
        if schema is not AIQuestionAnswer:
            return await super().generate_structured(system=system, prompt=prompt, schema=schema)
        self.calls.append({"system": system, "prompt": prompt})
        self.question_prompts.append(prompt)
        context = question_context_of(prompt)
        self.question_contexts.append(context)
        if self.question_error is not None:
            raise self.question_error
        if self.answer_override is not None:
            output = self.answer_override
        elif self.respond is not None:
            output = self.respond(context)
        else:
            output = faithful_answer(context)
        try:
            return schema.model_validate(output)
        except ValidationError as exc:
            raise AIInvalidResponseError(summarize_validation_error(exc)) from None

    @property
    def question_calls(self) -> int:
        return len(self.question_contexts)


# --- API shortcuts ---------------------------------------------------------------------------


def questions_url(application_id: str) -> str:
    return f"{API}/applications/{application_id}/questions"


def add_question(
    client: TestClient,
    headers: dict[str, str],
    application_id: str,
    text: str = "Why do you want to work for our company?",
    **fields: Any,
) -> dict[str, Any]:
    response = client.post(
        questions_url(application_id), headers=headers, json={"question_text": text, **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def act(
    client: TestClient,
    headers: dict[str, str],
    application_id: str,
    question_id: str,
    action: str,
    body: dict[str, Any] | None = None,
) -> Any:
    """POST /applications/{id}/questions/{qid}/{action}."""
    return client.post(
        f"{questions_url(application_id)}/{question_id}/{action}", headers=headers, json=body
    )


# --- A ready application ---------------------------------------------------------------------

SALARY = {
    "salary_expectation": "78000",
    "salary_currency": "EUR",
    "mention_salary_in_letter": True,
}


def ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return response.json()  # type: ignore[no-any-return]


def error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body  # type: ignore[no-any-return]


def make_ready(
    client: TestClient,
    headers: dict[str, str],
    db: Any,
    *,
    email: str = "jane@example.com",
    language: str | None = "de",
    prep: dict[str, Any] | None = None,
    confirm: bool = True,
) -> dict[str, Any]:
    """An application with a confirmed resume (unless `confirm` is false) and a
    complete preparation, German by default."""
    from tests.application_helpers import confirmed_resume, make_job, started, user_by_email

    user = user_by_email(db, email)
    job = make_job(db, user)
    if confirm:
        confirmed_resume(client, headers)
    body: dict[str, Any] = {
        "available_from": "2027-01-01",
        "notice_period": {"value": 3, "unit": "months"},
        **(prep if prep is not None else SALARY),
    }
    if language:
        body["application_language"] = language
    return started(client, headers, job, body)
