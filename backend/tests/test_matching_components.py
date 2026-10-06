"""V0.6 scoring components: one deterministic comparison each.

Covers the V0.6 test checklist: skills (taxonomy aliases, missing, unknown),
experience, seniority matrix, title, languages (no CEFR from prose),
education, location, work model, employment type, salary and deal breakers.
"""

import pytest

from app.models.professional_profile import SeniorityLevel
from app.services.matching import model
from tests.match_helpers import candidate, component, items, job, run

# --- Skills -----------------------------------------------------------------------


def test_same_skill_matches() -> None:
    outcome = run(candidate(skills=["Python"]), job(requirements=["Python"]))
    assert items(outcome, "skills")["python"][0] == "match"
    assert component(outcome, "skills").status == "match"


def test_skill_aliases_match_through_the_taxonomy() -> None:
    cand = candidate(skills=["JavaScript", "React", "PostgreSQL"])
    outcome = run(cand, job(requirements=["JS", "React.js", "Postgres"]))
    assert items(outcome, "skills") == {
        "javascript": ("match", "unspecified"),
        "react": ("match", "unspecified"),
        "postgresql": ("match", "unspecified"),
    }


def test_java_does_not_match_javascript() -> None:
    outcome = run(candidate(skills=["Java"]), job(requirements=["JavaScript"]))
    assert items(outcome, "skills")["javascript"][0] == "missing"
    assert component(outcome, "skills").status == "mismatch"


def test_missing_skill() -> None:
    outcome = run(candidate(skills=["Python"]), job(requirements=["Kubernetes required"]))
    assert items(outcome, "skills")["kubernetes"] == ("missing", "required")
    assert outcome.caps and outcome.caps[0]["key"] == "required_missing.skills"


def test_unknown_term_is_not_missing() -> None:
    # "GCP" can't be normalized (ambiguous): UNKNOWN, not scored.
    outcome = run(candidate(skills=["Python"]), job(requirements=["Python", "GCP"]))
    skills = component(outcome, "skills")
    assert items(outcome, "skills")["gcp"] == ("unknown", "unspecified")
    assert skills.score == 1.0 and skills.status == "match"


def test_required_counts_more_than_preferred() -> None:
    cand = candidate(skills=["Python"])
    missing_preferred = run(cand, job(requirements=["Python required", "Docker is a plus"]))
    missing_required = run(cand, job(requirements=["Python is a plus", "Docker required"]))
    preferred_gap = component(missing_preferred, "skills").score
    assert preferred_gap > component(missing_required, "skills").score
    assert not missing_preferred.caps
    assert missing_required.caps


def test_alternatives_are_satisfied_by_any() -> None:
    outcome = run(candidate(skills=["Azure"]), job(requirements=["AWS or Azure"]))
    found = items(outcome, "skills")
    assert found["microsoft_azure"][0] == "match"
    assert found["aws"][0] == "unknown"  # an alternative, not a gap
    assert component(outcome, "skills").score == 1.0


def test_fewer_skill_years_is_partial() -> None:
    from tests.match_helpers import skill

    cand = candidate(skills=[skill("Python", years=1)])
    outcome = run(cand, job(requirements=["3+ years Python development"]))
    assert items(outcome, "skills")["python"][0] == "partial"


def test_contextual_skills_are_never_scored() -> None:
    outcome = run(candidate(skills=["Python"]), job(responsibilities=["Maintain Kafka clusters"]))
    skills = component(outcome, "skills")
    assert skills.status == "unknown" and skills.reason_code == "no_skill_requirements"
    assert items(outcome, "skills")["apache_kafka"] == ("unknown", "contextual")


def test_unknown_candidate_skills_are_not_a_mismatch() -> None:
    outcome = run(candidate(), job(requirements=["Python"]))
    skills = component(outcome, "skills")
    assert skills.status == "unknown" and skills.score is None
    assert skills.reason_code == "candidate_skills_unknown"


