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
truth validation, versioning, storage -- without a real provider. It says
nothing about the quality of any real model.

Mock content: one SAP vacancy published on both boards (Cologne / "Köln",
same text) plus a Lever posting with the same title in Munich, which must
stay a separate job.

Environment:
    E2E_DATABASE_URL  database to (re)create and migrate
                      (default: ..._e2e next to DATABASE_URL)
    E2E_API_PORT      default 8000 (the frontend's default API URL)
"""

import os

import uvicorn
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
    from app.schemas.document_ai import AICoverLetter, AITailoredCV
    from app.schemas.resume_profile import ParsedResume
    from app.services.job_discovery.registry import get_job_source_registry
    from tests.document_helpers import FakeDocumentAI
    from tests.job_helpers import (
        FakeJobBoards,
        cross_source_registry,
        greenhouse_job,
        lever_posting,
    )

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

    class E2EStubAI(FakeDocumentAI):
        """Visible in every document's provenance as "e2e-stub".

        It serves only what the V0.8 flow needs. For anything else (the V0.6
        match explanation) it behaves as before it existed: no provider is
        configured, so that spec still sees the "not available" path."""

        name = "e2e-stub"
        model = "e2e-stub-1"

        async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
            if schema not in (ParsedResume, AITailoredCV, AICoverLetter):
                raise AINotConfiguredError("the E2E stub serves resumes and documents only")
            return await super().generate_structured(system=system, prompt=prompt, schema=schema)

    stub = E2EStubAI()
    app.dependency_overrides[get_ai_provider] = lambda: stub
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("E2E_API_PORT", "8000")))


if __name__ == "__main__":
    main()
