"""V0.6 candidate snapshot builder: V0.1 profile + confirmed Resume Profile."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CandidateProfileVersion,
    CandidateProfileVersionReason,
    DealBreaker,
    DealBreakerType,
    Industry,
    JobPreferenceIndustry,
    JobPreferenceJobTitle,
    JobPreferenceLocation,
    JobPreferences,
    JobPreferenceWorkModel,
    Language,
    LanguageProficiency,
    ProfessionalProfile,
    RelocationPreference,
    RemotePreference,
    Resume,
    ResumeProfile,
    ResumeProfileStatus,
    SeniorityLevel,
    Skill,
    SkillProficiency,
    User,
    UserIndustry,
    UserLanguage,
    UserProfile,
    UserSkill,
)
from app.schemas.resume_profile import (
    EducationItem,
    LanguageItem,
    ResumeProfileData,
    SkillItem,
    WorkExperienceItem,
)
from app.services.matching.candidate import build_snapshot, ensure_snapshot
from app.services.taxonomy import TAXONOMY_VERSION
from tests.intelligence_helpers import make_user


def add_profile(db: Session, user: User) -> None:
    db.add(ProfessionalProfile(user_id=user.id, current_job_title="Data Engineer",
                               years_of_experience=5, seniority_level=SeniorityLevel.SENIOR))
    db.add(UserProfile(user_id=user.id, location="Köln"))
    python = Skill(name="Python")
    db.add(python)
    db.flush()
    db.add(UserSkill(user_id=user.id, skill_id=python.id, proficiency=SkillProficiency.EXPERT,
                     years_of_experience=5))
    for name, proficiency in (("German", LanguageProficiency.NATIVE),
                              ("English", LanguageProficiency.FLUENT)):
        language = Language(name=name)
        db.add(language)
        db.flush()
        db.add(UserLanguage(user_id=user.id, language_id=language.id, proficiency=proficiency))
    industry = Industry(name="Finance")
    db.add(industry)
    db.flush()
    db.add(UserIndustry(user_id=user.id, industry_id=industry.id))
    prefs = JobPreferences(user_id=user.id, minimum_salary=Decimal(70000), salary_currency="EUR",
                           relocation=RelocationPreference.NO)
    db.add(prefs)
    db.flush()
    db.add(JobPreferenceJobTitle(job_preferences_id=prefs.id, title="Data Engineer"))
    db.add(JobPreferenceLocation(job_preferences_id=prefs.id, location="Cologne"))
    db.add(JobPreferenceWorkModel(job_preferences_id=prefs.id, work_model=RemotePreference.HYBRID))
    db.add(JobPreferenceIndustry(job_preferences_id=prefs.id, industry="Insurance"))
    db.add(DealBreaker(user_id=user.id, type=DealBreakerType.RELOCATION, enabled=True))
    db.flush()


def add_resume(db: Session, user: User, status: ResumeProfileStatus) -> ResumeProfile:
    resume = Resume(user_id=user.id, file_name="cv.pdf", storage_reference="x",
                    mime_type="application/pdf", file_size=1)
    db.add(resume)
    db.flush()
    data = ResumeProfileData(
        work_experience=[
            WorkExperienceItem(job_title="Data Analyst", start_date="2015-01",
                               end_date="2018-12", confidence="high", review_status="accepted"),
            WorkExperienceItem(job_title="Data Engineer", start_date="2019-01", is_current=True,
                               confidence="high", review_status="accepted"),
        ],
        skills=[
            SkillItem(name="Python", confidence="high", review_status="accepted"),
            SkillItem(name="Docker", confidence="high", review_status="edited"),
            SkillItem(name="Kubernetes", confidence="low", review_status="rejected"),
            SkillItem(name="Internal Tool X", confidence="high", review_status="accepted"),
        ],
        languages=[
            LanguageItem(language="German", cefr_level="C2", confidence="high",
                         review_status="accepted"),
            LanguageItem(language="French", proficiency="fließend", confidence="high",
                         review_status="accepted"),
        ],
        education=[
            EducationItem(degree="M.Sc.", field_of_study="Computer Science", confidence="high",
                          review_status="accepted"),
        ],
    )
    profile = ResumeProfile(
        resume_id=resume.id, user_id=user.id, status=status, parser_version="resume_parser_v1",
        raw_data=data.model_dump(mode="json"), reviewed_data=data.model_dump(mode="json"),
        confirmed_at=datetime.now(UTC) if status is ResumeProfileStatus.CONFIRMED else None,
    )
    db.add(profile)
    db.flush()
    return profile


def test_snapshot_from_user_data(db_session: Session) -> None:
    user = make_user(db_session)
    add_profile(db_session, user)
    doc = build_snapshot(db_session, user).document
    assert doc.role.current_title and doc.role.current_title.value == "Data Engineer"
    assert doc.role.seniority_level and doc.role.seniority_level.value is SeniorityLevel.SENIOR
    assert doc.role.years_of_experience and doc.role.years_of_experience.value == 5
    assert doc.role.industries == ["Finance"]
    assert [s.skill_key for s in doc.skills] == ["python"]
    # Only "native" is an explicit level; V0.1 "fluent" is not a CEFR level.
    assert {(lang.language_key, lang.level) for lang in doc.languages} == {
        ("de", "native"), ("en", "unknown")
    }
    mobility = doc.location_and_mobility
    assert mobility.current_location and mobility.current_location.value.city == "Cologne"
    assert [p.city for p in mobility.preferred_locations] == ["Cologne"]
    assert mobility.relocation is RelocationPreference.NO
    assert doc.preferences.industries == ["Insurance"]
    assert doc.preferences.minimum_salary == Decimal("70000.00")
    assert [d.type for d in doc.deal_breakers] == [DealBreakerType.RELOCATION]
    assert doc.metadata.taxonomy_version == TAXONOMY_VERSION
    assert doc.metadata.sources == ["user"]


def test_confirmed_resume_fills_gaps_and_user_wins(db_session: Session) -> None:
    user = make_user(db_session)
    add_profile(db_session, user)
    profile = add_resume(db_session, user, ResumeProfileStatus.CONFIRMED)
    doc = build_snapshot(db_session, user).document

    # The user's own values win where both exist.
    assert doc.role.current_title and doc.role.current_title.origin == "user"
    python = next(s for s in doc.skills if s.skill_key == "python")
    assert python.origin == "user"
    german = next(lang for lang in doc.languages if lang.language_key == "de")
    assert (german.origin, german.level) == ("user", "native")

    # The resume fills gaps, with evidence references.
    docker = next(s for s in doc.skills if s.skill_key == "docker")
    assert docker.origin == "resume"
    assert docker.evidence_ref and docker.evidence_ref.startswith(f"resume_profile:{profile.id}/")
    # Rejected items are never used; unknown skills are kept by name only.
    names = {s.name for s in doc.skills}
    assert "Kubernetes" not in names
    assert next(s for s in doc.skills if s.name == "Internal Tool X").skill_key is None
    # "fließend" is not converted to a CEFR level.
    french = next(lang for lang in doc.languages if lang.language_key == "fr")
    assert french.level == "unknown"
    assert [e.degree_level for e in doc.credentials.education] == ["master"]
    assert [t.value for t in doc.role.previous_titles] == ["Data Analyst"]
    assert doc.metadata.sources == ["user", "resume"]


def test_unconfirmed_resume_is_ignored(db_session: Session) -> None:
    user = make_user(db_session)
    add_resume(db_session, user, ResumeProfileStatus.NEEDS_REVIEW)
    doc = build_snapshot(db_session, user).document
    assert doc.skills == [] and doc.credentials.education == []


def test_resume_only_candidate(db_session: Session) -> None:
    user = make_user(db_session)
    add_resume(db_session, user, ResumeProfileStatus.CONFIRMED)
    doc = build_snapshot(db_session, user).document
    assert doc.role.current_title and doc.role.current_title.value == "Data Engineer"
    assert doc.role.current_title.origin == "resume"
    assert doc.role.years_of_experience and doc.role.years_of_experience.origin == "resume"


def test_snapshots_are_versioned_by_content(db_session: Session) -> None:
    user = make_user(db_session)
    first = ensure_snapshot(db_session, user)
    assert first.version_number == 1
    assert first.reason is CandidateProfileVersionReason.INITIAL
    # Same content: same version, nothing new stored.
    assert ensure_snapshot(db_session, user).id == first.id

    add_profile(db_session, user)
    second = ensure_snapshot(db_session, user)
    assert second.version_number == 2 and second.id != first.id

    add_resume(db_session, user, ResumeProfileStatus.CONFIRMED)
    third = ensure_snapshot(db_session, user)
    assert third.reason is CandidateProfileVersionReason.RESUME_CONFIRMED
    db_session.commit()
    count = db_session.scalar(
        select(func.count()).select_from(CandidateProfileVersion)
        .where(CandidateProfileVersion.user_id == user.id)
    )
    assert count == 3


def test_preference_only_change_is_labelled(db_session: Session) -> None:
    user = make_user(db_session)
    add_profile(db_session, user)
    ensure_snapshot(db_session, user)
    prefs = db_session.scalar(select(JobPreferences).where(JobPreferences.user_id == user.id))
    assert prefs is not None
    prefs.minimum_salary = Decimal(80000)
    db_session.flush()
    assert ensure_snapshot(db_session, user).reason is (
        CandidateProfileVersionReason.PREFERENCES_UPDATED
    )


def test_snapshots_are_per_user(db_session: Session) -> None:
    alice, bob = make_user(db_session), make_user(db_session, "bob@example.com")
    a, b = ensure_snapshot(db_session, alice), ensure_snapshot(db_session, bob)
    # Identical (empty) content, but each user has their own version 1.
    assert a.content_hash == b.content_hash and a.id != b.id
