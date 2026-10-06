"""V0.5 candidate & job intelligence data model, against Postgres.

Constraints, foreign-key behaviour, model-level validation and write-once
semantics of candidate_profile_versions, job_intelligence, job_requirements
and job_matches.
"""

from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
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
from app.models.mixins import ImmutableRecordError
from tests.intelligence_helpers import (
    candidate_document,
    make_intelligence,
    make_job,
    make_match,
    make_requirement,
    make_user,
    make_version,
    sha,
)


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def assert_integrity_error(db: Session, constraint: str) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        db.flush()
    db.rollback()


# --- candidate_profile_versions ------------------------------------------------------


def test_create_valid_candidate_snapshot(db_session: Session) -> None:
    user = make_user(db_session)
    version = make_version(db_session, user)
    db_session.commit()
    db_session.expire_all()

    stored = db_session.get(CandidateProfileVersion, version.id)
    assert stored is not None
    assert stored.version_number == 1
    assert stored.content_hash == sha("content-1")
    assert stored.reason is CandidateProfileVersionReason.INITIAL
    assert stored.created_at is not None
    # The version columns mirror the document metadata.
    assert stored.schema_version == "candidate_profile_v1"
    assert stored.taxonomy_version is None
    document = stored.read_document()
    assert document.metadata.sources == ["user", "resume"]
    assert document.skills[0].name == "SAP ABAP"
    assert stored.document["preferences"]["minimum_salary"] == "70000.00"
    assert [s["record_type"] for s in stored.sources] == [
        "professional_profile", "user_skills", "resume_profile"
    ]


@pytest.mark.parametrize(
    "document",
    [
        {"skills": "SAP"},
        {"role": {"current_title": {"value": "Dev"}}},  # no origin
        {"unexpected": True},
        {"work_authorization": {"authorized_countries": ["Germany"]}},
    ],
)
def test_invalid_document_is_rejected_before_it_reaches_the_database(
    db_session: Session, document: dict
) -> None:
    user = make_user(db_session)
    with pytest.raises(ValueError):
        CandidateProfileVersion(
            user_id=user.id, version_number=1, content_hash=sha("x"), document=document,
            sources=[], reason=CandidateProfileVersionReason.INITIAL,
        )
    db_session.rollback()
    assert count(db_session, CandidateProfileVersion) == 0


@pytest.mark.parametrize("field", ["email", "phone", "nationality", "gender", "date_of_birth"])
def test_personal_fields_cannot_be_stored(db_session: Session, field: str) -> None:
    document = candidate_document()
    document[field] = "x"
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        make_version(db_session, make_user(db_session), document=document)


def test_invalid_sources_and_hash_are_rejected(db_session: Session) -> None:
    user = make_user(db_session)
    with pytest.raises(ValueError, match="origin 'resume'"):
        CandidateProfileVersion(
            user_id=user.id, version_number=1, content_hash=sha("x"),
            document=candidate_document(), reason=CandidateProfileVersionReason.INITIAL,
            sources=[{"origin": "user", "record_type": "resume_profile", "record_id": None}],
        )
    with pytest.raises(ValueError, match="SHA-256"):
        CandidateProfileVersion(content_hash="not-a-hash")


def test_version_columns_must_match_the_document(db_session: Session) -> None:
    with pytest.raises(ValueError, match="does not match the document metadata"):
        CandidateProfileVersion(schema_version="candidate_profile_v0",
                                document=candidate_document())
    with pytest.raises(ValueError, match="does not match the document metadata"):
        CandidateProfileVersion(document=candidate_document(), taxonomy_version="skills_v9")
    # Given in agreement, in either order, is fine.
    version = CandidateProfileVersion(document=candidate_document(),
                                      schema_version="candidate_profile_v1")
    assert version.schema_version == "candidate_profile_v1"


