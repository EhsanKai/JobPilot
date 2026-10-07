#!/usr/bin/env python3
"""
Seed the local development database with realistic manual test data.

This script creates:
1. A synthetic test user with realistic profile, preferences, and resume
2. Multiple test jobs in different scenarios
3. Some seeded applications for tracking/dashboard testing
4. Everything in a clearly isolated, repeatable way

Usage:
    python -m scripts.seed_manual_test_data [--reset]

The --reset flag drops existing data and recreates from scratch.
"""

import argparse
import hashlib
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Ensure we can import from app
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.application import Application, ApplicationStatus, WorkAuthorization
from app.models.candidate_profile_version import CandidateProfileVersion
from app.models.deal_breaker import DealBreaker, DealBreakerType
from app.models.employment_type import EmploymentType
from app.models.job import Job
from app.models.job_preferences import JobPreferences
from app.models.job_source_posting import JobSourcePosting
from app.models.language import Language, UserLanguage, LanguageProficiency
from app.models.professional_profile import ProfessionalProfile
from app.models.resume import Resume
from app.models.resume_profile import ResumeProfile, EmployerHistory, EducationHistory
from app.models.skill import Skill, UserSkill, SkillProficiency
from app.models.user import User
from app.models.user_job import UserJob
from app.models.user_profile import UserProfile
from app.models.work_model import WorkModel


def create_test_user(session: Session) -> User:
    """Create a synthetic test user."""
    user = User(
        email="test.user@example.local",
        first_name="Max",
        last_name="Mustermann",
        hashed_password="$2b$12$KIX.Fl.F8qO4mSkLH9XZWOwU0k.o0bIIIVhUgj7oB/Cw59RM.7B2i",  # hash of "TestPassword123!"
    )
    session.add(user)
    session.flush()
    return user


def create_personal_profile(session: Session, user: User) -> UserProfile:
    """Create a realistic personal profile."""
    profile = UserProfile(
        user_id=user.id,
        phone="+49 (123) 456-7890",
        location_text="München, Deutschland",
    )
    session.add(profile)
    session.flush()
    return profile


def create_professional_profile(session: Session, user: User) -> ProfessionalProfile:
    """Create a realistic professional profile."""
    profile = ProfessionalProfile(
        user_id=user.id,
        current_job_title="Senior Software Engineer",
        years_of_experience=7,
        seniority_level="Senior",
    )
    session.add(profile)
    session.flush()
    return profile


def create_job_preferences(session: Session, user: User) -> JobPreferences:
    """Create realistic job preferences."""
    prefs = JobPreferences(
        user_id=user.id,
        desired_locations=["München", "Berlin", "Remote"],
        salary_min=Decimal("85000"),
        salary_max=Decimal("130000"),
        employment_types=[EmploymentType.PERMANENT],
        work_models=[WorkModel.HYBRID, WorkModel.REMOTE],
        willing_to_relocate=False,
        willing_to_travel=True,
    )
    session.add(prefs)
    session.flush()
    return prefs


def add_skills(session: Session, user: User) -> None:
    """Add skills to the user."""
    skills_data = [
        ("Python", SkillProficiency.ADVANCED),
        ("TypeScript", SkillProficiency.ADVANCED),
        ("React", SkillProficiency.ADVANCED),
        ("PostgreSQL", SkillProficiency.ADVANCED),
        ("AWS", SkillProficiency.INTERMEDIATE),
        ("Docker", SkillProficiency.INTERMEDIATE),
        ("FastAPI", SkillProficiency.ADVANCED),
        ("GraphQL", SkillProficiency.INTERMEDIATE),
    ]

    for skill_name, proficiency in skills_data:
        # Check if skill exists in catalog, create if not
        skill = session.query(Skill).filter(Skill.name == skill_name).first()
        if not skill:
            skill = Skill(name=skill_name)
            session.add(skill)
            session.flush()

        # Add user skill association
        user_skill = UserSkill(user_id=user.id, skill_id=skill.id, proficiency=proficiency)
        session.add(user_skill)


