"""V0.6 deterministic job requirement extraction (canonical job -> facts + requirements)."""

from decimal import Decimal

import pytest

from app.models.job_requirement import RequirementCategory, RequirementKind
from app.services.matching.extraction import (
    JOB_EXTRACTOR_VERSION,
    JobContent,
    classify_section_heading,
    description_passages,
    extract,
)
from app.services.taxonomy import TAXONOMY_VERSION
from tests.match_helpers import job


def requirements(content: JobContent, category: RequirementCategory) -> dict:
    found = {}
    for r in extract(content).requirements:
        if r.category is category:
            key = r.skill_key or r.value_key or r.value_text or str(r.min_years)
            found[key] = r
    return found


SECTIONED = """About the team
We build things.

Your tasks
• Operate services in Python and Kafka
• You will use Terraform daily

Must have
• Python and SQL

Qualifications
• Experience with Docker

Nice to have
• Kubernetes
• AWS is a plus"""


def test_heading_classification() -> None:
    assert classify_section_heading("Must have") == "required"
    assert classify_section_heading("Minimum qualifications") == "required"
    assert classify_section_heading("Nice to have") == "preferred"
    assert classify_section_heading("Preferred qualifications") == "preferred"
    assert classify_section_heading("Wünschenswert") == "preferred"
    assert classify_section_heading("Qualifications") == "requirements"
    assert classify_section_heading("Dein Profil") == "requirements"
    assert classify_section_heading("Your tasks") == "responsibilities"
    assert classify_section_heading("Benefits") is None


def test_sections_decide_required_preferred_and_unspecified() -> None:
    skills = requirements(job(description=SECTIONED), RequirementCategory.SKILL)
    assert skills["python"].kind is RequirementKind.REQUIRED
    assert skills["sql"].kind is RequirementKind.REQUIRED
    # "Qualifications" asks for it without saying how strictly: not guessed.
    assert skills["docker"].kind is RequirementKind.UNSPECIFIED
    assert skills["kubernetes"].kind is RequirementKind.PREFERRED
    assert skills["aws"].kind is RequirementKind.PREFERRED
    # Responsibilities are context, never requirements.
    assert skills["terraform"].kind is RequirementKind.CONTEXTUAL
    assert skills["apache_kafka"].kind is RequirementKind.CONTEXTUAL


def test_strongest_statement_wins() -> None:
    # Python is mentioned in the responsibilities AND under "Must have".
    assert requirements(job(description=SECTIONED), RequirementCategory.SKILL)[
        "python"
    ].kind is RequirementKind.REQUIRED


def test_item_markers_override_the_section() -> None:
    content = job(
        requirements=[
            "Knowledge of Docker (preferred)",
            "Python is required",
            "Ideally experience with Snowflake",
            "SQL",
        ]
    )
    skills = requirements(content, RequirementCategory.SKILL)
    assert skills["docker"].kind is RequirementKind.PREFERRED
    assert skills["python"].kind is RequirementKind.REQUIRED
    assert skills["snowflake"].kind is RequirementKind.PREFERRED
    assert skills["sql"].kind is RequirementKind.UNSPECIFIED


def test_mixed_markers_in_one_item_are_split() -> None:
    skills = requirements(
        job(requirements=["Python required, Docker is a plus"]), RequirementCategory.SKILL
    )
    assert skills["python"].kind is RequirementKind.REQUIRED
    assert skills["docker"].kind is RequirementKind.PREFERRED


def test_skills_use_taxonomy_aliases() -> None:
    skills = requirements(
        job(requirements=["JS, React.js and Postgres", "k8s"]), RequirementCategory.SKILL
    )
    assert {"javascript", "react", "postgresql", "kubernetes"} <= set(skills)


def test_java_is_not_javascript() -> None:
    skills = requirements(job(requirements=["Java"]), RequirementCategory.SKILL)
    assert set(skills) == {"java"}


def test_alternatives_form_a_group() -> None:
    skills = requirements(
        job(requirements=["Experience with AWS or Azure"]), RequirementCategory.SKILL
    )
    assert skills["aws"].value_key == skills["microsoft_azure"].value_key == "any_of:1"


def test_a_skill_also_asked_for_alone_is_not_an_alternative() -> None:
    skills = requirements(
        job(requirements=["AWS or Azure", "Strong AWS knowledge"]), RequirementCategory.SKILL
    )
    assert skills["aws"].value_key is None
    assert skills["microsoft_azure"].value_key == "any_of:1"


def test_ambiguous_acronyms_are_kept_as_unknown_requirements() -> None:
    content = job(requirements=["Experience with GCP", "Knowledge of SAP"])
    unknown = [r for r in extract(content).requirements if r.category is RequirementCategory.SKILL]
    assert {r.value_text for r in unknown} == {"GCP", "SAP"}
    assert all(r.skill_key is None for r in unknown)


def test_ordinary_words_and_verbs_are_not_skills() -> None:
    content = job(
        requirements=[
            "You excel at communication",
            "Ready to go the extra mile",
            "Lean into ambiguity",
        ]
    )
    assert requirements(content, RequirementCategory.SKILL) == {}


def test_skill_specific_years() -> None:
    skills = requirements(
        job(requirements=["3+ years Python development"]), RequirementCategory.SKILL
    )
    assert skills["python"].min_years == Decimal(3)


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("5+ years of professional experience", 5),
        ("3-5 years experience in accounting", 3),
        ("At least two years of relevant experience", 2),
        ("Mindestens 3 Jahre Berufserfahrung", 3),
        ("Berufserfahrung von mindestens 4 Jahren", 4),
        ("Experience of at least 6 years", 6),
    ],
)
def test_experience_requirement(text: str, years: int) -> None:
    found = requirements(job(requirements=[text]), RequirementCategory.EXPERIENCE)
    assert [r.min_years for r in found.values()] == [Decimal(years)]


