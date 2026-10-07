"""Seed synthetic manual-test data: one test user with a profile, a resume, jobs, matches
and applications in several tracking states.

    docker compose run --rm seed              # inside Docker (recommended)
    docker compose run --rm seed --reset      # delete the test user and seed again
    python -m scripts.seed_manual_test_data   # from backend/, against DATABASE_URL

Everything goes through JobPilot's real code paths -- no hand-written rows:

- the account is registered by the auth service (real e-mail validation, real bcrypt);
- profile, skills, languages and job preferences by their services and schemas;
- the resume is a generated DOCX uploaded through the real upload validation;
- jobs come through the real discovery pipeline (Greenhouse and Lever adapters,
  normalizer, cross-source deduplication, persistence). Only the network hop is replaced:
  the adapters read synthetic board payloads defined below instead of the live APIs;
- matches are calculated by the deterministic matching engine;
- applications are started by the application service and moved through the V0.12
  tracking state machine with the same calls the API makes.

The data is synthetic: the person, the companies ("Musterfirma Demo GmbH") and their job
postings do not exist, and the postings' application pages are not real.

Safety: refuses to run when APP_ENV=production, and refuses a database whose schema is not
at the Alembic head (the backend container migrates on start). Idempotent: when the test
user already exists it changes nothing unless --reset is given; --reset deletes only the
test user (through the real account deletion) and seeds it again.
"""

import argparse
import html
import io
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from docx import Document
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.migrate import schema_is_current
from app.db.session import SessionLocal, engine
from app.models.application import ApplicationStatus, NoticePeriodUnit
from app.models.deal_breaker import DealBreakerType
from app.models.discovery_run import DiscoveryRunStatus
from app.models.job_preferences import (
    EmploymentTypeValue,
    JobTitlePriority,
    RelocationPreference,
    RemotePreference,
    TravelWillingness,
)
from app.models.language import LanguageProficiency
from app.models.professional_profile import SeniorityLevel
from app.models.skill import SkillProficiency
from app.models.user import User
from app.schemas.application import ApplicationCreate, NoticePeriod
from app.schemas.application_tracking import StatusChangeRequest, TrackingUpdateRequest
from app.schemas.auth import RegisterRequest
from app.schemas.jobs import JobSearchCriteria
from app.schemas.languages import UserLanguageCreate
from app.schemas.matching import BatchMatchRequest
from app.schemas.preferences import (
    DealBreakerInput,
    DesiredJobTitleInput,
    JobPreferencesUpdate,
    PreferredLocationInput,
)
from app.schemas.profile import UserProfileUpdate
from app.schemas.skills import SkillCreate
from app.services import (
    auth_service,
    languages_service,
    preferences_service,
    profile_service,
    resume_service,
    skills_service,
)
from app.services.applications import service as application_service
from app.services.applications import tracking
from app.services.job_discovery import job_service, run_service
from app.services.job_discovery.registry import JobSourceRegistry
from app.services.job_discovery.sources.ats import parse_board_config
from app.services.job_discovery.sources.greenhouse import API_HOST as GREENHOUSE_HOST
from app.services.job_discovery.sources.greenhouse import GreenhouseJobSource
from app.services.job_discovery.sources.http import JsonFetcher
from app.services.job_discovery.sources.lever import API_HOSTS as LEVER_HOSTS
from app.services.job_discovery.sources.lever import LeverJobSource
from app.services.matching import service as matching_service
from app.services.privacy import deletion
from app.services.storage.factory import get_file_storage

# The synthetic manual-test account. example.com is reserved for documentation (RFC 2606)
# and passes JobPilot's real e-mail validation; ".local" is a special-use name the
# validation rightly rejects.
TEST_EMAIL = "test.user@example.com"
TEST_PASSWORD = "TestPassword123!"
TEST_FIRST_NAME = "Max"
TEST_LAST_NAME = "Mustermann"

DEMO_COMPANY = "Musterfirma Demo GmbH"
DEMO_BOARD = "jobpilot-demo"
LEVER_HOST = LEVER_HOSTS["global"]
GREENHOUSE_BOARDS = "https://job-boards.greenhouse.io"


