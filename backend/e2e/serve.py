"""Run the real JobPilot API for browser E2E tests, with deterministic job sources.

    python -m e2e.serve            (from backend/, used by frontend/playwright.config.ts)

Everything is production code -- FastAPI app, auth, Postgres schema built by
the Alembic migrations, Greenhouse and Lever adapters, normalizer, identity
resolution, API -- except the network hop to the job boards: the adapters'
`JsonFetcher` gets an `httpx.MockTransport` that serves fixed Greenhouse and
Lever payloads (`tests.job_helpers`). E2E runs must be repeatable, and the
real boards are not reachable from every environment (and their content
changes daily). This does NOT claim anything about the live APIs.

The AI is a deterministic stub as well (`E2EStubAI`, in this file only -- nothing
in `app/` can reach it): it analyzes the fixture resume with the canned parser
output, and writes tailored documents by restating the very facts it is shown,
so the browser flow exercises the real pipeline -- context, structured output,
truth validation, versioning, storage -- without a real provider. Answers to
application questions are faithful restatements of a supported fact (or "needs
input"), and Humanize rewords the stub's own sentences without changing a claim. It says
nothing about the quality of any real model.

V0.10 (browser assistant): the API runs a real headless Chromium against a local fake
employer website (`tests/fake_site.py`, a real HTTP server). Two extra vacancies
(published on request by `/e2e/browser-vacancies`, so earlier specs are unaffected) apply
there: "Platform Engineer" (the application form) and "Security Analyst" (a page that shows
a CAPTCHA). `/e2e/human/...` is a test-only stand-in for the person at the browser window:
it types into the very page the assistant works in, outside the assistant, the way a user
does in the window. It lives in this file only -- nothing in `app/` can reach it.

V0.11 (review & submit): `/e2e/submit-vacancies` publishes two more vacancies on the same fake
site, whose final button really submits: "Integration Engineer" (`/submit-apply`: the server
confirms the application) and "QA Engineer" (`/submit-ambiguous`: the page never confirms
anything). `/e2e/site` reports every press of a final button and every application the fake
employer received. A missing confirmation is given up on after 3 s here (production: 20 s).

Mock content: one SAP vacancy published on both boards (Cologne / "Köln",
same text) plus a Lever posting with the same title in Munich, which must
stay a separate job.

Environment:
    E2E_DATABASE_URL  database to (re)create and migrate
                      (default: ..._e2e next to DATABASE_URL)
    E2E_API_PORT      default 8000 (the frontend's default API URL)
"""

import os
from typing import Any

import uvicorn
from fastapi import APIRouter
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config
from app.core.config import settings


def _reset_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        # template0: never connected to, so CREATE DATABASE can't race autovacuum.
        connection.execute(text(f'CREATE DATABASE "{target.database}" TEMPLATE template0'))
    admin.dispose()
    config = Config()
    config.set_main_option(
        "script_location", os.path.join(os.path.dirname(__file__), "..", "alembic")
    )
    command.upgrade(config, "head")


def _human_router(site: Any) -> APIRouter:
    """Test-only: acts on the live browser page like a person at the window."""
    import uuid

    from pydantic import BaseModel

    from tests.automation_helpers import Human

    router = APIRouter(prefix="/e2e")

    class Typing(BaseModel):
        selector: str
        text: str

    class Selector(BaseModel):
        selector: str

    class Goto(BaseModel):
        url: str

    @router.post("/human/{session_id}/type")
    def type_into(session_id: uuid.UUID, body: Typing) -> dict[str, str]:
        Human(str(session_id)).type_into(body.selector, body.text)
        return {"ok": "true"}

    @router.post("/human/{session_id}/check")
    def check(session_id: uuid.UUID, body: Selector) -> dict[str, str]:
        Human(str(session_id)).check(body.selector, True)
        return {"ok": "true"}

    @router.post("/human/{session_id}/value")
    def value(session_id: uuid.UUID, body: Selector) -> dict[str, object]:
        human = Human(str(session_id))
        return {"value": human.value(body.selector)}

    @router.post("/human/{session_id}/checked")
    def checked(session_id: uuid.UUID, body: Selector) -> dict[str, object]:
        return {"checked": Human(str(session_id)).is_checked(body.selector)}

    @router.post("/human/{session_id}/files")
    def files(session_id: uuid.UUID, body: Selector) -> dict[str, object]:
        return {"files": Human(str(session_id)).files(body.selector)}

    @router.post("/human/{session_id}/goto")
    def goto(session_id: uuid.UUID, body: Goto) -> dict[str, str]:
        Human(str(session_id)).goto(body.url)
        return {"ok": "true"}

    @router.get("/site")
    def stats() -> dict[str, object]:
        stats_: dict[str, object] = site.stats()
        return stats_

    return router