def add_languages(session: Session, user: User) -> None:
    """Add languages to the user."""
    languages_data = [
        ("German", LanguageProficiency.NATIVE),  # Native
        ("English", LanguageProficiency.FLUENT),  # Fluent
        ("Spanish", LanguageProficiency.BASIC),  # Basic
    ]

    for lang_name, proficiency in languages_data:
        # Check if language exists in catalog, create if not
        lang = session.query(Language).filter(Language.name == lang_name).first()
        if not lang:
            lang = Language(name=lang_name)
            session.add(lang)
            session.flush()

        # Add user language association
        user_lang = UserLanguage(user_id=user.id, language_id=lang.id, proficiency=proficiency)
        session.add(user_lang)


def create_synthetic_resume(session: Session, user: User) -> Resume:
    """Create a realistic resume."""
    resume_text = """Max Mustermann
München, Deutschland | +49 (123) 456-7890
max.mustermann@example.local

PROFESSIONAL SUMMARY
Senior Software Engineer with 7+ years of experience building scalable backend systems and modern frontend applications.
Proven track record in cloud architecture, team leadership, and delivering high-impact products.

EMPLOYMENT HISTORY

Senior Software Engineer | TechCorp GmbH | Munich, Germany
January 2021 – Present (3+ years)
- Led architecture and implementation of microservices platform serving 2M+ users
- Designed and deployed PostgreSQL scaling strategy, reducing query latency by 40%
- Mentored 4 junior engineers; led code review culture improving defect detection by 25%
- Implemented automated deployment pipeline reducing release time from 4 hours to 15 minutes
- Tech: Python, FastAPI, PostgreSQL, Docker, Kubernetes, AWS

Software Engineer | FinServe Solutions | Berlin, Germany
July 2018 – December 2020 (2.5 years)
- Built REST APIs serving 500K daily active users
- Developed TypeScript/React frontend for payment dashboard
- Implemented real-time notifications system using WebSockets
- Tech: Node.js, TypeScript, React, PostgreSQL, Redis

Junior Software Engineer | StartupXYZ | Hamburg, Germany
June 2017 – June 2018 (1 year)
- Full-stack development on customer portal
- Implemented authentication and authorization system
- Tech: Python, Django, Vue.js, MongoDB

EDUCATION

Bachelor of Science in Computer Science
University of Munich (LMU)
Graduated: June 2017
GPA: 3.8/4.0

CERTIFICATIONS
- AWS Certified Solutions Architect - Professional (2022)
- Kubernetes Application Developer (CKAD) (2021)

TECHNICAL SKILLS
Backend: Python, FastAPI, Node.js, Java
Frontend: TypeScript, React, Vue.js, HTML/CSS
Databases: PostgreSQL, MongoDB, Redis
Cloud: AWS (EC2, S3, Lambda, RDS), Google Cloud
DevOps: Docker, Kubernetes, CI/CD (GitHub Actions, GitLab CI)

LANGUAGES
- German: Native
- English: Fluent (C1)
- Spanish: Basic (A2)
"""

    # Hash the resume text to use as file_hash
    file_hash = hashlib.sha256(resume_text.encode()).hexdigest()

    resume = Resume(
        user_id=user.id,
        file_name="max_mustermann_resume.pdf",
        file_size=len(resume_text.encode()),
        file_hash=file_hash,
        file_format="pdf",
        raw_text=resume_text,
        is_confirmed=True,
    )
    session.add(resume)
    session.flush()

    # Create resume profile from the resume
    resume_profile = ResumeProfile(
        user_id=user.id,
        resume_id=resume.id,
        ai_raw_output={},  # Would normally be AI output
    )
    session.add(resume_profile)
    session.flush()

    # Add employer history to resume profile
    employers = [
        {
            "company_name": "TechCorp GmbH",
            "job_title": "Senior Software Engineer",
            "start_date": "2021-01-15",
            "end_date": None,
            "location": "Munich, Germany",
            "description": "Led architecture and implementation of microservices platform",
            "extracted_technologies": ["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS"],
        },
        {
            "company_name": "FinServe Solutions",
            "job_title": "Software Engineer",
            "start_date": "2018-07-01",
            "end_date": "2020-12-31",
            "location": "Berlin, Germany",
            "description": "Built REST APIs and React frontends",
            "extracted_technologies": ["Node.js", "TypeScript", "React", "PostgreSQL", "Redis"],
        },
    ]

    for emp in employers:
        emp_history = EmployerHistory(
            resume_profile_id=resume_profile.id,
            company_name=emp["company_name"],
            job_title=emp["job_title"],
            start_date=emp["start_date"],
            end_date=emp["end_date"],
            location=emp["location"],
            description=emp["description"],
            extracted_technologies=emp["extracted_technologies"],
        )
        session.add(emp_history)

    # Add education
    education = EducationHistory(
        resume_profile_id=resume_profile.id,
        degree="Bachelor of Science",
        field="Computer Science",
        institution="University of Munich (LMU)",
        graduation_date="2017-06-30",
    )
    session.add(education)

    return resume