# --- Experience ---------------------------------------------------------------------


def test_experience_meets() -> None:
    outcome = run(candidate(years=6), job(requirements=["3+ years of experience"]))
    exp = component(outcome, "experience")
    assert (exp.status, exp.score) == ("match", 1.0)


def test_experience_gap() -> None:
    outcome = run(candidate(years=2), job(requirements=["5+ years of experience"]))
    exp = component(outcome, "experience")
    assert (exp.status, exp.reason_code) == ("mismatch", "experience_gap")


def test_experience_slightly_below_is_partial() -> None:
    outcome = run(candidate(years=4), job(requirements=["5+ years of experience"]))
    assert component(outcome, "experience").status == "partial"


def test_unstated_experience_is_unknown() -> None:
    outcome = run(candidate(years=1), job(requirements=["Python"]))
    exp = component(outcome, "experience")
    assert (exp.status, exp.reason_code) == ("unknown", "experience_not_stated")


# --- Seniority (the documented matrix) ----------------------------------------------


@pytest.mark.parametrize(
    ("cand", "asked", "status"),
    [
        ("mid", "mid", "match"),
        ("senior", "mid", "match"),
        ("junior", "senior", "mismatch"),
        ("senior", "junior", "partial"),
        ("mid", "senior", "partial"),
        ("intern", "director", "mismatch"),
    ],
)
def test_seniority_matrix(cand: str, asked: str, status: str) -> None:
    outcome = run(candidate(seniority=cand), job(seniority=asked))
    seniority = component(outcome, "seniority")
    assert seniority.status == status
    assert seniority.score == model.SENIORITY_MATRIX[SeniorityLevel(cand)][SeniorityLevel(asked)]


def test_matrix_is_complete_and_diagonal_is_perfect() -> None:
    for level in SeniorityLevel:
        assert set(model.SENIORITY_MATRIX[level]) == set(SeniorityLevel)
        assert model.SENIORITY_MATRIX[level][level] == 1.0


def test_unknown_seniority() -> None:
    assert component(run(candidate(seniority="mid"), job()), "seniority").status == "unknown"
    assert component(run(candidate(), job(seniority="mid")), "seniority").status == "unknown"


# --- Title ----------------------------------------------------------------------------


def test_matching_title() -> None:
    outcome = run(candidate(title="Software Engineer"), job("Senior Software Engineer (m/w/d)"))
    title = component(outcome, "title")
    assert (title.label, title.score) == ("strong_match", 1.0)


def test_related_title_from_resume_history() -> None:
    outcome = run(
        candidate(title="Accountant", previous_titles=["Financial Analyst"]),
        job("Financial Analyst"),
    )
    assert component(outcome, "title").label == "related_role"


def test_unrelated_titles() -> None:
    outcome = run(candidate(title="Software Engineer"), job("Accountant"))
    title = component(outcome, "title")
    assert (title.label, title.score) == ("weak_match", 0.0)


def test_unknown_titles_are_not_guessed() -> None:
    # "Software Developer" isn't a taxonomy alias of "Software Engineer".
    outcome = run(candidate(title="Software Engineer"), job("Software Developer"))
    assert component(outcome, "title").status == "unknown"


def test_identical_role_outside_the_vocabulary() -> None:
    outcome = run(candidate(title="Food Safety Manager"), job("Senior Food Safety Manager"))
    assert component(outcome, "title").label == "strong_match"


# --- Industry ---------------------------------------------------------------------------


def test_industry() -> None:
    assert component(run(candidate(industries=["Finance"]), job(industry="finance")),
                     "industry").status == "match"
    assert component(run(candidate(industries=["Retail"]), job(industry="Finance")),
                     "industry").score == model.INDUSTRY_SCORES["no_match"]
    assert component(run(candidate(), job(industry="Finance")), "industry").status == "unknown"
    assert component(run(candidate(industries=["Retail"]), job()), "industry").status == "unknown"