def test_version_number_is_unique_per_user(db_session: Session) -> None:
    user = make_user(db_session)
    make_version(db_session, user, number=1, content="a")
    db_session.commit()
    db_session.add(
        CandidateProfileVersion(
            user_id=user.id, version_number=1, content_hash=sha("b"),
            document=candidate_document(), sources=[],
            reason=CandidateProfileVersionReason.PROFILE_UPDATED,
        )
    )
    assert_integrity_error(db_session, "uq_candidate_profile_versions_user_version")


def test_same_user_cannot_duplicate_a_content_hash(db_session: Session) -> None:
    user = make_user(db_session)
    make_version(db_session, user, number=1, content="same")
    db_session.commit()
    db_session.add(
        CandidateProfileVersion(
            user_id=user.id, version_number=2, content_hash=sha("same"),
            document=candidate_document(), sources=[],
            reason=CandidateProfileVersionReason.PROFILE_UPDATED,
        )
    )
    assert_integrity_error(db_session, "uq_candidate_profile_versions_user_content_hash")


def test_two_users_may_share_a_content_hash(db_session: Session) -> None:
    make_version(db_session, make_user(db_session, "a@example.com"), content="same")
    make_version(db_session, make_user(db_session, "b@example.com"), content="same")
    db_session.commit()
    assert count(db_session, CandidateProfileVersion) == 2


def test_version_numbers_increase_per_user(db_session: Session) -> None:
    user = make_user(db_session)
    for number in (1, 2, 3):
        make_version(db_session, user, number=number, content=f"c{number}")
    db_session.commit()
    numbers = db_session.scalars(
        select(CandidateProfileVersion.version_number).where(
            CandidateProfileVersion.user_id == user.id
        )
    ).all()
    assert sorted(numbers) == [1, 2, 3]

    db_session.add(
        CandidateProfileVersion(
            user_id=user.id, version_number=0, content_hash=sha("zero"),
            document=candidate_document(), sources=[],
            reason=CandidateProfileVersionReason.INITIAL,
        )
    )
    assert_integrity_error(db_session, "ck_candidate_profile_versions_version_number")


def test_work_authorization_is_stored_as_declared_iso_codes(db_session: Session) -> None:
    document = candidate_document()
    document["work_authorization"] = {"authorized_countries": ["ch", "DE", "at"]}
    version = make_version(db_session, make_user(db_session), document=document)
    db_session.commit()
    db_session.expire_all()
    assert version.document["work_authorization"] == {"authorized_countries": ["AT", "CH", "DE"]}


def test_absent_work_authorization_is_stored_as_unknown(db_session: Session) -> None:
    document = candidate_document()
    del document["work_authorization"]
    version = make_version(db_session, make_user(db_session), document=document)
    db_session.commit()
    db_session.expire_all()
    assert version.document["work_authorization"] == {"authorized_countries": []}
    assert "work_authorization" not in version.document["metadata"]["sections_provided"]


def test_candidate_snapshot_is_immutable(db_session: Session) -> None:
    version = make_version(db_session, make_user(db_session))
    db_session.commit()

    version.reason = CandidateProfileVersionReason.RESUME_CONFIRMED
    with pytest.raises(ImmutableRecordError, match="reason"):
        db_session.flush()
    db_session.rollback()

    document = candidate_document()
    document["work_authorization"] = {"authorized_countries": ["FR"]}
    version.document = document
    version.content_hash = sha("changed")
    with pytest.raises(ImmutableRecordError, match="content_hash"):
        db_session.flush()
    db_session.rollback()

    db_session.expire_all()
    assert version.document["work_authorization"] == {"authorized_countries": ["DE"]}


# --- job_intelligence ----------------------------------------------------------------


def test_job_intelligence_roundtrip(db_session: Session) -> None:
    job, posting = make_job(db_session)
    intelligence = make_intelligence(db_session, job, posting=posting)
    db_session.commit()
    db_session.expire_all()

    stored = db_session.get(JobIntelligence, intelligence.id)
    assert stored is not None and stored.is_current is True and stored.verified_at is None
    assert stored.source_posting_id == posting.id
    assert stored.job.id == job.id
    facts = stored.read_facts()
    assert facts.salary is not None and facts.salary.value.maximum == Decimal("90000.00")
    assert stored.facts["schema_version"] == "job_facts_v1"


