"""Builders for the V0.7 Application Workspace tests.

Domain-neutral on purpose: nothing here is specific to one profession,
industry or job board, and no test touches the network.
"""

import io
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, JobSourcePosting, User, UserJob
from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel
from tests.resume_helpers import make_text_pdf, sample_resume_text

API = "/api/v1"

DESCRIPTION = """Requirements
• 3+ years of experience
• Strong communication

Nice to have
• A second language"""

#: Where a posting of each source lives. "other" is a source that does not host
#: its own application form.
POSTING_URLS = {
    "greenhouse": "https://job-boards.greenhouse.io/acme/jobs/{key}",
    "lever": "https://jobs.lever.co/acme/{key}",
    "other": "https://careers.example.org/jobs/{key}",
}


def user_by_email(db: Session, email: str = "jane@example.com") -> User:
    found = db.scalar(select(User).where(User.email == email))
    assert found is not None
    return found


def make_posting(
    db: Session, job: Job, source: str = "greenhouse", **overrides: Any
) -> JobSourcePosting:
    key = uuid.uuid4().hex[:8]
    url = POSTING_URLS[source].format(key=key)
    values: dict[str, Any] = {
        "job_id": job.id,
        "source": source,
        "source_job_id": key,
        "dedup_key": f"{source}:id:{key}",
        "source_company_key": "acme",
        # As the source returned it (tracking parameters), and normalized.
        "source_url": f"{url}?utm_source=feed",
        "canonical_source_url": url,
        # Lever states its application URL; the others do not.
        "apply_url": f"{url}/apply" if source == "lever" else None,
        "title": job.title,
        "company_name": job.company_name,
        "location": job.location,
        "is_active": True,
    }
    values.update(overrides)
    posting = JobSourcePosting(**values)
    db.add(posting)
    db.flush()
    return posting


def make_job(
    db: Session,
    owner: User | None = None,
    *,
    sources: Sequence[str] = ("greenhouse",),
    **overrides: Any,
) -> Job:
    """A canonical job with one posting per entry of `sources` (the first is
    preferred), visible to `owner` when given."""
    values: dict[str, Any] = {
        "canonical_url": f"https://jobs.example.com/{uuid.uuid4()}",
        "title": "Senior Project Manager",
        "company_name": "Example GmbH",
        "location": "Köln",
        "locations": ["Köln"],
        "country": "DE",
        "department": "Operations",
        "description": DESCRIPTION,
        "requirements": ["3+ years of experience"],
        "responsibilities": ["Plan and run projects"],
        "technologies": ["Excel"],
        "work_model": RemotePreference.HYBRID,
        "employment_type": EmploymentTypeValue.FULL_TIME,
        "seniority": SeniorityLevel.SENIOR,
        "industry": "Construction",
        "salary_min": Decimal(70000),
        "salary_max": Decimal(80000),
        "salary_currency": "EUR",
        "salary_period": SalaryPeriod.YEAR,
        "salary_text": "70-80k",
    }
    values.update(overrides)
    job = Job(**values)
    db.add(job)
    db.flush()
    postings = [make_posting(db, job, source) for source in sources]
    if postings:
        job.preferred_source_posting_id = postings[0].id
        job.canonical_url = postings[0].canonical_source_url
        job.apply_url = postings[0].apply_url
    if owner is not None:
        db.add(UserJob(user_id=owner.id, job_id=job.id))
    db.commit()
    return job


def start(
    client: TestClient, headers: dict[str, str], job: Job | str, body: dict[str, Any] | None = None
) -> Any:
    job_id = job.id if isinstance(job, Job) else job
    return client.post(f"{API}/jobs/{job_id}/application", headers=headers, json=body)


def started(
    client: TestClient, headers: dict[str, str], job: Job, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = start(client, headers, job, body)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def patch(
    client: TestClient, headers: dict[str, str], application_id: str, body: dict[str, Any]
) -> Any:
    return client.patch(f"{API}/applications/{application_id}", headers=headers, json=body)


# --- Resumes -------------------------------------------------------------------------------


def upload_resume(
    client: TestClient, headers: dict[str, str], name: str = "resume.pdf"
) -> dict[str, Any]:
    # Every upload gets its own content: an identical file would be recognized
    # as already analyzed (V0.2 reuses the earlier result).
    content = make_text_pdf(f"{sample_resume_text()}\nFile: {name} {uuid.uuid4()}")
    response = client.post(
        f"{API}/resumes",
        headers=headers,
        files={"file": (name, io.BytesIO(content), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def confirmed_resume(
    client: TestClient, headers: dict[str, str], name: str = "resume.pdf"
) -> dict[str, Any]:
    """Upload, analyze (fake AI), review and confirm a resume through the real API."""
    resume = upload_resume(client, headers, name)
    base = f"{API}/resumes/{resume['id']}"
    assert client.post(f"{base}/process", headers=headers, json={}).status_code == 202
    assert client.post(f"{base}/profile/accept-pending", headers=headers).status_code == 200
    assert client.post(f"{base}/profile/confirm", headers=headers).status_code == 200
    return resume