# --- Synthetic job postings -------------------------------------------------------------------


def _sections(intro: str, duties: list[str], needs: list[str]) -> str:
    items = lambda values: "".join(f"<li>{v}</li>" for v in values)  # noqa: E731
    return (
        f"<p>{intro}</p><h3>Your tasks</h3><ul>{items(duties)}</ul>"
        f"<h3>Your profile</h3><ul>{items(needs)}</ul>"
    )


#: (key, title, location, employment, intro, duties, requirements, scenario)
JOBS: list[dict[str, Any]] = [
    {
        "title": "Senior Python Backend Engineer",
        "location": "Munich, Germany",
        "lever_location": "München",
        "intro": "Build the APIs behind our logistics platform (synthetic demo posting).",
        "duties": ["Design and run FastAPI services", "Own PostgreSQL schemas and migrations"],
        "needs": [
            "5+ years of professional Python experience",
            "Experience with FastAPI or Django",
            "Strong PostgreSQL skills",
            "Docker and CI/CD",
            "Fluent German or English",
        ],
        "scenario": "excellent match, published on BOTH Greenhouse and Lever (one job)",
        "both_sources": True,
    },
    {
        "title": "Senior Full-Stack Engineer (TypeScript / Python)",
        "location": "Remote, Germany",
        "intro": "Work across our React frontend and Python backend (synthetic demo posting).",
        "duties": ["Ship features end to end", "Mentor other engineers"],
        "needs": ["TypeScript and React", "Python", "REST API design", "5+ years of experience"],
        "scenario": "good match, remote",
    },
    {
        "title": "Staff Platform Engineer",
        "location": "Berlin, Germany",
        "intro": "Run our Kubernetes platform (synthetic demo posting).",
        "duties": ["Operate Kubernetes clusters", "Build internal tooling in Go"],
        "needs": [
            "8+ years of experience",
            "Kubernetes in production",
            "Go",
            "Terraform",
            "Docker",
        ],
        "scenario": "partial match: missing Kubernetes / Go",
    },
    {
        "title": "Data Engineer (Spark / Scala)",
        "location": "Frankfurt am Main, Germany",
        "intro": "Build batch and streaming pipelines (synthetic demo posting).",
        "duties": ["Develop Spark jobs", "Model data in the lakehouse"],
        "needs": ["Scala", "Apache Spark", "Kafka", "SQL"],
        "scenario": "weak match: different stack",
    },
    {
        "title": "Junior Frontend Developer (Vue.js)",
        "location": "Hamburg, Germany",
        "intro": "Join our frontend team as a junior developer (synthetic demo posting).",
        "duties": ["Build Vue.js components", "Write unit tests"],
        "needs": ["First experience with Vue.js", "HTML and CSS", "German C1"],
        "scenario": "seniority mismatch (junior role)",
    },
    {
        "title": "Engineering Manager Backend",
        "location": "Munich, Germany",
        "intro": "Lead a team of six backend engineers (synthetic demo posting).",
        "duties": ["Lead and grow the team", "Own delivery and hiring"],
        "needs": ["3+ years of people leadership", "Backend engineering background"],
        "scenario": "management role",
    },
    {
        "title": "Working Student Software Development",
        "location": "Cologne, Germany",
        "lever_only": True,
        "lever_location": "Köln",
        "commitment": "Working Student",
        "intro": "Support our engineering team 20h/week (synthetic demo posting).",
        "duties": ["Help build internal tools"],
        "needs": ["Enrolled student", "Python basics"],
        "scenario": "employment-type deal breaker (working student)",
    },
]


def _greenhouse_payload() -> dict[str, Any]:
    jobs = []
    for index, job in enumerate(JOBS):
        if job.get("lever_only"):
            continue
        jobs.append(
            {
                "id": 9_100_000 + index,
                "title": job["title"],
                "updated_at": "2026-10-01T10:00:00Z",
                "first_published": "2026-09-28T08:00:00Z",
                "location": {"name": job["location"]},
                "absolute_url": f"{GREENHOUSE_BOARDS}/{DEMO_BOARD}/jobs/{9_100_000 + index}",
                "metadata": [{"name": "Employment Type", "value": "Full-time"}],
                "content": html.escape(_sections(job["intro"], job["duties"], job["needs"])),
            }
        )
    return {"jobs": jobs}