def test_multiple_historical_versions_with_one_current(db_session: Session) -> None:
    job, _ = make_job(db_session)
    make_intelligence(db_session, job, result="r1", is_current=False)
    make_intelligence(db_session, job, result="r2", is_current=False)
    current = make_intelligence(db_session, job, result="r3", is_current=True)
    db_session.commit()

    assert count(db_session, JobIntelligence) == 3
    assert db_session.scalars(
        select(JobIntelligence.id).where(JobIntelligence.job_id == job.id,
                                         JobIntelligence.is_current)
    ).all() == [current.id]


def test_only_one_current_version_per_job(db_session: Session) -> None:
    job, _ = make_job(db_session)
    other_job, _ = make_job(db_session, key="999")
    make_intelligence(db_session, job, result="r1")
    make_intelligence(db_session, other_job, result="r1")  # other jobs are independent
    db_session.commit()

    db_session.add(
        JobIntelligence(
            job_id=job.id, extractor_version="job_extractor_v2",
            taxonomy_version="skill_taxonomy_v1", input_hash=sha("i"), result_hash=sha("r2"),
            facts={}, is_current=True,
        )
    )
    assert_integrity_error(db_session, "uq_job_intelligence_current")


def test_current_pointer_can_move_but_content_cannot_change(db_session: Session) -> None:
    job, _ = make_job(db_session)
    old = make_intelligence(db_session, job, result="r1")
    db_session.commit()

    old.is_current = False
    db_session.flush()
    new = make_intelligence(db_session, job, result="r2", extractor_version="job_extractor_v2")
    new.verified_at = func.now()  # set by the later review checkpoint
    db_session.commit()
    assert (old.is_current, new.is_current) == (False, True)
    assert new.verified_at is not None

    old.facts = {}
    with pytest.raises(ImmutableRecordError, match="facts"):
        db_session.flush()
    db_session.rollback()

    new.extractor_version = "job_extractor_v3"
    with pytest.raises(ImmutableRecordError, match="extractor_version"):
        db_session.flush()
    db_session.rollback()


def test_duplicate_result_for_the_same_versions_is_rejected(db_session: Session) -> None:
    job, _ = make_job(db_session)
    make_intelligence(db_session, job, result="same", is_current=False)
    # The same result from a newer extractor is a new version.
    make_intelligence(db_session, job, result="same", is_current=False,
                      extractor_version="job_extractor_v2")
    db_session.commit()

    db_session.add(
        JobIntelligence(
            job_id=job.id, extractor_version="job_extractor_v1",
            taxonomy_version="skill_taxonomy_v1", input_hash=sha("other input"),
            result_hash=sha("same"), facts={}, is_current=False,
        )
    )
    assert_integrity_error(db_session, "uq_job_intelligence_result")


def test_invalid_facts_and_hashes_are_rejected(db_session: Session) -> None:
    with pytest.raises(ValueError):
        JobIntelligence(facts={"salary": {"value": {"currency": "EUR"}}})
    with pytest.raises(ValueError, match="Extra inputs"):
        JobIntelligence(facts={"score": 90})
    with pytest.raises(ValueError, match="SHA-256"):
        JobIntelligence(input_hash="ABC")
    with pytest.raises(ValueError, match="SHA-256"):
        JobIntelligence(result_hash=sha("x").upper())


def test_source_posting_deletion_sets_reference_to_null(db_session: Session) -> None:
    job, posting = make_job(db_session)
    intelligence = make_intelligence(db_session, job, posting=posting)
    db_session.commit()

    db_session.execute(delete(JobSourcePosting).where(JobSourcePosting.id == posting.id))
    db_session.commit()
    db_session.expire_all()

    assert intelligence.source_posting_id is None
    assert db_session.get(Job, job.id) is not None  # the job itself stays


