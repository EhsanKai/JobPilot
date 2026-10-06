"""Builders for the V0.5 candidate & job intelligence data model tests."""

import hashlib
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    CandidateProfileVersion,
    CandidateProfileVersionReason,
    ExtractionConfidence,
    Job,
    JobIntelligence,
    JobMatch,
    JobRequirement,
    JobSourcePosting,
    MatchVerdict,
    RequirementCategory,
    RequirementKind,
    User,
)

RESUME_PROFILE_ID = uuid.UUID("00000000-0000-4000-8000-00000000e001")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def resume_ref(item: str) -> str:
    return f"resume_profile:{RESUME_PROFILE_ID}/{item}"


def candidate_document() -> dict[str, Any]:
    """A complete, valid snapshot with values of both origins."""
    return {
        "role": {
            "current_title": {"value": "SAP ABAP Developer", "origin": "user"},
            "seniority_level": {"value": "senior", "origin": "user"},
            "years_of_experience": {
                "value": 8.5,
                "origin": "resume",
                "evidence_ref": resume_ref("derived.experience_years"),
            },
            "target_titles": [{"title": "SAP Architect", "priority": "primary"}],
        },
        "skills": [
            {
                "name": "SAP ABAP",
                "category": "programming_language",
                "proficiency": "expert",
                "years": 8,
                "last_used": "2026-09",
                "origin": "user",
            },
            {
                "name": "SAP HANA",
                "category": "database",
                "origin": "resume",
                "evidence_ref": resume_ref("3f2a"),
            },
        ],
        "languages": [
            {"language_key": "de", "level": "native", "origin": "user"},
            {"language_key": "en", "level": "C1", "origin": "resume",
             "evidence_ref": resume_ref("9b1c")},
        ],
        "location_and_mobility": {
            "current_location": {
                "value": {"text": "Köln", "city": "Cologne", "country": "DE"},
                "origin": "user",
            },
            "preferred_locations": [
                {"text": "Remote - Germany", "country": "DE", "is_remote": True},
            ],
            "relocation": "no",
            "travel": "occasional",
        },
        "preferences": {
            "work_models": ["hybrid", "remote"],
            "employment_types": ["full_time"],
            "minimum_salary": "70000",
            "desired_salary": "85000",
            "currency": "EUR",
        },
        "deal_breakers": [{"type": "minimum_salary", "value": None, "enabled": True}],
        "credentials": {
            "education": [
                {
                    "degree": "Diplom-Informatiker",
                    "degree_level": "master",
                    "field_of_study": "Computer Science",
                    "origin": "resume",
                    "evidence_ref": resume_ref("e1"),
                }
            ],
            "certifications": [
                {"name": "SAP Certified Development Associate", "issuer": "SAP",
                 "date": "2021", "origin": "user"},
            ],
        },
        "work_authorization": {"authorized_countries": ["DE"]},
    }


def snapshot_sources() -> list[dict[str, Any]]:
    return [
        {"origin": "user", "record_type": "professional_profile", "record_id": str(uuid.uuid4()),
         "record_updated_at": "2026-10-01T10:00:00+00:00"},
        {"origin": "user", "record_type": "user_skills"},
        {"origin": "resume", "record_type": "resume_profile",
         "record_id": str(RESUME_PROFILE_ID)},
    ]


def job_facts() -> dict[str, Any]:
    provenance = {"extraction_method": "vocabulary", "confidence": "high"}
    return {
        "seniority": {"value": "senior", "source_field": "title", **provenance},
        "employment_type": {"value": "full_time", "source_field": "employment_type",
                            **provenance},
        "work_model": {"value": "hybrid", "source_field": "location", **provenance},
        "location": {
            "value": [{"text": "Cologne, Germany", "city": "Cologne", "country": "DE"}],
            "source_field": "location",
            **provenance,
        },
        "salary": {
            "value": {"minimum": "70000", "maximum": "90000", "currency": "EUR",
                      "period": "year"},
            "source_field": "salary_text",
            **provenance,
        },
        "language": {
            "value": ["de", "en"],
            "source_field": "description",
            "extraction_method": "section_rules",
            "confidence": "medium",
            "evidence_text": "Sehr gute Deutsch- und Englischkenntnisse",
            "evidence_offset": 120,
        },
        "travel": {"value": "occasional", "source_field": "description", **provenance},
        "authorization": {
            "value": {"countries": ["DE"], "sponsorship_available": False},
            "source_field": "description",
            **provenance,
        },
        "document_language": {"value": "de", "source_field": "description",
                              "extraction_method": "stopwords", "confidence": "high"},
        # V0.6
        "title": {"value": "Senior SAP ABAP Developer", "source_field": "title", **provenance},
        "industry": {"value": "Finance", "source_field": "industry", **provenance},
    }