def _lever_payload() -> list[dict[str, Any]]:
    postings = []
    for index, job in enumerate(JOBS):
        if not (job.get("both_sources") or job.get("lever_only")):
            continue
        posting_id = f"5eed0000-0000-4000-8000-{index:012d}"
        items = lambda values: "".join(f"<li>{v}</li>" for v in values)  # noqa: E731
        location = job.get("lever_location", job["location"])
        postings.append(
            {
                "id": posting_id,
                "text": job["title"],
                "categories": {
                    "location": location,
                    "commitment": job.get("commitment", "Full Time"),
                    "allLocations": [location],
                },
                "country": "DE",
                "description": f"<div>{job['intro']}</div>",
                "lists": [
                    {"text": "Your tasks", "content": items(job["duties"])},
                    {"text": "Your profile", "content": items(job["needs"])},
                ],
                "hostedUrl": f"https://jobs.lever.co/{DEMO_BOARD}/{posting_id}",
                "applyUrl": f"https://jobs.lever.co/{DEMO_BOARD}/{posting_id}/apply",
                "workplaceType": "unspecified",
                "createdAt": 1759219200000,
            }
        )
    return postings


def demo_registry() -> JobSourceRegistry:
    """The real Greenhouse and Lever adapters, reading the synthetic payloads above."""
    greenhouse_jobs, lever_postings = _greenhouse_payload(), _lever_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == GREENHOUSE_HOST and path == f"/v1/boards/{DEMO_BOARD}":
            return httpx.Response(200, json={"name": DEMO_COMPANY})
        if host == GREENHOUSE_HOST and path == f"/v1/boards/{DEMO_BOARD}/jobs":
            return httpx.Response(200, json=greenhouse_jobs)
        if host == LEVER_HOST and path == f"/v0/postings/{DEMO_BOARD}":
            skip = int(request.url.params.get("skip", "0"))
            limit = int(request.url.params.get("limit", "100"))
            return httpx.Response(200, json=lever_postings[skip : skip + limit])
        return httpx.Response(404, json={"error": "not found"})

    def fetcher(host: str) -> JsonFetcher:
        return JsonFetcher(
            allowed_hosts=frozenset({host}),
            timeout_seconds=10.0,
            max_response_bytes=5_000_000,
            transport=httpx.MockTransport(handler),
            max_retries=0,
        )

    boards = parse_board_config(f"{DEMO_BOARD}:{DEMO_COMPANY}")
    registry = JobSourceRegistry()
    registry.register(GreenhouseJobSource(boards, fetcher(GREENHOUSE_HOST), max_jobs_per_board=100))
    registry.register(LeverJobSource(boards, fetcher(LEVER_HOST), max_jobs_per_board=100))
    return registry


# --- Synthetic resume --------------------------------------------------------------------------


def resume_docx() -> bytes:
    document = Document()
    document.add_heading(f"{TEST_FIRST_NAME} {TEST_LAST_NAME}", level=0)
    document.add_paragraph(f"{TEST_EMAIL} | Munich, Germany")
    document.add_paragraph("Synthetic resume for JobPilot manual testing. Not a real person.")
    document.add_heading("Professional Experience", level=1)
    document.add_paragraph(
        "Senior Software Engineer, Beispiel Logistik GmbH, Munich (2021 - today)"
    )
    for line in (
        "Designed and operated Python / FastAPI services on PostgreSQL",
        "Introduced Docker-based CI/CD pipelines",
        "Mentored three junior engineers",
    ):
        document.add_paragraph(line, style="List Bullet")
    document.add_paragraph("Software Engineer, Muster Media AG, Berlin (2018 - 2021)")
    for line in (
        "Built React and TypeScript frontends",
        "Developed REST APIs in Python",
    ):
        document.add_paragraph(line, style="List Bullet")
    document.add_heading("Education", level=1)
    document.add_paragraph("M.Sc. Computer Science, Technical University of Munich (2018)")
    document.add_heading("Skills", level=1)
    document.add_paragraph("Python, FastAPI, PostgreSQL, TypeScript, React, Docker, AWS, GraphQL")
    document.add_heading("Languages", level=1)
    document.add_paragraph("German (native), English (fluent), Spanish (basic)")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --- Seeding -------------------------------------------------------------------------------------