def test_job_deletion_cascades_to_intelligence_requirements_and_matches(
    db_session: Session,
) -> None:
    user = make_user(db_session)
    job, _ = make_job(db_session)
    other_job, _ = make_job(db_session, key="999")
    version = make_version(db_session, user)
    intelligence = make_intelligence(db_session, job)
    make_requirement(db_session, intelligence)
    make_match(db_session, user, job, version, intelligence)
    kept = make_intelligence(db_session, other_job)
    make_match(db_session, user, other_job, version, kept)
    db_session.commit()

    db_session.delete(job)
    db_session.commit()
    db_session.expire_all()

    assert db_session.scalars(select(JobIntelligence.job_id)).all() == [other_job.id]
    assert count(db_session, JobRequirement) == 0
    assert db_session.scalars(select(JobMatch.job_id)).all() == [other_job.id]
    assert db_session.get(CandidateProfileVersion, version.id) is not None


# --- job_requirements ----------------------------------------------------------------


def test_every_requirement_kind_and_category_is_valid(db_session: Session) -> None:
    job, _ = make_job(db_session)
    intelligence = make_intelligence(db_session, job)
    for kind in RequirementKind:
        for category in RequirementCategory:
            make_requirement(
                db_session, intelligence, kind=kind, category=category,
                skill_key="sap_abap" if category is RequirementCategory.SKILL else None,
            )
    db_session.commit()
    db_session.expire_all()

    stored = {(r.kind, r.category) for r in intelligence.requirements}
    assert stored == {(k, c) for k in RequirementKind for c in RequirementCategory}


def test_requirement_provenance_persists(db_session: Session) -> None:
    job, posting = make_job(db_session)
    intelligence = make_intelligence(db_session, job, posting=posting)
    requirement = make_requirement(
        db_session, intelligence, kind=RequirementKind.PREFERRED,
        confidence=ExtractionConfidence.MEDIUM,
    )
    db_session.commit()
    db_session.expire_all()

    stored = db_session.get(JobRequirement, requirement.id)
    assert stored is not None
    assert (stored.source_field, stored.section_kind, stored.extraction_method) == (
        "description", "requirements", "vocabulary"
    )
    assert stored.confidence is ExtractionConfidence.MEDIUM
    assert stored.evidence_text == "3+ years of SAP ABAP development"
    assert stored.evidence_offset == 42
    assert stored.min_years == Decimal("3.0")
    assert stored.created_at is not None
    # Posting provenance comes from the intelligence version.
    assert stored.job_intelligence.source_posting_id == posting.id


def test_future_value_fields_are_nullable(db_session: Session) -> None:
    job, _ = make_job(db_session)
    intelligence = make_intelligence(db_session, job)
    bare = make_requirement(
        db_session, intelligence, category=RequirementCategory.LANGUAGE, skill_key=None,
        value_text=None, min_years=None, section_kind=None, evidence_text=None,
        evidence_offset=None,
    )
    language = make_requirement(
        db_session, intelligence, category=RequirementCategory.LANGUAGE, skill_key=None,
        value_key="de", level="C1", min_years=None,
    )
    salary = make_requirement(
        db_session, intelligence, category=RequirementCategory.SALARY, skill_key=None,
        min_years=None, salary_min=Decimal("60000"), salary_max=Decimal("80000"),
        salary_currency="EUR",
    )
    db_session.commit()
    db_session.expire_all()

    for column in ("skill_key", "value_key", "value_text", "level", "min_years", "salary_min",
                   "salary_max", "salary_currency", "salary_period", "section_kind",
                   "evidence_text", "evidence_offset"):
        assert getattr(bare, column) is None, column
    assert (language.value_key, language.level) == ("de", "C1")
    assert (salary.salary_min, salary.salary_max, salary.salary_currency) == (
        Decimal("60000.00"), Decimal("80000.00"), "EUR"
    )
    assert salary.salary_period is None


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"category": RequirementCategory.LANGUAGE, "skill_key": "german"},
         "ck_job_requirements_skill_key"),
        ({"salary_min": Decimal("1")}, "ck_job_requirements_salary_fields"),
        ({"category": RequirementCategory.SALARY, "skill_key": None,
          "salary_min": Decimal("9"), "salary_max": Decimal("1")},
         "ck_job_requirements_salary_range"),
        ({"min_years": Decimal("-1")}, "ck_job_requirements_min_years"),
        ({"evidence_text": None, "evidence_offset": 3}, "ck_job_requirements_evidence_offset"),
    ],
)
def test_requirement_check_constraints(
    db_session: Session, overrides: dict, constraint: str
) -> None:
    job, _ = make_job(db_session)
    intelligence = make_intelligence(db_session, job)
    db_session.commit()
    with pytest.raises(IntegrityError, match=constraint):
        make_requirement(db_session, intelligence, **overrides)
    db_session.rollback()