def test_no_experience_without_a_number() -> None:
    content = job(requirements=["Several years of experience", "Mehrjährige Berufserfahrung"])
    assert requirements(content, RequirementCategory.EXPERIENCE) == {}


def test_experience_outside_requirements_needs_a_candidate_cue() -> None:
    assert requirements(
        job(description="You have 4+ years of experience in maintenance."),
        RequirementCategory.EXPERIENCE,
    )
    assert not requirements(
        job(description="Our company has 25 years of experience in logistics."),
        RequirementCategory.EXPERIENCE,
    )


def test_languages_with_explicit_levels_only() -> None:
    content = job(requirements=["German (B2) and fluent English", "Deutschkenntnisse"])
    languages = {
        r.value_key: r for r in extract(content).requirements
        if r.category is RequirementCategory.LANGUAGE
    }
    assert languages["de"].level == "B2"
    # "fluent" is never mapped to a CEFR level (V0.5.1 decision).
    assert languages["en"].level is None


def test_language_needs_a_language_cue() -> None:
    content = job(requirements=["Experience working with German clients"])
    assert requirements(content, RequirementCategory.LANGUAGE) == {}


def test_education_minimum_level_and_equivalent() -> None:
    content = job(
        requirements=["Bachelor's or Master's degree in Finance or equivalent experience"]
    )
    found = requirements(content, RequirementCategory.EDUCATION)
    assert set(found) == {"bachelor"}
    assert found["bachelor"].level == "or_equivalent"


def test_education_needs_an_education_cue() -> None:
    assert not requirements(
        job(requirements=["Scrum Master certification"]), RequirementCategory.EDUCATION
    )


def test_facts_from_canonical_fields() -> None:
    facts = extract(
        job(
            "Senior Accountant (m/w/d)",
            locations=["Köln, Deutschland", "Remote - Germany"],
            work_model="hybrid",
            employment_type="full_time",
            seniority="senior",
            salary=(60000, 70000, "EUR", "year"),
            industry="Finance",
        )
    ).facts
    assert facts.title and facts.title.value == "Senior Accountant (m/w/d)"
    assert facts.seniority and facts.seniority.value.value == "senior"
    assert facts.work_model and facts.work_model.value.value == "hybrid"
    assert facts.employment_type and facts.employment_type.value.value == "full_time"
    assert facts.salary and facts.salary.value.maximum == Decimal("70000.00")
    assert facts.industry and facts.industry.value == "Finance"
    assert facts.location is not None
    cologne, remote = facts.location.value
    assert (cologne.city, cologne.country, cologne.is_remote) == ("Cologne", "DE", False)
    assert (remote.country, remote.is_remote) == ("DE", True)


def test_missing_facts_stay_missing() -> None:
    facts = extract(job("Accountant")).facts
    assert facts.salary is None and facts.industry is None and facts.work_model is None
    assert facts.travel is None and facts.seniority is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Travel up to 50% of the time", "frequent"),
        ("Travel up to 10%", "occasional"),
        ("Occasional travel to customer sites", "occasional"),
        ("Extensive travel across Europe", "frequent"),
        ("No travel required", "none"),
    ],
)
def test_travel_only_from_explicit_phrases(text: str, value: str) -> None:
    facts = extract(job(requirements=[text])).facts
    assert facts.travel is not None and facts.travel.value.value == value


def test_v04_technologies_become_context() -> None:
    skills = requirements(job(technologies=["Jira"]), RequirementCategory.SKILL)
    assert skills["jira"].kind is RequirementKind.CONTEXTUAL
    assert skills["jira"].source_field == "technologies"


def test_evidence_is_verbatim_with_offsets() -> None:
    content = job(description=SECTIONED)
    python = requirements(content, RequirementCategory.SKILL)["python"]
    assert python.evidence_text == "Python and SQL"
    assert python.evidence_offset is not None
    assert SECTIONED[python.evidence_offset :].startswith("Python and SQL")


def test_description_passages_keep_headings() -> None:
    passages = description_passages(SECTIONED)
    assert [(p.text, p.section) for p in passages if p.text == "Kubernetes"] == [
        ("Kubernetes", "preferred")
    ]
    inline = description_passages("Must have: Python, SQL")
    assert [(p.text, p.section) for p in inline] == [("Python, SQL", "required")]


def test_domain_neutral_examples() -> None:
    food = requirements(
        job(requirements=["HACCP and GMP certification required", "Forklift experience"]),
        RequirementCategory.SKILL,
    )
    assert food["haccp"].kind is RequirementKind.REQUIRED
    assert "forklift_operation" in food
    finance = requirements(
        job(requirements=["IFRS reporting", "Advanced Excel"]), RequirementCategory.SKILL
    )
    assert {"ifrs", "microsoft_excel"} <= set(finance)


def test_deterministic_and_versioned() -> None:
    content = job(description=SECTIONED, locations=["Berlin"])
    first, second = extract(content), extract(content)
    assert first == second
    assert first.result_hash() == second.result_hash()
    assert first.extractor_version == JOB_EXTRACTOR_VERSION == "job_extractor_v1"
    assert first.taxonomy_version == TAXONOMY_VERSION
    assert content.input_hash() == job(description=SECTIONED, locations=["Berlin"]).input_hash()
    assert content.input_hash() != job(description=SECTIONED, locations=["Munich"]).input_hash()
