"""V0.5 JSONB documents: candidate snapshot, job facts, match results.

Pure Pydantic validation -- no database (see test_intelligence_persistence.py).
"""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.candidate_profile import (
    CANDIDATE_PROFILE_SCHEMA_VERSION,
    CandidateProfileDocument,
    SnapshotSources,
)
from app.schemas.job_facts import JobFacts
from app.schemas.job_match import MatchCaps, MatchCriteria
from tests.intelligence_helpers import (
    candidate_document,
    job_facts,
    match_criteria,
    resume_ref,
    snapshot_sources,
)


def validate(document: dict[str, Any]) -> CandidateProfileDocument:
    return CandidateProfileDocument.model_validate(document)


# --- Candidate profile document ------------------------------------------------------


def test_complete_document_is_valid_and_derives_metadata() -> None:
    document = validate(candidate_document())

    assert document.metadata.sections_provided == [
        "role", "skills", "languages", "location_and_mobility", "preferences",
        "deal_breakers", "credentials", "work_authorization",
    ]
    assert document.metadata.sources == ["user", "resume"]
    assert document.metadata.schema_version == CANDIDATE_PROFILE_SCHEMA_VERSION
    assert document.metadata.taxonomy_version is None
    assert document.preferences.minimum_salary == Decimal("70000.00")
    assert document.skills[1].skill_key is None  # no taxonomy resolution yet
    # A dumped document re-validates to the same JSON (stable for hashing).
    dumped = document.model_dump(mode="json")
    assert validate(dumped).model_dump(mode="json") == dumped


def test_empty_document_is_valid_and_provides_nothing() -> None:
    document = validate({})
    assert document.metadata.sections_provided == []
    assert document.metadata.sources == []
    assert document.work_authorization.authorized_countries == []


def test_user_only_document_has_only_user_origin() -> None:
    document = validate({"preferences": {"work_models": ["remote"]}})
    assert document.metadata.sections_provided == ["preferences"]
    assert document.metadata.sources == ["user"]


@pytest.mark.parametrize(
    "field",
    ["name", "full_name", "email", "phone", "linkedin_url", "resume_text", "age",
     "date_of_birth", "gender", "nationality", "marital_status", "photo", "health"],
)
def test_personal_fields_are_rejected_at_the_top_level(field: str) -> None:
    document = candidate_document()
    document[field] = "x"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        validate(document)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("role", "full_name"),
        ("location_and_mobility", "address"),
        ("work_authorization", "nationality"),
        ("work_authorization", "citizenship"),
        ("metadata", "email"),
    ],
)
def test_personal_fields_are_rejected_inside_sections(section: str, field: str) -> None:
    document = candidate_document()
    document.setdefault(section, {})[field] = "x"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        validate(document)


def test_personal_fields_are_not_part_of_the_schema() -> None:
    schema = CandidateProfileDocument.model_json_schema()
    field_names = {
        name.lower()
        for definition in [schema, *schema["$defs"].values()]
        for name in definition.get("properties", {})
    }
    assert "authorized_countries" in field_names  # sanity: nested fields are collected
    forbidden = {
        "full_name", "first_name", "last_name", "email", "phone", "linkedin_url", "github_url",
        "portfolio_url", "address", "resume_text", "extracted_text", "age", "date_of_birth",
        "birth_date", "gender", "nationality", "citizenship", "marital_status", "photo",
        "health",
    }
    assert field_names & forbidden == set()
    # "name" exists only as a skill/certification name, never a person's.
    assert {d for d, v in schema["$defs"].items() if "name" in v.get("properties", {})} == {
        "SkillEntry", "CertificationEntry"
    }


def test_work_authorization_accepts_iso_country_codes() -> None:
    document = validate(
        {"work_authorization": {"authorized_countries": ["de", "AT", "DE", " ch "]}}
    )
    assert document.work_authorization.authorized_countries == ["AT", "CH", "DE"]
    assert document.metadata.sections_provided == ["work_authorization"]
    assert document.metadata.sources == ["user"]