# --- Languages ----------------------------------------------------------------------------


def test_same_language_and_level() -> None:
    outcome = run(
        candidate(languages={"de": "B2", "en": "C1"}),
        job(requirements=["German B2 and English skills"]),
    )
    assert items(outcome, "languages") == {
        "de": ("match", "unspecified"),
        "en": ("match", "unspecified"),
    }


def test_required_language_missing() -> None:
    outcome = run(candidate(languages={"en": "C1"}), job(requirements=["German C1 required"]))
    assert items(outcome, "languages")["de"] == ("missing", "required")
    assert any(c["key"] == "required_missing.languages" for c in outcome.caps)


def test_language_level_below() -> None:
    outcome = run(candidate(languages={"de": "B1"}), job(requirements=["German (C1)"]))
    assert items(outcome, "languages")["de"][0] == "partial"


def test_native_satisfies_cefr() -> None:
    outcome = run(candidate(languages={"de": "native"}), job(requirements=["German (C2)"]))
    assert items(outcome, "languages")["de"][0] == "match"


def test_unknown_level_is_not_compared_or_penalized() -> None:
    outcome = run(candidate(languages={"de": "unknown"}), job(requirements=["German (B2)"]))
    lang = component(outcome, "languages")
    assert lang.items[0].reason_code == "language_level_not_compared"
    assert lang.score == 1.0


def test_fluent_is_not_a_cefr_level() -> None:
    outcome = run(candidate(languages={"en": "B1"}), job(requirements=["Fluent English"]))
    # The job states no explicit level, so only the language is compared.
    assert items(outcome, "languages")["en"][0] == "match"


# --- Education ------------------------------------------------------------------------------


def test_master_meets_bachelor_requirement() -> None:
    outcome = run(candidate(education=["master"]), job(requirements=["Bachelor's degree"]))
    assert component(outcome, "education").status == "match"


def test_bachelor_below_master_requirement() -> None:
    outcome = run(candidate(education=["bachelor"]), job(requirements=["Master's degree required"]))
    assert items(outcome, "education")["master"] == ("missing", "required")
    assert any(c["key"] == "required_missing.education" for c in outcome.caps)


def test_equivalent_experience_softens_education() -> None:
    outcome = run(
        candidate(education=["bachelor"]),
        job(requirements=["Master's degree or equivalent experience"]),
    )
    assert items(outcome, "education")["master"][0] == "partial"
    assert not outcome.caps


def test_unknown_education() -> None:
    assert component(run(candidate(education=["master"]), job()), "education").status == "unknown"
    assert component(run(candidate(), job(requirements=["Bachelor's degree"])),
                     "education").status == "unknown"


# --- Location ---------------------------------------------------------------------------


def test_preferred_city() -> None:
    outcome = run(candidate(preferred_locations=["Cologne"]), job(locations=["Köln, Germany"]))
    assert component(outcome, "location").reason_code == "in_preferred_location"


def test_preferred_country_covers_its_cities() -> None:
    outcome = run(candidate(preferred_locations=["Germany"]), job(locations=["Munich"]))
    assert component(outcome, "location").status == "match"


def test_different_city_depends_on_relocation() -> None:
    def score(relocation: str | None) -> float | None:
        cand = candidate(preferred_locations=["Cologne"], relocation=relocation)
        return component(run(cand, job(locations=["München"])), "location").score

    assert score("yes") > score("maybe") > score(None) > score("no") == 0.0


def test_remote_job_fits_any_location() -> None:
    outcome = run(
        candidate(preferred_locations=["Cologne"], relocation="no"),
        job(locations=["Munich"], work_model="remote"),
    )
    assert component(outcome, "location").reason_code == "remote_job"
    assert not outcome.is_blocked


def test_remote_restricted_to_another_country() -> None:
    outcome = run(
        candidate(preferred_locations=["Cologne"]), job(locations=["Remote - United States"])
    )
    assert component(outcome, "location").reason_code == "remote_in_other_country"