def create_test_jobs(session: Session) -> list[Job]:
    """Create multiple test jobs representing different scenarios."""
    now = datetime.now(UTC)
    jobs = []

    # Job A: Excellent Match (Strong Technical + Preference Match)
    job_a = Job(
        title="Senior Backend Engineer",
        company_name="DataViz Systems",
        description="""We're seeking a Senior Backend Engineer to join our growing team in Munich.

Requirements:
- 5+ years backend development experience
- Expert in Python and FastAPI
- PostgreSQL optimization experience
- AWS or cloud platform experience
- Team leadership experience

Nice to have:
- Kubernetes experience
- Microservices architecture
- Open source contributions

We offer:
- Hybrid work (3 days office, 2 days remote)
- Salary: €95,000 - €130,000 annually
- Relocation support available
- 30 days vacation
- Professional development budget
- Health insurance""",
        location="München, Deutschland",
        employment_type=EmploymentType.PERMANENT,
        work_model=[WorkModel.HYBRID],
        salary_min=Decimal("95000"),
        salary_max=Decimal("130000"),
        salary_currency="EUR",
        seniority_level="Senior",
        technologies=["Python", "FastAPI", "PostgreSQL", "AWS", "Docker"],
        industries=["Technology"],
        first_seen=now - timedelta(days=5),
        last_seen=now,
        is_active=True,
    )
    session.add(job_a)
    jobs.append(job_a)

    # Job B: Good Match (Good Technical, Some Preference Mismatch)
    job_b = Job(
        title="Full-Stack Engineer",
        company_name="WebServices Europe",
        description="""Join our Berlin-based engineering team as a Full-Stack Engineer.

You'll work on:
- Modern web applications with React and TypeScript
- Backend services in Node.js
- PostgreSQL database design
- AWS infrastructure

Requirements:
- 3+ years professional experience
- Strong JavaScript/TypeScript skills
- React experience required
- Backend API development experience

We offer:
- Fully remote position (Germany-based)
- Salary: €75,000 - €105,000
- 28 days vacation
- Home office equipment budget
- Flexible working hours""",
        location="Berlin, Deutschland",
        employment_type=EmploymentType.PERMANENT,
        work_model=[WorkModel.REMOTE],
        salary_min=Decimal("75000"),
        salary_max=Decimal("105000"),
        salary_currency="EUR",
        seniority_level="Mid",
        technologies=["TypeScript", "React", "Node.js", "PostgreSQL", "AWS"],
        industries=["Technology"],
        first_seen=now - timedelta(days=3),
        last_seen=now,
        is_active=True,
    )
    session.add(job_b)
    jobs.append(job_b)

    # Job C: Weak Match (Some overlapping skills, important gaps)
    job_c = Job(
        title="Java Developer",
        company_name="EnterpriseCorps",
        description="""We need experienced Java developers for our enterprise platform.

Requirements:
- 5+ years Java development
- Spring Boot framework expertise
- Hibernate and JPA
- Oracle or SQL Server database experience
- Enterprise architecture patterns

We offer:
- Full-time permanent role
- Munich location (on-site 4 days/week)
- Salary: €80,000 - €115,000
- Standard benefits package""",
        location="München, Deutschland",
        employment_type=EmploymentType.PERMANENT,
        work_model=[WorkModel.HYBRID],
        salary_min=Decimal("80000"),
        salary_max=Decimal("115000"),
        salary_currency="EUR",
        seniority_level="Senior",
        technologies=["Java", "Spring Boot", "Hibernate", "Oracle"],
        industries=["Technology"],
        first_seen=now - timedelta(days=10),
        last_seen=now,
        is_active=True,
    )
    session.add(job_c)
    jobs.append(job_c)

    # Job D: Missing Information (Unknown salary/work model)
    job_d = Job(
        title="Senior Platform Engineer",
        company_name="CloudScale GmbH",
        description="""We're looking for a Platform Engineer to help scale our infrastructure.

What we need:
- Strong backend development skills
- Experience with distributed systems
- Kubernetes administration
- Python or Go experience
- 5+ years experience

Competitive salary based on experience.
Location and work arrangement TBD during interview.""",
        location="Deutschland (flexible)",
        employment_type=EmploymentType.PERMANENT,
        work_model=None,  # Unknown
        salary_min=None,
        salary_max=None,
        seniority_level="Senior",
        technologies=["Python", "Kubernetes", "Docker", "Go"],
        industries=["Technology"],
        first_seen=now - timedelta(days=2),
        last_seen=now,
        is_active=True,
    )
    session.add(job_d)
    jobs.append(job_d)

    # Job E: Deal Breaker (Fully on-site when user prefers hybrid/remote)
    job_e = Job(
        title="Mid-Level Software Engineer",
        company_name="OnSiteOnly Corp",
        description="""We have an exciting opportunity for a Software Engineer.

Requirements:
- 3-5 years experience
- Python or JavaScript skills
- Database experience

Important: This is a fully on-site position in our Stuttgart office.
No remote work or hybrid options.

We offer:
- On-site role in Stuttgart
- Salary: €65,000 - €85,000
- Company parking provided
- On-site gym access""",
        location="Stuttgart, Deutschland",
        employment_type=EmploymentType.PERMANENT,
        work_model=[],  # Empty list = on-site only
        salary_min=Decimal("65000"),
        salary_max=Decimal("85000"),
        salary_currency="EUR",
        seniority_level="Mid",
        technologies=["Python", "JavaScript"],
        industries=["Technology"],
        first_seen=now - timedelta(days=15),
        last_seen=now,
        is_active=True,
    )
    session.add(job_e)
    jobs.append(job_e)

    # Job F: Cross-source duplicate (we'll create two source postings for this)
    job_f = Job(
        title="DevOps Engineer",
        company_name="MultiCloud Systems",
        description="""Join us as a DevOps Engineer.

You'll be responsible for:
- Managing Kubernetes clusters
- Infrastructure as Code (Terraform)
- CI/CD pipeline design
- Monitoring and alerting systems

Requirements:
- 4+ years DevOps experience
- Kubernetes hands-on experience
- Cloud platforms (AWS/GCP/Azure)
- Scripting skills (Python/Bash)

Salary: €90,000 - €125,000 annually
Work: Hybrid (Berlin, Munich, or remote)""",
        location="Berlin / München (flexible)",
        employment_type=EmploymentType.PERMANENT,
        work_model=[WorkModel.HYBRID, WorkModel.REMOTE],
        salary_min=Decimal("90000"),
        salary_max=Decimal("125000"),
        salary_currency="EUR",
        seniority_level="Senior",
        technologies=["Kubernetes", "Terraform", "AWS", "Python", "Bash"],
        industries=["Technology"],
        first_seen=now - timedelta(days=7),
        last_seen=now,
        is_active=True,
    )
    session.add(job_f)
    jobs.append(job_f)

    # Job G: Different language (German job posting)
    job_g = Job(
        title="Senior Softwareentwickler",
        company_name="Mittelstand GmbH",
        description="""Wir suchen einen erfahrenen Softwareentwickler.

Anforderungen:
- 6+ Jahre Berufserfahrung
- Python und/oder C++ Kenntnisse
- Datenbankerfahrung (PostgreSQL, MySQL)
- Team-Fähigkeit und Kommunikation

Wir bieten:
- Hybride Arbeit (Köln)
- Gehalt: €85.000 - €120.000
- 30 Tage Urlaub
- Weiterbildungsbudget
- Flexible Arbeitszeiten""",
        location="Köln, Deutschland",
        employment_type=EmploymentType.PERMANENT,
        work_model=[WorkModel.HYBRID],
        salary_min=Decimal("85000"),
        salary_max=Decimal("120000"),
        salary_currency="EUR",
        seniority_level="Senior",
        technologies=["Python", "C++", "PostgreSQL"],
        industries=["Technology"],
        first_seen=now - timedelta(days=1),
        last_seen=now,
        is_active=True,
    )
    session.add(job_g)
    jobs.append(job_g)

    # Job H: Expired/Inactive job
    job_h = Job(
        title="QA Engineer (Expired)",
        company_name="NoLongerHiring Corp",
        description="""This position is no longer available.

Note: This is an inactive job used for testing.
        """,
        location="Hamburg, Deutschland",
        employment_type=EmploymentType.PERMANENT,
        work_model=[WorkModel.HYBRID],
        salary_min=Decimal("60000"),
        salary_max=Decimal("80000"),
        salary_currency="EUR",
        seniority_level="Mid",
        technologies=["Python", "Selenium"],
        industries=["Technology"],
        first_seen=now - timedelta(days=60),
        last_seen=now - timedelta(days=30),  # Haven't seen in 30 days
        is_active=False,
    )
    session.add(job_h)
    jobs.append(job_h)

    session.flush()
    return jobs


