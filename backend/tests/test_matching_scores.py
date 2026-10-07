"""V0.6 complete candidate/job combinations: Job Match, Preference Match,
Overall Match, caps and the deterministic recommendation."""

import pytest

from app.models.job_match import MatchVerdict
from app.services.matching import model
from app.services.matching.engine import aggregate, overall, recommendation, round_score
from tests.match_helpers import candidate, component, job, run

# Pinned like the taxonomy fingerprints: changing any weight, threshold or
# matrix requires a new MATCHING_ALGORITHM_VERSION (and a new pin), so a
# stored result is never mistaken for one the new definition produced.
MODEL_PINS = {
    "matching_v1": "fdaa9e60a6e43e5592eda763525ab67113e3ca9012b168d6f711131c1d915cae",
}


def test_model_hash_is_pinned() -> None:
    assert model.MATCHING_ALGORITHM_VERSION in MODEL_PINS
    assert MODEL_PINS[model.MATCHING_ALGORITHM_VERSION] == model.MODEL_HASH


def test_weights_follow_the_documented_model() -> None:
    assert model.JOB_WEIGHTS == {
        "skills": 30, "experience": 20, "title": 10, "seniority": 10, "industry": 10,
        "languages": 5, "education": 5,
    }
    assert sum(model.PREFERENCE_WEIGHTS.values()) == 100
    assert model.OVERALL_WEIGHTS == {"job": 0.7, "preference": 0.3}


DESCRIPTION = """Requirements
• 3+ years of experience in backend development
• Python and SQL
• Fluent English

Nice to have
• Kubernetes"""


def strong_candidate(**overrides) -> object:  # type: ignore[no-untyped-def]
    values = {
        "skills": ["Python", "SQL", "AWS"],
        "title": "Software Engineer",
        "seniority": "senior",
        "years": 6,
        "languages": {"en": "C1", "de": "native"},
        "preferred_locations": ["Cologne"],
        "relocation": "no",
        "work_models": ["hybrid", "onsite"],
        "employment_types": ["full_time"],
        "minimum_salary": 70000,
        "currency": "EUR",
        "target_titles": [("Software Engineer", "primary")],
    }
    values.update(overrides)
    return candidate(**values)


def strong_job(**overrides) -> object:  # type: ignore[no-untyped-def]
    values = {
        "description": DESCRIPTION,
        "locations": ["Köln"],
        "work_model": "hybrid",
        "employment_type": "full_time",
        "seniority": "senior",
        "salary": (75000, 85000, "EUR", "year"),
    }
    values.update(overrides)
    return job("Senior Software Engineer", **values)


def test_strong_fit_everywhere_is_a_strong_match() -> None:
    outcome = run(strong_candidate(), strong_job())
    assert outcome.job_score >= 85
    assert outcome.preference_score >= 85
    assert outcome.overall_score >= 85
    assert outcome.verdict is MatchVerdict.STRONG
    assert not outcome.is_blocked and outcome.caps == []


def test_strong_fit_wrong_location_no_relocation_is_not_recommended() -> None:
    outcome = run(strong_candidate(), strong_job(locations=["München"], work_model="onsite"))
    assert outcome.job_score >= 85  # still a strong professional fit
    assert outcome.preference_score < 75
    assert outcome.is_blocked and outcome.verdict is MatchVerdict.BLOCKED
    assert outcome.overall_score == model.HARD_BLOCK_CAP
    # The raw component scores are never hidden.
    assert outcome.overall_uncapped == overall(outcome.job_score, outcome.preference_score)
    assert outcome.overall_uncapped > outcome.overall_score


def test_job_and_preference_are_separate() -> None:
    fit = run(strong_candidate(), strong_job())
    unwanted = run(
        strong_candidate(relocation="yes"),
        strong_job(employment_type="part_time", work_model="onsite", locations=["München"],
                   salary=(40000, 50000, "EUR", "year")),
    )
    assert unwanted.job_score == fit.job_score
    assert unwanted.preference_score < fit.preference_score - 30


def test_weak_technical_fit() -> None:
    outcome = run(
        candidate(skills=["Excel"], title="Accountant", seniority="junior", years=1,
                  work_models=["hybrid"], preferred_locations=["Cologne"]),
        strong_job(),
    )
    assert outcome.job_score < 40
    assert outcome.verdict is MatchVerdict.WEAK


def test_missing_required_qualification_caps_at_potential() -> None:
    outcome = run(
        strong_candidate(),
        strong_job(description=DESCRIPTION + "\n\nMust have\n• Kubernetes"),
    )
    assert any(cap["key"] == "required_missing.skills" for cap in outcome.caps)
    assert outcome.overall_score <= model.REQUIRED_MISSING_CAP
    assert outcome.verdict in (MatchVerdict.PARTIAL, MatchVerdict.WEAK)


