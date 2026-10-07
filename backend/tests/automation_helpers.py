"""Helpers for the V0.10 browser assistant tests.

A real Chromium (headless) drives a local fake employer website (`tests.fake_site`);
the application's own context snapshot points at it, exactly as a real application
points at a real employer. No test touches the internet, and no test calls a real AI.

`Human` stands in for the person at the keyboard: it types into the *live* page
directly, outside the assistant -- which is what a user does in the browser window.
"""

import json
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.provider import AIInvalidResponseError, T, summarize_validation_error
from app.models import JobSourcePosting
from app.schemas.automation_ai import AIFieldMapping
from app.services.automation.registry import registry
from tests.application_helpers import API, confirmed_resume, make_job, started, user_by_email
from tests.fake_site import FakeApplicationSite
from tests.question_helpers import FakeQuestionAI

# --- The application -----------------------------------------------------------------------------

PREP = {
    "salary_expectation": "78000",
    "salary_currency": "EUR",
    "mention_salary_in_letter": False,
    "available_from": "2027-01-01",
    "notice_period": {"value": 3, "unit": "months"},
    "work_authorization": "authorized",
    "application_language": "en",
}


def make_application(
    client: TestClient,
    headers: dict[str, str],
    db: Session,
    site: FakeApplicationSite,
    *,
    path: str = "/apply",
    email: str = "jane@example.com",
    prep: dict[str, Any] | None = None,
    resume: bool = False,
    apply_url: str | None = None,
) -> dict[str, Any]:
    """An application whose "where to apply" is a page of the fake site."""
    user = user_by_email(db, email)
    job = make_job(db, user)
    posting = db.scalar(select(JobSourcePosting).where(JobSourcePosting.job_id == job.id))
    assert posting is not None
    posting.apply_url = apply_url if apply_url is not None else site.url(path)
    db.commit()
    if resume:
        confirmed_resume(client, headers)
    return started(client, headers, job, PREP if prep is None else prep)


def base(application_id: str) -> str:
    return f"{API}/applications/{application_id}/automation"


def start(client: TestClient, headers: dict[str, str], application_id: str) -> Any:
    return client.post(f"{base(application_id)}/start", headers=headers)


def started_session(
    client: TestClient, headers: dict[str, str], application_id: str
) -> dict[str, Any]:
    response = start(client, headers, application_id)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def session_url(application_id: str, session_id: str) -> str:
    return f"{base(application_id)}/{session_id}"


def post(
    client: TestClient,
    headers: dict[str, str],
    application_id: str,
    session_id: str,
    action: str,
    body: dict[str, Any] | None = None,
) -> Any:
    return client.post(
        f"{session_url(application_id, session_id)}/{action}", headers=headers, json=body
    )


def get_session(
    client: TestClient, headers: dict[str, str], application_id: str, session_id: str
) -> dict[str, Any]:
    response = client.get(session_url(application_id, session_id), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def get_fields(
    client: TestClient, headers: dict[str, str], application_id: str, session_id: str
) -> list[dict[str, Any]]:
    response = client.get(f"{session_url(application_id, session_id)}/fields", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]  # type: ignore[no-any-return]


def field_named(fields: list[dict[str, Any]], label: str) -> dict[str, Any]:
    matches = [f for f in fields if f["label"] == label]
    assert len(matches) == 1, f"{label!r}: {[f['label'] for f in fields]}"
    return matches[0]


def answer_question(
    client: TestClient,
    headers: dict[str, str],
    application_id: str,
    question_id: str,
    text: str | None = None,
    options: list[str] | None = None,
) -> dict[str, Any]:
    """The user's own answer to a question (V0.9 `manual`)."""
    body: dict[str, Any] = {"answer": text} if text is not None else {"selected_options": options}
    response = client.post(
        f"{API}/applications/{application_id}/questions/{question_id}/manual",
        headers=headers,
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# --- The person at the keyboard ------------------------------------------------------------------


class Human:
    """Acts on the live page the way a user does in the browser window: directly, not
    through the assistant. Only for tests (and the E2E server)."""

    def __init__(self, session_id: str) -> None:
        import uuid

        live = registry.peek(uuid.UUID(session_id))
        assert live is not None, "the session has no live browser"
        self._browser = live.browser

    def _run(self, script: str, *args: Any) -> Any:
        from app.services.automation.browser.playwright_driver import _worker

        async def go() -> Any:
            page = self._browser.live_page()  # type: ignore[attr-defined]
            return await page.evaluate(script, list(args))

        return _worker().call(go())

    def _page_do(self, action: Callable[[Any], Any]) -> Any:
        from app.services.automation.browser.playwright_driver import _worker

        async def go() -> Any:
            return await action(self._browser.live_page())  # type: ignore[attr-defined]

        return _worker().call(go())

    def type_into(self, selector: str, text: str) -> None:
        self._page_do(lambda page: page.fill(selector, text))

    def check(self, selector: str, checked: bool = True) -> None:
        self._page_do(lambda page: page.set_checked(selector, checked))

    def choose(self, selector: str, value: str) -> None:
        self._page_do(lambda page: page.select_option(selector, value=value))

    def value(self, selector: str) -> str:
        return str(self._page_do(lambda page: page.input_value(selector)))

    def is_checked(self, selector: str) -> bool:
        return bool(self._page_do(lambda page: page.is_checked(selector)))

    def files(self, selector: str) -> list[list[Any]]:
        return self._run(
            "args => Array.from(document.querySelector(args[0]).files).map(f => [f.name, f.size])",
            selector,
        )  # type: ignore[no-any-return]

    def events(self) -> dict[str, int]:
        return self._run("() => window.__events || {}")  # type: ignore[no-any-return]

    def goto(self, url: str) -> None:
        self._page_do(lambda page: page.goto(url, wait_until="domcontentloaded"))

    def url(self) -> str:
        return str(self._browser.page.url)


# --- A fake AI that can also map fields ----------------------------------------------------------

MappingResponder = Callable[[dict[str, Any]], dict[str, Any]]


class FakeAutomationAI(FakeQuestionAI):
    """Question answers as `FakeQuestionAI`; field mappings as a test says."""

    def __init__(self) -> None:
        super().__init__()
        self.mapping: MappingResponder | None = None
        self.mapping_error: Exception | None = None
        self.mapping_contexts: list[dict[str, Any]] = []
        self.name = "fake"
        self.model = "fake-1"

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
        if schema is not AIFieldMapping:
            return await super().generate_structured(system=system, prompt=prompt, schema=schema)
        self.calls.append({"system": system, "prompt": prompt})
        start = prompt.index("<mapping_context>") + len("<mapping_context>")
        end = prompt.index("</mapping_context>")
        context = json.loads(prompt[start:end])
        self.mapping_contexts.append(context)
        if self.mapping_error is not None:
            raise self.mapping_error
        output = (
            self.mapping(context)
            if self.mapping is not None
            else {"application_question_id": None, "confidence": "LOW", "reason": "none"}
        )
        try:
            return schema.model_validate(output)
        except ValidationError as exc:
            raise AIInvalidResponseError(summarize_validation_error(exc)) from None