def test_requirement_currency_is_validated(db_session: Session) -> None:
    with pytest.raises(ValueError, match="ISO 4217"):
        JobRequirement(salary_currency="eur")


def test_deleting_intelligence_cascades_to_its_requirements(db_session: Session) -> None:
    job, _ = make_job(db_session)
    doomed = make_intelligence(db_session, job, result="r1", is_current=False)
    kept = make_intelligence(db_session, job, result="r2")
    for _ in range(3):
        make_requirement(db_session, doomed)
    make_requirement(db_session, kept)
    db_session.commit()

    db_session.execute(delete(JobIntelligence).where(JobIntelligence.id == doomed.id))
    db_session.commit()
    assert db_session.scalars(select(JobRequirement.job_intelligence_id)).all() == [kept.id]

    # The ORM relationship cascades the same way.
    db_session.delete(kept)
    db_session.commit()
    assert count(db_session, JobRequirement) == 0


def test_requirements_are_immutable(db_session: Session) -> None:
    job, _ = make_job(db_session)
    requirement = make_requirement(db_session, make_intelligence(db_session, job))
    db_session.commit()

    requirement.kind = RequirementKind.CONTEXTUAL
    with pytest.raises(ImmutableRecordError, match="kind"):
        db_session.flush()
    db_session.rollback()


# --- job_matches ---------------------------------------------------------------------


def setup_match_inputs(
    db: Session,
) -> tuple[User, Job, CandidateProfileVersion, JobIntelligence]:
    user = make_user(db)
    job, _ = make_job(db)
    return user, job, make_version(db, user), make_intelligence(db, job)


def test_match_persists_model_and_version_references(db_session: Session) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    match = make_match(db_session, user, job, version, intelligence)
    db_session.commit()
    db_session.expire_all()

    stored = db_session.get(JobMatch, match.id)
    assert stored is not None
    assert (stored.model_id, stored.model_hash) == ("match_v1", sha("model-match_v1"))
    assert stored.score == Decimal("72.50")
    assert stored.coverage == Decimal("0.875")
    assert stored.verdict is MatchVerdict.GOOD and stored.is_blocked is False
    assert stored.is_current is True and stored.created_at is not None
    assert stored.candidate_profile_version.id == version.id
    assert stored.job_intelligence.id == intelligence.id
    assert [c.key for c in stored.read_criteria().root] == ["skills.required", "work_model"]
    assert stored.read_caps().root[0].max_score == 80


def test_score_may_be_null(db_session: Session) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    match = make_match(db_session, user, job, version, intelligence, score=None,
                       verdict=MatchVerdict.INSUFFICIENT_DATA)
    db_session.commit()
    db_session.expire_all()
    assert match.score is None and match.coverage == Decimal("0.875")