def test_unknown_data_does_not_reduce_scores() -> None:
    cand = strong_candidate()
    full = run(cand, strong_job())
    sparse = run(cand, strong_job(salary=None, employment_type=None))
    # Unknown salary / employment type: excluded, not counted as mismatches.
    assert component(sparse, "salary").status == "unknown"
    assert sparse.preference_score >= full.preference_score - 5
    assert sparse.preference_coverage < full.preference_coverage


def test_too_little_known_is_insufficient_data() -> None:
    outcome = run(candidate(), job("Coordinator"))
    assert outcome.job_score is None and outcome.overall_score is None
    assert outcome.verdict is MatchVerdict.INSUFFICIENT_DATA
    outcome = run(candidate(seniority="mid"), job("Coordinator", seniority="mid"))
    assert outcome.job_score == 100 and outcome.job_coverage < model.MIN_JOB_COVERAGE
    assert outcome.verdict is MatchVerdict.INSUFFICIENT_DATA


@pytest.mark.parametrize(
    ("cand", "content", "verdicts"),
    [
        # Accountant
        (
            candidate(skills=["Accounting", "IFRS", "Excel"], title="Accountant", years=5,
                      seniority="mid", education=["bachelor"]),
            job("Accountant", requirements=["IFRS and accounting experience",
                                            "3+ years of experience", "Bachelor's degree"],
                seniority="mid"),
            {MatchVerdict.STRONG},
        ),
        # Food safety
        (
            candidate(skills=["HACCP", "GMP"], title="Food Safety Manager", years=8),
            job("Food Safety Manager", requirements=["HACCP and SQF required",
                                                     "5+ years of experience"]),
            {MatchVerdict.PARTIAL, MatchVerdict.WEAK},
        ),
        # Maintenance technician
        (
            candidate(skills=["PLC programming", "Forklift"], title="Maintenance Technician",
                      years=3),
            job("Maintenance Technician II", requirements=["PLC programming",
                                                           "2+ years of experience"]),
            {MatchVerdict.STRONG},
        ),
        # Supply chain
        (
            candidate(skills=["Procurement", "Supply chain management"],
                      title="Supply Chain Planner", years=4),
            job("Supply Chain Planner", requirements=["Supply chain experience",
                                                      "Inventory management"]),
            {MatchVerdict.PARTIAL, MatchVerdict.GOOD},
        ),
    ],
)
def test_domain_neutral_professions(cand, content, verdicts) -> None:  # type: ignore[no-untyped-def]
    assert run(cand, content).verdict in verdicts


def test_deterministic() -> None:
    first = run(strong_candidate(), strong_job())
    second = run(strong_candidate(), strong_job())
    assert first == second


def test_scores_are_integers_and_overall_is_checkable() -> None:
    outcome = run(strong_candidate(), strong_job(salary=None))
    assert isinstance(outcome.job_score, int) and isinstance(outcome.preference_score, int)
    assert outcome.overall_score == round_score(
        0.7 * outcome.job_score + 0.3 * outcome.preference_score
    )


def test_aggregate_excludes_unknown_components() -> None:
    from app.schemas.job_match import ComponentResult

    def c(key: str, score: float | None) -> ComponentResult:
        return ComponentResult(
            key=key, group="job", status="unknown" if score is None else "match", score=score,
            weight=model.JOB_WEIGHTS[key], reason_code="x",
        )

    score, coverage = aggregate([c("skills", 1.0), c("experience", 0.5), c("title", None)],
                                model.JOB_WEIGHTS)
    assert score == 80  # (30*1 + 20*0.5) / 50
    assert coverage == round(50 / 90, 3)
    assert aggregate([c("title", None)], model.JOB_WEIGHTS) == (None, 0.0)


@pytest.mark.parametrize(
    ("score", "verdict"),
    [(100, MatchVerdict.STRONG), (80, MatchVerdict.STRONG), (79, MatchVerdict.GOOD),
     (70, MatchVerdict.GOOD), (69, MatchVerdict.PARTIAL), (60, MatchVerdict.PARTIAL),
     (59, MatchVerdict.WEAK), (0, MatchVerdict.WEAK)],
)
def test_recommendation_thresholds(score: int, verdict: MatchVerdict) -> None:
    assert recommendation(score, 1.0, score, False) is verdict
    assert recommendation(score, 1.0, score, True) is MatchVerdict.BLOCKED


@pytest.mark.parametrize(
    ("score", "band"),
    [(95, "excellent"), (90, "excellent"), (85, "strong"), (72, "good"), (60, "potential"),
     (12, "weak")],
)
def test_bands(score: int, band: str) -> None:
    assert model.band(score)[0] == band
    assert model.band(None) is None


def test_overall_without_preferences_is_the_job_match() -> None:
    assert overall(80, None) == 80
    assert overall(None, 90) is None
    assert overall(87, 72) == 83  # 60.9 + 21.6 = 82.5 -> 83