@pytest.mark.parametrize("code", ["XX", "Germany", "DEU", "D", "EU", ""])
def test_work_authorization_rejects_anything_but_iso_codes(code: str) -> None:
    with pytest.raises(ValidationError, match="ISO 3166-1 alpha-2"):
        validate({"work_authorization": {"authorized_countries": [code]}})


def test_absent_work_authorization_is_unknown_not_negative() -> None:
    document = candidate_document()
    del document["work_authorization"]
    validated = validate(document)
    assert validated.work_authorization.authorized_countries == []
    assert "work_authorization" not in validated.metadata.sections_provided


def test_work_authorization_has_no_origin_it_is_always_user_declared() -> None:
    with pytest.raises(ValidationError):
        validate({"work_authorization": {"authorized_countries": ["DE"], "origin": "resume"}})


def test_resume_values_need_evidence_and_user_values_have_none() -> None:
    document = candidate_document()
    del document["skills"][1]["evidence_ref"]
    with pytest.raises(ValidationError, match="evidence_ref"):
        validate(document)

    document = candidate_document()
    document["skills"][0]["evidence_ref"] = resume_ref("x")
    with pytest.raises(ValidationError, match="user-declared values carry no evidence_ref"):
        validate(document)

    document = candidate_document()
    document["skills"][1]["evidence_ref"] = "page 2"
    with pytest.raises(ValidationError, match="evidence_ref"):
        validate(document)


def test_unknown_origin_is_rejected() -> None:
    document = candidate_document()
    document["skills"][0]["origin"] = "inferred"
    with pytest.raises(ValidationError):
        validate(document)


def test_one_entry_per_skill_language_and_deal_breaker() -> None:
    document = candidate_document()
    document["skills"].append({"name": "sap abap", "origin": "user"})
    with pytest.raises(ValidationError, match="skills must be unique"):
        validate(document)

    document = candidate_document()
    document["languages"].append({"language_key": "DE", "origin": "user"})
    with pytest.raises(ValidationError, match="languages must be unique"):
        validate(document)

    document = candidate_document()
    document["deal_breakers"].append({"type": "minimum_salary", "enabled": False})
    with pytest.raises(ValidationError, match="unique per type"):
        validate(document)


def test_deal_breakers_keep_the_v01_representation() -> None:
    document = validate(
        {"deal_breakers": [{"type": "employment_type", "value": "contract", "enabled": False}]}
    )
    assert document.model_dump(mode="json")["deal_breakers"] == [
        {"type": "employment_type", "value": "contract", "enabled": False}
    ]


def test_skill_entry_supports_all_fields() -> None:
    skill = validate(candidate_document()).skills[0]
    assert skill.model_dump(mode="json") == {
        "origin": "user",
        "evidence_ref": None,
        "name": "SAP ABAP",
        "skill_key": None,
        "category": "programming_language",
        "proficiency": "expert",
        "years": 8.0,
        "last_used": "2026-09",
    }


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("skills", 0, "last_used"), "09/2026"),
        (("skills", 0, "last_used"), "2026-13"),
        (("skills", 0, "years"), -1),
        (("skills", 0, "proficiency"), "guru"),
        (("languages", 0, "language_key"), "german"),
        (("languages", 0, "level"), "fluent"),
        (("preferences", "currency"), "EURO"),
        (("preferences", "work_models"), ["office"]),
        (("role", "seniority_level", "value"), "principal"),
        (("location_and_mobility", "current_location", "value", "country"), "XX"),
        (("location_and_mobility", "relocation"), "perhaps"),
        (("credentials", "education", 0, "degree_level"), "phd"),
    ],
)
def test_invalid_values_are_rejected(path: tuple[Any, ...], value: Any) -> None:
    document = candidate_document()
    target: Any = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        validate(document)


def test_desired_salary_cannot_be_below_minimum() -> None:
    document = candidate_document()
    document["preferences"]["desired_salary"] = "50000"
    with pytest.raises(ValidationError, match="desired_salary"):
        validate(document)


def test_declared_metadata_must_match_the_content() -> None:
    document = candidate_document()
    document["metadata"] = {"sections_provided": ["role"]}
    with pytest.raises(ValidationError, match="metadata.sections_provided"):
        validate(document)

    document = candidate_document()
    document["metadata"] = {"sources": ["user"]}
    with pytest.raises(ValidationError, match="metadata.sources"):
        validate(document)