def make_user(db: Session, email: str = "jane@example.com") -> User:
    user = User(email=email, password_hash="x", first_name="Jane", last_name="Doe")
    db.add(user)
    db.flush()
    return user


def make_job(db: Session, key: str = "4012345") -> tuple[Job, JobSourcePosting]:
    url = f"https://job-boards.greenhouse.io/acme/jobs/{key}"
    job = Job(canonical_url=url, title="Senior SAP ABAP Developer", company_name="Example GmbH")
    db.add(job)
    db.flush()
    posting = JobSourcePosting(
        job_id=job.id,
        source="greenhouse",
        source_job_id=key,
        dedup_key=f"greenhouse:id:{key}",
        canonical_source_url=url,
        title=job.title,
        company_name=job.company_name,
    )
    db.add(posting)
    db.flush()
    return job, posting


def make_version(
    db: Session,
    user: User,
    number: int = 1,
    content: str = "content-1",
    document: dict[str, Any] | None = None,
) -> CandidateProfileVersion:
    version = CandidateProfileVersion(
        user_id=user.id,
        version_number=number,
        content_hash=sha(content),
        document=document if document is not None else candidate_document(),
        sources=snapshot_sources(),
        reason=CandidateProfileVersionReason.INITIAL,
    )
    db.add(version)
    db.flush()
    return version


def make_intelligence(
    db: Session,
    job: Job,
    result: str = "result-1",
    is_current: bool = True,
    posting: JobSourcePosting | None = None,
    extractor_version: str = "job_extractor_v1",
) -> JobIntelligence:
    intelligence = JobIntelligence(
        job_id=job.id,
        extractor_version=extractor_version,
        taxonomy_version="skill_taxonomy_v1",
        input_hash=sha(f"input-{job.id}"),
        result_hash=sha(result),
        facts=job_facts(),
        is_current=is_current,
        source_posting_id=posting.id if posting else None,
    )
    db.add(intelligence)
    db.flush()
    return intelligence


def make_requirement(
    db: Session, intelligence: JobIntelligence, **overrides: Any
) -> JobRequirement:
    values: dict[str, Any] = {
        "job_intelligence_id": intelligence.id,
        "kind": RequirementKind.REQUIRED,
        "category": RequirementCategory.SKILL,
        "skill_key": "sap_abap",
        "value_text": "SAP ABAP",
        "min_years": Decimal("3"),
        "source_field": "description",
        "section_kind": "requirements",
        "extraction_method": "vocabulary",
        "confidence": ExtractionConfidence.HIGH,
        "evidence_text": "3+ years of SAP ABAP development",
        "evidence_offset": 42,
    }
    values.update(overrides)
    requirement = JobRequirement(**values)
    db.add(requirement)
    db.flush()
    return requirement


def match_criteria() -> list[dict[str, Any]]:
    return [
        {"key": "skills.required", "status": "partial", "weight": 0.4, "score": 0.5,
         "requirement_ids": [str(uuid.uuid4())], "candidate_refs": ["skills/0"],
         "reason_code": "skill_missing"},
        {"key": "work_model", "status": "unknown", "weight": 0.1},
    ]


def make_match(
    db: Session,
    user: User,
    job: Job,
    version: CandidateProfileVersion,
    intelligence: JobIntelligence,
    *,
    model_id: str = "match_v1",
    is_current: bool = True,
    score: Decimal | None = Decimal("72.50"),
    verdict: MatchVerdict = MatchVerdict.GOOD,
    is_blocked: bool = False,
) -> JobMatch:
    match = JobMatch(
        user_id=user.id,
        job_id=job.id,
        candidate_profile_version_id=version.id,
        job_intelligence_id=intelligence.id,
        model_id=model_id,
        model_hash=sha(f"model-{model_id}"),
        score=score,
        coverage=Decimal("0.875"),
        verdict=verdict,
        is_blocked=is_blocked,
        criteria=match_criteria(),
        caps=[{"key": "skills.required_missing", "max_score": 80, "reason_code": "skill_missing",
               "criterion_key": "skills.required"}],
        is_current=is_current,
    )
    db.add(match)
    db.flush()
    return match