def test_same_inputs_and_model_give_one_result(db_session: Session) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    make_match(db_session, user, job, version, intelligence)
    db_session.commit()

    db_session.add(
        JobMatch(
            user_id=user.id, job_id=job.id, candidate_profile_version_id=version.id,
            job_intelligence_id=intelligence.id, model_id="match_v1", model_hash=sha("m"),
            coverage=Decimal("1"), verdict=MatchVerdict.GOOD, is_current=False,
        )
    )
    assert_integrity_error(db_session, "uq_job_matches_inputs")


def test_only_one_current_match_per_user_and_job(db_session: Session) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    make_match(db_session, user, job, version, intelligence, model_id="match_v1")
    db_session.commit()

    db_session.add(
        JobMatch(
            user_id=user.id, job_id=job.id, candidate_profile_version_id=version.id,
            job_intelligence_id=intelligence.id, model_id="match_v2", model_hash=sha("m2"),
            coverage=Decimal("1"), verdict=MatchVerdict.GOOD, is_current=True,
        )
    )
    assert_integrity_error(db_session, "uq_job_matches_current")


def test_historical_matches_remain_available(db_session: Session) -> None:
    user, job, v1, i1 = setup_match_inputs(db_session)
    old = make_match(db_session, user, job, v1, i1)
    db_session.commit()

    # The user edits their profile and the job is re-extracted: a new
    # current result replaces the old one, which stays as history.
    v2 = make_version(db_session, user, number=2, content="content-2")
    i1.is_current = False
    db_session.flush()
    i2 = make_intelligence(db_session, job, result="result-2")
    old.is_current = False
    db_session.flush()
    new = make_match(db_session, user, job, v2, i2, score=Decimal("90"),
                     verdict=MatchVerdict.STRONG)
    db_session.commit()
    db_session.expire_all()

    matches = db_session.scalars(
        select(JobMatch).where(JobMatch.user_id == user.id).order_by(JobMatch.created_at)
    ).all()
    assert {m.id for m in matches} == {old.id, new.id}
    history = db_session.get(JobMatch, old.id)
    assert history is not None and history.is_current is False
    assert history.score == Decimal("72.50")
    assert history.candidate_profile_version.version_number == 1
    assert history.job_intelligence.result_hash == sha("result-1")


def test_referenced_versions_cannot_be_deleted_on_their_own(db_session: Session) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    make_match(db_session, user, job, version, intelligence)
    unreferenced = make_version(db_session, user, number=2, content="content-2")
    db_session.commit()

    with pytest.raises(IntegrityError, match="fk_job_matches_candidate_profile_version"):
        db_session.execute(
            delete(CandidateProfileVersion).where(CandidateProfileVersion.id == version.id)
        )
    db_session.rollback()

    with pytest.raises(IntegrityError, match="fk_job_matches_job_intelligence"):
        db_session.execute(delete(JobIntelligence).where(JobIntelligence.id == intelligence.id))
    db_session.rollback()

    # A version no match references can go.
    db_session.execute(
        delete(CandidateProfileVersion).where(CandidateProfileVersion.id == unreferenced.id)
    )
    db_session.commit()
    assert count(db_session, CandidateProfileVersion) == 1


def test_user_deletion_cascades_to_versions_and_matches(db_session: Session) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    make_match(db_session, user, job, version, intelligence)
    other = make_user(db_session, "other@example.com")
    other_version = make_version(db_session, other)
    make_match(db_session, other, job, other_version, intelligence)
    db_session.commit()

    db_session.delete(user)
    db_session.commit()
    db_session.expire_all()

    assert db_session.scalars(select(CandidateProfileVersion.user_id)).all() == [other.id]
    assert db_session.scalars(select(JobMatch.user_id)).all() == [other.id]
    # Jobs and their intelligence are global and stay.
    assert db_session.get(JobIntelligence, intelligence.id) is not None