def test_equal_content_serializes_identically() -> None:
    document = candidate_document()
    other = candidate_document()
    other["preferences"]["minimum_salary"] = "70000.00"
    other["work_authorization"]["authorized_countries"] = ["de", "DE"]
    assert validate(document).model_dump(mode="json") == validate(other).model_dump(mode="json")


def test_snapshot_sources_validate_origin_and_uniqueness() -> None:
    sources = SnapshotSources.model_validate(snapshot_sources())
    assert [s.record_type for s in sources.root] == [
        "professional_profile", "user_skills", "resume_profile"
    ]

    with pytest.raises(ValidationError, match="origin 'resume'"):
        SnapshotSources.model_validate(
            [{"origin": "user", "record_type": "resume_profile", "record_id": None}]
        )
    with pytest.raises(ValidationError, match="unique"):
        SnapshotSources.model_validate(
            [{"origin": "user", "record_type": "user_skills"}] * 2
        )


# --- Job facts -----------------------------------------------------------------------


def test_job_facts_cover_every_placeholder() -> None:
    facts = JobFacts.model_validate(job_facts())
    assert facts.salary is not None and facts.salary.value.minimum == Decimal("70000.00")
    assert facts.authorization is not None
    assert facts.authorization.value.countries == ["DE"]
    assert facts.document_language is not None and facts.document_language.value == "de"
    dumped = facts.model_dump(mode="json")
    assert set(dumped) == {
        "schema_version", "seniority", "employment_type", "work_model", "location", "salary",
        "language", "travel", "authorization", "document_language",
    }
    assert JobFacts.model_validate(dumped).model_dump(mode="json") == dumped


def test_empty_job_facts_mean_unknown() -> None:
    facts = JobFacts.model_validate({})
    assert facts.seniority is None and facts.authorization is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("salary", {"minimum": "90000", "maximum": "70000"}),
        ("salary", {"currency": "EUR"}),
        ("authorization", {"countries": ["Germany"]}),
        ("authorization", {}),
        ("location", []),
        ("language", ["Deutsch"]),
        ("seniority", "rockstar"),
    ],
)
def test_invalid_job_facts_are_rejected(field: str, value: Any) -> None:
    facts = job_facts()
    facts[field]["value"] = value
    with pytest.raises(ValidationError):
        JobFacts.model_validate(facts)


def test_job_facts_need_provenance_and_reject_unknown_keys() -> None:
    facts = job_facts()
    del facts["seniority"]["confidence"]
    with pytest.raises(ValidationError):
        JobFacts.model_validate(facts)

    with pytest.raises(ValidationError, match="Extra inputs"):
        JobFacts.model_validate({"match_score": 90})


# --- Match criteria and caps ---------------------------------------------------------


def test_match_criteria_and_caps_validate() -> None:
    criteria = MatchCriteria.model_validate(match_criteria())
    assert [c.key for c in criteria.root] == ["skills.required", "work_model"]
    caps = MatchCaps.model_validate([{"key": "deal_breaker.minimum_salary", "max_score": 0,
                                      "blocking": True, "reason_code": "below_minimum"}])
    assert caps.root[0].blocking is True


@pytest.mark.parametrize(
    "criterion",
    [
        {"key": "work_model", "status": "unknown", "weight": 0.1, "score": 0.5},
        {"key": "work_model", "status": "met", "weight": -1},
        {"key": "work_model", "status": "met", "weight": 1, "score": 1.5},
        {"key": "Work Model", "status": "met", "weight": 1},
        {"key": "work_model", "status": "maybe", "weight": 1},
    ],
)
def test_invalid_criteria_are_rejected(criterion: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MatchCriteria.model_validate([criterion])


def test_criterion_and_cap_keys_are_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        MatchCriteria.model_validate([match_criteria()[0]] * 2)
    cap = {"key": "a", "max_score": 50, "reason_code": "r"}
    with pytest.raises(ValidationError, match="unique"):
        MatchCaps.model_validate([cap, cap])
    with pytest.raises(ValidationError):
        MatchCaps.model_validate([{**cap, "max_score": 101}])