def _find_test_user(db: Session) -> User | None:
    return db.scalar(select(User).where(User.email == TEST_EMAIL))


def _job_by_title(db: Session, user: User, title: str) -> uuid.UUID:
    listing = job_service.list_jobs(db, user, page=1, page_size=100, q=title)
    for job in listing.items:
        if job.title == title:
            return job.id
    raise RuntimeError(f"seeded job not found: {title}")


def seed(db: Session) -> User:
    user = auth_service.register_user(
        db,
        RegisterRequest(
            email=TEST_EMAIL,
            password=TEST_PASSWORD,
            first_name=TEST_FIRST_NAME,
            last_name=TEST_LAST_NAME,
        ),
    )
    profile_service.update_profile(
        db,
        user,
        UserProfileUpdate(
            phone="+49 89 1234567",
            location="Munich, Germany",
            current_job_title="Senior Software Engineer",
            years_of_experience=7,
            seniority_level=SeniorityLevel.SENIOR,
            industries=["Software", "Logistics"],
        ),
    )
    for name, proficiency, years in (
        ("Python", SkillProficiency.EXPERT, 7),
        ("FastAPI", SkillProficiency.ADVANCED, 4),
        ("PostgreSQL", SkillProficiency.ADVANCED, 6),
        ("TypeScript", SkillProficiency.ADVANCED, 5),
        ("React", SkillProficiency.ADVANCED, 5),
        ("Docker", SkillProficiency.INTERMEDIATE, 4),
        ("AWS", SkillProficiency.INTERMEDIATE, 3),
        ("GraphQL", SkillProficiency.INTERMEDIATE, 2),
    ):
        skills_service.add_skill(
            db, user, SkillCreate(name=name, proficiency=proficiency, years_of_experience=years)
        )
    for name, level in (
        ("German", LanguageProficiency.NATIVE),
        ("English", LanguageProficiency.FLUENT),
        ("Spanish", LanguageProficiency.BASIC),
    ):
        languages_service.add_language(db, user, UserLanguageCreate(name=name, proficiency=level))
    preferences_service.update_preferences(
        db,
        user,
        JobPreferencesUpdate(
            desired_job_titles=[
                DesiredJobTitleInput(
                    title="Senior Backend Engineer", priority=JobTitlePriority.PRIMARY
                ),
                DesiredJobTitleInput(title="Full-Stack Engineer"),
            ],
            preferred_locations=[
                PreferredLocationInput(location="Munich"),
                PreferredLocationInput(location="Berlin"),
            ],
            employment_types=[EmploymentTypeValue.FULL_TIME],
            preferred_industries=["Software"],
            work_models=[RemotePreference.HYBRID, RemotePreference.REMOTE],
            minimum_salary=Decimal("75000"),
            desired_salary=Decimal("90000"),
            salary_currency="EUR",
            relocation=RelocationPreference.NO,
            travel_willingness=TravelWillingness.OCCASIONAL,
            deal_breakers=[
                DealBreakerInput(
                    type=DealBreakerType.EMPLOYMENT_TYPE,
                    value=EmploymentTypeValue.WORKING_STUDENT.value,
                )
            ],
        ),
    )
    content = resume_docx()
    resume_service.upload_resume(
        db,
        user,
        get_file_storage(),
        file_name="Max_Mustermann_CV.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_size=len(content),
        content=io.BytesIO(content),
    )

    # Jobs through the real discovery pipeline.
    registry = demo_registry()
    run = run_service.start_run(db, user, registry, JobSearchCriteria())
    run_service.execute_run(run.id, session_factory=SessionLocal, registry=registry)
    db.refresh(run)
    if run.status is not DiscoveryRunStatus.COMPLETED:
        raise RuntimeError(f"seed discovery run ended {run.status.value}: {run.error_code}")

    matching_service.calculate_batch(db, user, BatchMatchRequest(limit=25, force=True))

    _seed_applications(db, user)
    return user