def create_job_source_postings(session: Session, jobs: list[Job]) -> None:
    """Create source postings for jobs (they come from different job boards)."""
    now = datetime.now(UTC)

    # Job A from Greenhouse
    JobSourcePosting(
        job_id=jobs[0].id,
        source="greenhouse",
        source_job_id="acme_123456",
        original_url="https://jobs.greenhouse.io/dataviz/jobs/senior-backend",
        raw_data={"job_id": "acme_123456", "title": jobs[0].title},
        normalized_data={},
        first_seen=now - timedelta(days=5),
        last_seen=now,
        is_active=True,
    )

    # Job B from Lever
    JobSourcePosting(
        job_id=jobs[1].id,
        source="lever",
        source_job_id="webservices_789",
        original_url="https://jobs.lever.co/webservices/position",
        raw_data={"id": "webservices_789", "text": jobs[1].title},
        normalized_data={},
        first_seen=now - timedelta(days=3),
        last_seen=now,
        is_active=True,
    )

    # Job F from both Greenhouse and Lever (cross-source duplicate)
    JobSourcePosting(
        job_id=jobs[5].id,
        source="greenhouse",
        source_job_id="multicloud_555",
        original_url="https://jobs.greenhouse.io/multicloud/jobs/devops",
        raw_data={"job_id": "multicloud_555", "title": jobs[5].title},
        normalized_data={},
        first_seen=now - timedelta(days=7),
        last_seen=now,
        is_active=True,
    )

    JobSourcePosting(
        job_id=jobs[5].id,
        source="lever",
        source_job_id="multicloud_lever_888",
        original_url="https://jobs.lever.co/multicloud/devops-engineer",
        raw_data={"id": "multicloud_lever_888", "text": jobs[5].title},
        normalized_data={},
        first_seen=now - timedelta(days=7),
        last_seen=now,
        is_active=True,
    )

    # Add postings for other jobs
    for idx, job in enumerate(jobs[2:], 2):
        source = "greenhouse" if idx % 2 == 0 else "lever"
        JobSourcePosting(
            job_id=job.id,
            source=source,
            source_job_id=f"source_{idx}_{idx * 100}",
            original_url=f"https://jobs.example.com/{idx}",
            raw_data={"id": f"{idx}", "title": job.title},
            normalized_data={},
            first_seen=job.first_seen,
            last_seen=job.last_seen,
            is_active=job.is_active,
        )