def test_hybrid_in_preferred_city() -> None:
    outcome = run(
        candidate(preferred_locations=["Berlin"], work_models=["hybrid"]),
        job(locations=["Berlin"], work_model="hybrid"),
    )
    assert component(outcome, "location").status == "match"
    assert component(outcome, "work_model").status == "match"


def test_current_location_counts() -> None:
    outcome = run(candidate(current_location="Hamburg"), job(locations=["Hamburg"]))
    assert component(outcome, "location").reason_code == "at_current_location"


def test_unknown_locations() -> None:
    assert component(run(candidate(), job(locations=["Berlin"])), "location").status == "unknown"
    assert component(run(candidate(preferred_locations=["Berlin"]), job()),
                     "location").status == "unknown"


# --- Work model ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wanted", "offered", "status"),
    [
        (["remote"], "remote", "match"),
        (["hybrid", "remote"], "hybrid", "match"),
        (["remote"], "hybrid", "partial"),
        (["remote"], "onsite", "mismatch"),
        (["flexible"], "onsite", "match"),
    ],
)
def test_work_model(wanted: list[str], offered: str, status: str) -> None:
    outcome = run(candidate(work_models=wanted), job(work_model=offered))
    assert component(outcome, "work_model").status == status


def test_unknown_work_model() -> None:
    assert component(run(candidate(work_models=["remote"]), job()), "work_model").status == (
        "unknown"
    )


# --- Employment type ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wanted", "offered", "status"),
    [
        (["full_time"], "full_time", "match"),
        (["full_time"], "part_time", "mismatch"),
        (["working_student"], "working_student", "match"),
        (["full_time", "contract"], "contract", "match"),
    ],
)
def test_employment_type(wanted: list[str], offered: str, status: str) -> None:
    outcome = run(candidate(employment_types=wanted), job(employment_type=offered))
    assert component(outcome, "employment_type").status == status


def test_employment_type_mismatch_affects_preference_not_job_match() -> None:
    cand = candidate(skills=["Python"], employment_types=["full_time"])
    fits = run(cand, job(requirements=["Python"], employment_type="full_time"))
    differs = run(cand, job(requirements=["Python"], employment_type="part_time"))
    assert fits.job_score == differs.job_score
    assert fits.preference_score > differs.preference_score


# --- Salary --------------------------------------------------------------------------------


def test_salary_inside_range() -> None:
    cand = candidate(minimum_salary=70000, currency="EUR")
    outcome = run(cand, job(salary=(75000, 85000, "EUR", "year")))
    assert component(outcome, "salary").status == "match"


def test_salary_below_minimum() -> None:
    cand = candidate(minimum_salary=70000, currency="EUR")
    outcome = run(cand, job(salary=(55000, 65000, "EUR", "year")))
    salary = component(outcome, "salary")
    assert (salary.status, salary.reason_code) == ("mismatch", "salary_below_minimum")


def test_unknown_salary() -> None:
    cand = candidate(minimum_salary=70000, currency="EUR")
    assert component(run(cand, job()), "salary").reason_code == "salary_not_stated"


def test_salary_never_converts_currencies_or_hours() -> None:
    cand = candidate(minimum_salary=70000, currency="EUR")
    assert component(run(cand, job(salary=(80000, 90000, "USD", "year"))),
                     "salary").status == "unknown"
    assert component(run(cand, job(salary=(40, 50, "EUR", "hour"))), "salary").status == "unknown"
    monthly = component(run(cand, job(salary=(6000, 7000, "EUR", "month"))), "salary")
    assert monthly.status == "match"  # 84,000 a year


# --- Travel ----------------------------------------------------------------------------------


def test_travel() -> None:
    cand = candidate(travel="none")
    outcome = run(cand, job(requirements=["Travel up to 50%"]))
    assert component(outcome, "travel").status == "mismatch"
    assert component(run(cand, job()), "travel").status == "unknown"