def _start(db: Session, user: User, title: str, **preparation: Any) -> Any:
    job_id = _job_by_title(db, user, title)
    result = application_service.start_application(
        db, user, job_id, ApplicationCreate(**preparation)
    )
    return result.application


def _move(db: Session, user: User, application: Any, status: ApplicationStatus, note: str) -> None:
    tracking.change_status(db, user, application.id, StatusChangeRequest(status=status, note=note))


def _seed_applications(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    # Saved only (no application yet).
    junior = _job_by_title(db, user, "Junior Frontend Developer (Vue.js)")
    job_service.set_saved(db, user, junior, saved=True)

    # Preparing: the best match, with preparation data filled in.
    _start(
        db,
        user,
        "Senior Python Backend Engineer",
        notice_period=NoticePeriod(value=3, unit=NoticePeriodUnit.MONTHS),
        application_language="en",
    )
    # Applied, with a follow-up reminder.
    applied = _start(db, user, "Senior Full-Stack Engineer (TypeScript / Python)")
    _move(db, user, applied, ApplicationStatus.APPLIED, "Applied on the company website.")
    tracking.update_tracking(
        db, user, applied.id, TrackingUpdateRequest(follow_up_at=now + timedelta(days=7))
    )
    # Interview.
    interview = _start(db, user, "Staff Platform Engineer")
    _move(db, user, interview, ApplicationStatus.APPLIED, "Sent CV and cover letter.")
    _move(db, user, interview, ApplicationStatus.INTERVIEW, "First interview invitation.")
    # Rejected.
    rejected = _start(db, user, "Data Engineer (Spark / Scala)")
    _move(db, user, rejected, ApplicationStatus.APPLIED, "Applied.")
    _move(db, user, rejected, ApplicationStatus.REJECTED, "Rejection e-mail received.")
    # Withdrawn while preparing.
    withdrawn = _start(db, user, "Engineering Manager Backend")
    _move(db, user, withdrawn, ApplicationStatus.WITHDRAWN, "Decided not to apply.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed JobPilot's synthetic manual-test data.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the test user (and everything it owns) and seed it again",
    )
    args = parser.parse_args(argv)

    if settings.is_production:
        print("Refusing to seed synthetic test data: APP_ENV=production.", file=sys.stderr)
        return 2
    try:
        current = schema_is_current(engine)
    except Exception as exc:  # noqa: BLE001 -- report and stop
        print(f"Database not reachable: {type(exc).__name__}.", file=sys.stderr)
        return 1
    if not current:
        print(
            "The database schema is not at the current migration. Start the backend first "
            "(it migrates on start: `docker compose up`), or run `python -m app.db.migrate`.",
            file=sys.stderr,
        )
        return 1

    with SessionLocal() as db:
        existing = _find_test_user(db)
        if existing is not None:
            if not args.reset:
                print(f"Test user {TEST_EMAIL} already exists; nothing changed.")
                print("Use --reset to delete it and seed again.")
                _print_credentials()
                return 0
            report = deletion.delete_account(db, existing, get_file_storage())
            print(
                f"Deleted the existing test user ({sum(report.deleted_rows.values())} records, "
                f"{report.deleted_files} files)."
            )
        seed(db)
    print("Seeded synthetic manual-test data:")
    print("  - profile, 8 skills, 3 languages, job preferences with a deal breaker")
    print("  - 1 resume (DOCX, not yet analyzed: analysis needs an AI provider)")
    print(f"  - {len(JOBS)} synthetic jobs via the discovery pipeline (one on two sources)")
    print("  - deterministic matches, 1 saved job, 5 applications in different states")
    _print_credentials()
    return 0


def _print_credentials() -> None:
    print()
    print("  Open    http://localhost:3000/login")
    print(f"  E-mail  {TEST_EMAIL}")
    print(f"  Password {TEST_PASSWORD}")


if __name__ == "__main__":
    sys.exit(main())