def main() -> None:
    default = make_url(settings.DATABASE_URL)
    url = os.environ.get(
        "E2E_DATABASE_URL",
        default.set(database=f"{default.database}_e2e").render_as_string(hide_password=False),
    )
    settings.DATABASE_URL = url
    _reset_database(url)

    # Imported after the URL is set: the app's engine is created on import.
    os.environ["DATABASE_URL"] = url
    from app.ai.factory import get_ai_provider
    from app.ai.provider import AINotConfiguredError, T
    from app.main import app
    from app.schemas.document_ai import AICoverLetter, AIHumanizedLetter, AITailoredCV
    from app.schemas.question_ai import AIQuestionAnswer
    from app.schemas.resume_profile import ParsedResume
    from app.services.job_discovery.registry import get_job_source_registry
    from tests.fake_site import FakeApplicationSite
    from tests.humanize_helpers import FakeHumanizeAI
    from tests.job_helpers import (
        FakeJobBoards,
        cross_source_registry,
        greenhouse_job,
        lever_posting,
    )

    site = FakeApplicationSite(port=int(os.environ.get("E2E_SITE_PORT", "8765"))).start()
    settings.AUTOMATION_BROWSER_HEADLESS = True
    settings.AUTOMATION_ALLOW_PRIVATE_HOSTS = True
    settings.SUBMISSION_RESULT_TIMEOUT_SECONDS = 3.0
    # Many users share this one server in a full E2E run, and a stopped or submitted session
    # keeps its browser open for the user (by design): allow more than the default 3.
    settings.AUTOMATION_MAX_BROWSERS = 10
    chromium = os.environ.get("E2E_CHROMIUM_PATH")
    if chromium:
        settings.AUTOMATION_CHROMIUM_PATH = chromium

    boards = FakeJobBoards()
    registry = cross_source_registry(
        boards,
        greenhouse_jobs=[greenhouse_job(7001)],
        lever_postings=[
            lever_posting("c0ffee00-0000-4000-8000-000000000001", location="Köln"),
            lever_posting("c0ffee00-0000-4000-8000-000000000002", location="München"),
        ],
    )
    app.dependency_overrides[get_job_source_registry] = lambda: registry

    class E2EStubAI(FakeHumanizeAI):
        """Visible in every document's provenance as "e2e-stub".

        It serves only what the V0.8 / V0.9 flows need: documents, answers to
        application questions and Humanize. For anything else (the V0.6
        match explanation) it behaves as before it existed: no provider is
        configured, so that spec still sees the "not available" path."""

        name = "e2e-stub"
        model = "e2e-stub-1"

        async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
            served = (
                ParsedResume,
                AITailoredCV,
                AICoverLetter,
                AIQuestionAnswer,
                AIHumanizedLetter,
            )
            if schema not in served:
                raise AINotConfiguredError("the E2E stub serves documents and answers only")
            return await super().generate_structured(system=system, prompt=prompt, schema=schema)

    stub = E2EStubAI()
    app.dependency_overrides[get_ai_provider] = lambda: stub
    app.include_router(_human_router(site))

    from fastapi import APIRouter

    vacancies = APIRouter(prefix="/e2e")

    @vacancies.post("/browser-vacancies")
    def browser_vacancies() -> dict[str, str]:
        """Publish the two vacancies whose application page is the fake employer site. They
        appear only when the V0.10 spec asks, so earlier specs see the jobs they always saw."""
        boards.greenhouse_board(
            "example",
            jobs={
                "jobs": [
                    greenhouse_job(7001),
                    greenhouse_job(
                        7002,
                        title="Platform Engineer",
                        location="Hamburg, Germany",
                        url=site.url("/apply"),
                    ),
                    greenhouse_job(
                        7003,
                        title="Security Analyst",
                        location="Bremen, Germany",
                        url=site.url("/challenge"),
                    ),
                ]
            },
            board={"name": "Example GmbH"},
        )
        return {"ok": "true"}

    @vacancies.post("/submit-vacancies")
    def submit_vacancies() -> dict[str, str]:
        """V0.11: the two vacancies whose application form really submits."""
        boards.greenhouse_board(
            "example",
            jobs={
                "jobs": [
                    greenhouse_job(7001),
                    greenhouse_job(
                        7002,
                        title="Platform Engineer",
                        location="Hamburg, Germany",
                        url=site.url("/apply"),
                    ),
                    greenhouse_job(
                        7003,
                        title="Security Analyst",
                        location="Bremen, Germany",
                        url=site.url("/challenge"),
                    ),
                    greenhouse_job(
                        7101,
                        title="Integration Engineer",
                        location="Hamburg, Germany",
                        url=site.url("/submit-apply"),
                    ),
                    greenhouse_job(
                        7102,
                        title="QA Engineer",
                        location="Bremen, Germany",
                        url=site.url("/submit-ambiguous"),
                    ),
                ]
            },
            board={"name": "Example GmbH"},
        )
        return {"ok": "true"}

    app.include_router(vacancies)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("E2E_API_PORT", "8000")))


if __name__ == "__main__":
    main()