def test_match_cannot_mix_users_or_jobs(db_session: Session) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    other = make_user(db_session, "other@example.com")
    other_job, _ = make_job(db_session, key="999")
    db_session.commit()

    db_session.add(
        JobMatch(
            user_id=other.id, job_id=job.id, candidate_profile_version_id=version.id,
            job_intelligence_id=intelligence.id, model_id="match_v1", model_hash=sha("m"),
            coverage=Decimal("1"), verdict=MatchVerdict.GOOD,
        )
    )
    assert_integrity_error(db_session, "fk_job_matches_candidate_profile_version")

    db_session.add(
        JobMatch(
            user_id=user.id, job_id=other_job.id, candidate_profile_version_id=version.id,
            job_intelligence_id=intelligence.id, model_id="match_v1", model_hash=sha("m"),
            coverage=Decimal("1"), verdict=MatchVerdict.GOOD,
        )
    )
    assert_integrity_error(db_session, "fk_job_matches_job_intelligence")


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"score": Decimal("100.01")}, "ck_job_matches_score"),
        ({"score": Decimal("-1")}, "ck_job_matches_score"),
        ({"coverage": Decimal("1.001")}, "ck_job_matches_coverage"),
        ({"is_blocked": True}, "ck_job_matches_blocked"),
        ({"verdict": MatchVerdict.BLOCKED}, "ck_job_matches_blocked"),
    ],
)
def test_match_check_constraints(db_session: Session, values: dict, constraint: str) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    db_session.commit()
    match = JobMatch(
        user_id=user.id, job_id=job.id, candidate_profile_version_id=version.id,
        job_intelligence_id=intelligence.id, model_id="match_v1", model_hash=sha("m"),
        coverage=Decimal("0.5"), verdict=MatchVerdict.WEAK,
    )
    for key, value in values.items():
        setattr(match, key, value)
    db_session.add(match)
    assert_integrity_error(db_session, constraint)


def test_blocked_match(db_session: Session) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    match = make_match(db_session, user, job, version, intelligence, score=Decimal("0"),
                       verdict=MatchVerdict.BLOCKED, is_blocked=True)
    db_session.commit()
    assert match.is_blocked is True


def test_invalid_match_documents_are_rejected(db_session: Session) -> None:
    with pytest.raises(ValueError, match="unique"):
        JobMatch(criteria=[{"key": "a", "status": "met", "weight": 1}] * 2)
    with pytest.raises(ValueError):
        JobMatch(caps=[{"key": "a", "max_score": 120, "reason_code": "r"}])
    with pytest.raises(ValueError, match="SHA-256"):
        JobMatch(model_hash="match_v1")


def test_matches_are_immutable_except_for_the_current_pointer(db_session: Session) -> None:
    user, job, version, intelligence = setup_match_inputs(db_session)
    match = make_match(db_session, user, job, version, intelligence)
    db_session.commit()

    match.is_current = False
    db_session.commit()
    assert match.is_current is False

    match.score = Decimal("99")
    with pytest.raises(ImmutableRecordError, match="score"):
        db_session.flush()
    db_session.rollback()

    match.criteria = []
    with pytest.raises(ImmutableRecordError, match="criteria"):
        db_session.flush()
    db_session.rollback()


def test_current_matches_ordered_by_score(db_session: Session) -> None:
    user = make_user(db_session)
    version = make_version(db_session, user)
    scores = [Decimal("40"), None, Decimal("95"), Decimal("70")]
    for index, score in enumerate(scores):
        job, _ = make_job(db_session, key=str(index))
        make_match(db_session, user, job, version, make_intelligence(db_session, job),
                   score=score,
                   verdict=MatchVerdict.INSUFFICIENT_DATA if score is None else MatchVerdict.GOOD)
    db_session.commit()

    ordered = db_session.scalars(
        select(JobMatch.score)
        .where(JobMatch.user_id == user.id, JobMatch.is_current)
        .order_by(JobMatch.score.desc().nulls_last())
    ).all()
    assert ordered == [Decimal("95.00"), Decimal("70.00"), Decimal("40.00"), None]