# --- Deal breakers ----------------------------------------------------------------------------


def blocks(outcome) -> list[str]:  # type: ignore[no-untyped-def]
    return [d.type for d in outcome.evidence.deal_breakers if d.status == "hard_block"]


def test_explicit_deal_breaker_hard_blocks() -> None:
    cand = candidate(employment_types=["full_time"], deal_breakers=[("employment_type", None)])
    outcome = run(cand, job(employment_type="working_student"))
    assert blocks(outcome) == ["employment_type"]
    assert outcome.is_blocked and outcome.verdict.value == "blocked"


def test_deal_breaker_value_names_excluded_types() -> None:
    cand = candidate(deal_breakers=[("employment_type", "working_student, internship")])
    assert blocks(run(cand, job(employment_type="internship"))) == ["employment_type"]
    assert blocks(run(cand, job(employment_type="full_time"))) == []


def test_relocation_no_against_onsite_elsewhere_blocks() -> None:
    cand = candidate(preferred_locations=["Cologne"], relocation="no")
    outcome = run(cand, job(locations=["München"], work_model="onsite"))
    assert blocks(outcome) == ["relocation_preference"]


def test_relocation_unknown_work_model_does_not_block() -> None:
    cand = candidate(preferred_locations=["Cologne"], relocation="no")
    outcome = run(cand, job(locations=["München"]))
    assert not outcome.is_blocked
    assert [d.status for d in outcome.evidence.deal_breakers] == ["unknown"]


@pytest.mark.parametrize(
    ("breaker", "cand_kwargs", "job_kwargs"),
    [
        ("minimum_salary", {"minimum_salary": 70000, "currency": "EUR"},
         {"salary": (50000, 60000, "EUR", "year")}),
        ("remote_requirement", {}, {"work_model": "onsite"}),
        ("location", {"preferred_locations": ["Cologne"]}, {"locations": ["Berlin"]}),
        ("relocation", {"preferred_locations": ["Cologne"]},
         {"locations": ["Berlin"], "work_model": "hybrid"}),
        ("travel", {}, {"requirements": ["Extensive travel"]}),
    ],
)
def test_each_deal_breaker_type(breaker: str, cand_kwargs: dict, job_kwargs: dict) -> None:
    cand = candidate(deal_breakers=[(breaker, None)], **cand_kwargs)
    assert blocks(run(cand, job(**job_kwargs))) == [breaker]


def test_deal_breakers_never_block_on_unknown_data() -> None:
    cand = candidate(
        minimum_salary=70000, currency="EUR", preferred_locations=["Cologne"],
        deal_breakers=[("minimum_salary", None), ("remote_requirement", None), ("travel", None),
                       ("location", None), ("relocation", None)],
    )
    outcome = run(cand, job())
    assert not outcome.is_blocked
    assert {d.status for d in outcome.evidence.deal_breakers} == {"unknown"}


def test_no_deal_breaker_is_invented() -> None:
    # Strong preference mismatches without an explicit deal breaker never block.
    cand = candidate(
        work_models=["remote"], employment_types=["full_time"], minimum_salary=90000,
        currency="EUR",
    )
    outcome = run(
        cand, job(work_model="onsite", employment_type="part_time",
                  salary=(30000, 40000, "EUR", "year")),
    )
    assert not outcome.is_blocked and outcome.evidence.deal_breakers == []


def test_disabled_deal_breakers_are_ignored() -> None:
    from app.schemas.candidate_profile import CandidateProfileDocument

    cand = candidate(deal_breakers=[("remote_requirement", None)])
    data = cand.model_dump(mode="json")
    data["deal_breakers"][0]["enabled"] = False
    outcome = run(CandidateProfileDocument.model_validate(data), job(work_model="onsite"))
    assert not outcome.is_blocked
