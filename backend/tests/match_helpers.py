"""Builders for the V0.6 matching tests: candidate documents and job content.

Domain-neutral on purpose: the same builders serve software, finance,
food-safety and maintenance scenarios.
"""

from decimal import Decimal
from typing import Any

from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel
from app.schemas.candidate_profile import CandidateProfileDocument
from app.services.matching.components import JobView, RequirementView
from app.services.matching.engine import MatchOutcome, evaluate
from app.services.matching.extraction import JobContent, extract
from app.services.taxonomy import TAXONOMY_VERSION, normalize_skill


def skill(name: str, **extra: Any) -> dict[str, Any]:
    result = normalize_skill(name)
    key = result.key if result is not None and result.is_match else None
    return {"name": name, "skill_key": key, "origin": "user", **extra}


def candidate(
    *,
    skills: list[str | dict[str, Any]] = (),  # type: ignore[assignment]
    title: str | None = None,
    previous_titles: list[str] = (),  # type: ignore[assignment]
    seniority: str | None = None,
    years: float | None = None,
    industries: list[str] = (),  # type: ignore[assignment]
    languages: dict[str, str] | None = None,
    education: list[str] = (),  # type: ignore[assignment]
    preferred_locations: list[str] = (),  # type: ignore[assignment]
    current_location: str | None = None,
    relocation: str | None = None,
    travel: str | None = None,
    work_models: list[str] = (),  # type: ignore[assignment]
    employment_types: list[str] = (),  # type: ignore[assignment]
    minimum_salary: int | None = None,
    desired_salary: int | None = None,
    currency: str | None = None,
    target_titles: list[tuple[str, str]] = (),  # type: ignore[assignment]
    preferred_industries: list[str] = (),  # type: ignore[assignment]
    deal_breakers: list[tuple[str, str | None]] = (),  # type: ignore[assignment]
) -> CandidateProfileDocument:
    from app.services.matching.extraction import location_value

    role: dict[str, Any] = {}
    if title:
        role["current_title"] = {"value": title, "origin": "user"}
    if seniority:
        role["seniority_level"] = {"value": seniority, "origin": "user"}
    if years is not None:
        role["years_of_experience"] = {"value": years, "origin": "user"}
    if previous_titles:
        role["previous_titles"] = [
            {
                "value": t,
                "origin": "resume",
                "evidence_ref": f"resume_profile:00000000-0000-4000-8000-00000000e001/w{i}",
            }
            for i, t in enumerate(previous_titles)
        ]
    if industries:
        role["industries"] = list(industries)
    if target_titles:
        role["target_titles"] = [{"title": t, "priority": p} for t, p in target_titles]
    mobility: dict[str, Any] = {}
    if preferred_locations:
        mobility["preferred_locations"] = [location_value(p) for p in preferred_locations]
    if current_location:
        mobility["current_location"] = {"value": location_value(current_location), "origin": "user"}
    if relocation:
        mobility["relocation"] = relocation
    if travel:
        mobility["travel"] = travel
    return CandidateProfileDocument.model_validate(
        {
            "role": role,
            "skills": [s if isinstance(s, dict) else skill(s) for s in skills],
            "languages": [
                {"language_key": code, "level": level, "origin": "user"}
                for code, level in (languages or {}).items()
            ],
            "location_and_mobility": mobility,
            "preferences": {
                "work_models": list(work_models),
                "employment_types": list(employment_types),
                "minimum_salary": minimum_salary,
                "desired_salary": desired_salary,
                "currency": currency,
                "industries": list(preferred_industries),
            },
            "deal_breakers": [
                {"type": kind, "value": value, "enabled": True} for kind, value in deal_breakers
            ],
            "credentials": {
                "education": [
                    {
                        "degree": level,
                        "degree_level": level,
                        "origin": "resume",
                        "evidence_ref": f"resume_profile:00000000-0000-4000-8000-00000000e001/e{i}",
                    }
                    for i, level in enumerate(education)
                ]
            },
            "metadata": {"taxonomy_version": TAXONOMY_VERSION},
        }
    )


def job(
    title: str = "Specialist",
    *,
    description: str | None = None,
    requirements: list[str] = (),  # type: ignore[assignment]
    responsibilities: list[str] = (),  # type: ignore[assignment]
    locations: list[str] = (),  # type: ignore[assignment]
    work_model: str | None = None,
    employment_type: str | None = None,
    seniority: str | None = None,
    salary: tuple[int | None, int | None, str | None, str | None] | None = None,
    industry: str | None = None,
    technologies: list[str] = (),  # type: ignore[assignment]
) -> JobContent:
    salary_min, salary_max, currency, period = salary or (None, None, None, None)
    return JobContent(
        title=title,
        description=description,
        requirements=tuple(requirements),
        responsibilities=tuple(responsibilities),
        technologies=tuple(technologies),
        locations=tuple(locations),
        salary_min=Decimal(salary_min) if salary_min is not None else None,
        salary_max=Decimal(salary_max) if salary_max is not None else None,
        salary_currency=currency,
        salary_period=SalaryPeriod(period) if period else None,
        employment_type=EmploymentTypeValue(employment_type) if employment_type else None,
        work_model=RemotePreference(work_model) if work_model else None,
        seniority=SeniorityLevel(seniority) if seniority else None,
        industry=industry,
    )


def view(content: JobContent) -> JobView:
    extraction = extract(content)
    return JobView(
        facts=extraction.facts,
        requirements=tuple(RequirementView.from_extracted(r) for r in extraction.requirements),
    )


def run(cand: CandidateProfileDocument, content: JobContent) -> MatchOutcome:
    return evaluate(cand, view(content))


def component(outcome: MatchOutcome, key: str):  # type: ignore[no-untyped-def]
    result = outcome.evidence.component(key)
    assert result is not None
    return result


def items(outcome: MatchOutcome, key: str) -> dict[str, tuple[str, str | None]]:
    """item key -> (status, importance)."""
    return {i.key: (i.status, i.importance) for i in component(outcome, key).items}