def create_user_jobs(session: Session, user: User, jobs: list[Job]) -> list[UserJob]:
    """Create UserJob records (discovered/saved jobs for the user)."""
    user_jobs = []
    for job in jobs:
        # All jobs are discovered
        user_job = UserJob(
            user_id=user.id,
            job_id=job.id,
            is_saved=True,  # User has seen and interacted with these
        )
        session.add(user_job)
        user_jobs.append(user_job)
    session.flush()
    return user_jobs


def create_candidate_profile_snapshot(session: Session, user: User) -> CandidateProfileVersion:
    """Create a candidate profile snapshot for matching."""
    now = datetime.now(UTC)
    snapshot = CandidateProfileVersion(
        user_id=user.id,
        snapshot_data={
            "profile": {
                "current_job_title": "Senior Software Engineer",
                "years_of_experience": 7,
                "seniority_level": "Senior",
            },
            "skills": [
                {"name": "Python", "proficiency": "ADVANCED"},
                {"name": "TypeScript", "proficiency": "ADVANCED"},
                {"name": "React", "proficiency": "ADVANCED"},
                {"name": "PostgreSQL", "proficiency": "ADVANCED"},
                {"name": "AWS", "proficiency": "INTERMEDIATE"},
                {"name": "Docker", "proficiency": "INTERMEDIATE"},
                {"name": "FastAPI", "proficiency": "ADVANCED"},
            ],
            "languages": [
                {"name": "German", "cefr_level": "C2"},
                {"name": "English", "cefr_level": "C1"},
            ],
            "preferences": {
                "locations": ["München", "Berlin", "Remote"],
                "work_models": ["HYBRID", "REMOTE"],
                "salary_min": 85000,
                "salary_max": 130000,
                "willing_to_relocate": False,
            },
        },
        created_at=now,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def create_sample_applications(
    session: Session, user: User, jobs: list[Job], user_jobs: list[UserJob], resume: Resume
) -> None:
    """Create some seeded applications in different states for testing tracking/dashboard."""
    now = datetime.now(UTC)

    # Application 1: Job A in SUBMITTED state (for testing the real journey)
    # Don't create this one - let it be created by the real user flow

    # Application 2: Job B in PREPARING state
    app2 = Application(
        user_id=user.id,
        user_job_id=user_jobs[1].id,
        job_id=jobs[1].id,
        resume_id=resume.id,
        status=ApplicationStatus.PREPARING,
        mention_salary_in_letter=True,
        available_from=now.date() + timedelta(days=30),
        work_authorization=WorkAuthorization.AUTHORIZED,
        notes="Good fit, but need to prepare tailored CV first",
    )
    session.add(app2)

    # Application 3: Job C in DRAFT state
    app3 = Application(
        user_id=user.id,
        user_job_id=user_jobs[2].id,
        job_id=jobs[2].id,
        resume_id=resume.id,
        status=ApplicationStatus.DRAFT,
        notes="Skills don't align perfectly, needs further consideration",
    )
    session.add(app3)

    # Application 4: Job D in IN_PROGRESS state
    app4 = Application(
        user_id=user.id,
        user_job_id=user_jobs[3].id,
        job_id=jobs[3].id,
        resume_id=resume.id,
        status=ApplicationStatus.IN_PROGRESS,
        notes="Application in progress, waiting on feedback",
    )
    session.add(app4)

    # Application 5: Job G (German job) in INTERVIEWING state
    app5 = Application(
        user_id=user.id,
        user_job_id=user_jobs[6].id,
        job_id=jobs[6].id,
        resume_id=resume.id,
        status=ApplicationStatus.INTERVIEWING,
        application_language="de",
        notes="First interview scheduled for next week",
    )
    session.add(app5)

    # Application 6: Job C in REJECTED state (for tracking testing)
    # Note: Can't reuse the same job for two applications (unique constraint on user_job_id)
    # So we'll use a different job (Job H)
    app6 = Application(
        user_id=user.id,
        user_job_id=user_jobs[7].id,
        job_id=jobs[7].id,
        resume_id=resume.id,
        status=ApplicationStatus.REJECTED,
        notes="Rejected - position filled",
    )
    session.add(app6)

    session.flush()


def add_deal_breakers(session: Session, user: User) -> None:
    """Add deal breakers to user's job preferences."""
    # Salary minimum
    deal_breaker1 = DealBreaker(
        user_id=user.id,
        type=DealBreakerType.MINIMUM_SALARY,
        value="85000",
        enabled=True,
    )
    session.add(deal_breaker1)

    # Remote requirement (user wants hybrid/remote, not on-site)
    deal_breaker2 = DealBreaker(
        user_id=user.id,
        type=DealBreakerType.REMOTE_REQUIREMENT,
        value="hybrid_or_remote",
        enabled=True,
    )
    session.add(deal_breaker2)


def seed_database() -> None:
    """Run the complete seeding process."""
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionLocal()

    try:
        print("Creating test user...")
        user = create_test_user(session)

        print("Creating personal profile...")
        create_personal_profile(session, user)

        print("Creating professional profile...")
        create_professional_profile(session, user)

        print("Creating job preferences...")
        create_job_preferences(session, user)

        print("Adding skills...")
        add_skills(session, user)

        print("Adding languages...")
        add_languages(session, user)

        print("Adding deal breakers...")
        add_deal_breakers(session, user)

        print("Creating synthetic resume...")
        resume = create_synthetic_resume(session, user)

        print("Creating candidate profile snapshot...")
        create_candidate_profile_snapshot(session, user)

        print("Creating test jobs...")
        jobs = create_test_jobs(session)

        print("Creating job source postings...")
        create_job_source_postings(session, jobs)

        print("Creating user jobs...")
        user_jobs = create_user_jobs(session, user, jobs)

        print("Creating sample applications...")
        create_sample_applications(session, user, jobs, user_jobs, resume)

        session.commit()
        print("\n✓ Database seeded successfully!")
        print(f"\nTest User Credentials:")
        print(f"  Email: test.user@example.local")
        print(f"  Password: TestPassword123!")
        print(f"\nCreated:")
        print(f"  - 1 test user with complete profile")
        print(f"  - 1 synthetic resume with employment history")
        print(f"  - 8 test jobs with different scenarios")
        print(f"  - 6 sample applications in different states")
        print(f"  - Cross-source job duplicate (for testing deduplication)")

    except Exception as e:
        session.rollback()
        print(f"✗ Error seeding database: {e}")
        raise
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed JobPilot with manual test data")
    parser.add_argument("--reset", action="store_true", help="Reset database before seeding")
    args = parser.parse_args()

    if args.reset:
        print("WARNING: This will delete all data in the database!")
        response = input("Continue? (type 'yes' to confirm): ")
        if response.lower() != "yes":
            print("Cancelled.")
            return

        engine = create_engine(settings.DATABASE_URL)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        print("Database reset.")

        # Run migrations
        print("Running migrations...")
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        print("Migrations complete.")

    seed_database()


if __name__ == "__main__":
    main()
