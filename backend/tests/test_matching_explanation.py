"""V0.6 explainability: the deterministic summary and the validated AI layer.

The AI is always a fake here -- no real AI API is ever called in tests.
"""

from typing import Any

import pytest

from app.ai.provider import AIInvalidResponseError, AINotConfiguredError
from app.models.job_match import MatchVerdict
from app.schemas.job_match import AIMatchExplanation
from app.services.matching.explanation import (
    MATCH_EXPLANATION_PROMPT_VERSION,
    ExplanationRejectedError,
    ai_facts,
    build_user_prompt,
    generate_ai_explanation,
    summarize,
    validate_ai_explanation,
)
from tests.match_helpers import candidate, job, run
from tests.resume_helpers import FakeAIProvider


def outcome_and_summary():  # type: ignore[no-untyped-def]
    cand = candidate(
        skills=["Python", "AWS"], years=6, seniority="senior", languages={"en": "C1"},
        minimum_salary=70000, currency="EUR", preferred_locations=["Cologne"],
    )
    content = job(
        "Senior Data Engineer",
        description=(
            "Requirements\n• 5+ years of experience\n• Python and AWS\n\n"
            "Nice to have\n• Kubernetes"
        ),
        seniority="senior",
        locations=["Köln"],
    )
    outcome = run(cand, content)
    return outcome, summarize(outcome.evidence)


def facts() -> dict[str, Any]:
    outcome, summary = outcome_and_summary()
    return ai_facts(
        job_title="Senior Data Engineer",
        company_name="Example GmbH",
        job_match=outcome.job_score,
        preference_match=outcome.preference_score,
        overall_match=outcome.overall_score,
        verdict=outcome.verdict,
        summary=summary,
    )


# --- Deterministic summary ------------------------------------------------------------


def test_summary_lists_strong_matches_gaps_and_unknowns() -> None:
    _, summary = outcome_and_summary()
    strong = {i.id: i.text for i in summary.strong_matches}
    gaps = {i.id: i for i in summary.gaps}
    unknowns = {i.id: i.text for i in summary.unknowns}
    assert strong["skills:python"] == "Python"
    assert strong["skills:aws"] == "Amazon Web Services"
    assert strong["experience"] == "6 years of experience (5+ asked)"
    assert gaps["skills:kubernetes"].importance == "preferred"
    assert gaps["skills:kubernetes"].text == "Kubernetes (nice to have)"
    assert unknowns["salary"] == "Salary not stated"
    assert summary.deal_breakers == []


def test_summary_items_carry_structured_state() -> None:
    _, summary = outcome_and_summary()
    for item in summary.all_items():
        assert item.kind in ("strong_match", "gap", "unknown", "deal_breaker")
        assert item.status and item.component and item.group in ("job", "preference")


def test_deal_breakers_are_listed_first_class() -> None:
    cand = candidate(preferred_locations=["Cologne"], relocation="no")
    outcome = run(cand, job(locations=["München"], work_model="onsite"))
    summary = summarize(outcome.evidence)
    assert [i.id for i in summary.deal_breakers] == ["deal_breaker:relocation_preference"]


def test_preference_unknowns_without_a_preference_are_silent() -> None:
    summary = summarize(run(candidate(skills=["Python"]), job(requirements=["Python"])).evidence)
    assert "salary" not in {i.id for i in summary.unknowns}


# --- AI explanation ----------------------------------------------------------------------


def valid_answer(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": (
            f"You match well: Python and Amazon Web Services are in your profile and your "
            f"experience meets the requirement. Kubernetes is not in your profile yet, and the "
            f"salary is not stated. Overall match: {f['scores']['overall_match']}%."
        ),
        "strengths": [{"fact_id": "skills:python", "text": "Python is in your profile."}],
        "concerns": [{"fact_id": "skills:kubernetes", "text": "Kubernetes would be a plus."}],
    }


async def test_valid_ai_explanation_is_accepted() -> None:
    f = facts()
    provider = FakeAIProvider(response=valid_answer(f))
    result = await generate_ai_explanation(provider, f)
    assert result.strengths[0].fact_id == "skills:python"
    # The AI received the versioned prompt and only the structured facts.
    call = provider.calls[0]
    assert call["system"].startswith("You explain job match results")
    assert "<match_facts>" in call["prompt"]
    assert MATCH_EXPLANATION_PROMPT_VERSION == "matching_explanation_v1"


def test_ai_receives_only_existing_evidence() -> None:
    f = facts()
    prompt = build_user_prompt(f)
    assert set(f) == {"job", "scores", "recommendation", "strong_matches", "gaps", "unknowns",
                      "deal_breakers"}
    # No raw profile or resume data, no contact data: only explanation items.
    assert '"origin"' not in prompt
    assert "resume_profile:" not in prompt
    assert "evidence_ref" not in prompt


def test_ai_output_has_no_way_to_change_the_score() -> None:
    assert set(AIMatchExplanation.model_fields) == {"summary", "strengths", "concerns"}
    with pytest.raises(ValueError):
        AIMatchExplanation.model_validate(
            {"summary": "s", "strengths": [], "concerns": [], "overall_match": 99}
        )


def test_ai_cannot_change_the_numeric_score() -> None:
    f = facts()
    answer = valid_answer(f)
    answer["summary"] = "This is a 97% match for you."
    with pytest.raises(ExplanationRejectedError, match="numbers"):
        validate_ai_explanation(AIMatchExplanation.model_validate(answer), f)


def test_ai_cannot_introduce_unsupported_skills() -> None:
    f = facts()
    answer = valid_answer(f)
    answer["summary"] = "Your Terraform expertise makes you a great fit."
    with pytest.raises(ExplanationRejectedError, match="terms"):
        validate_ai_explanation(AIMatchExplanation.model_validate(answer), f)


def test_ai_cannot_claim_a_missing_skill() -> None:
    f = facts()
    answer = valid_answer(f)
    answer["strengths"] = [{"fact_id": "skills:python", "text": "Python and Kubernetes."}]
    with pytest.raises(ExplanationRejectedError, match="lacks"):
        validate_ai_explanation(AIMatchExplanation.model_validate(answer), f)
    answer = valid_answer(f)
    answer["summary"] = "You bring strong Kubernetes skills."
    with pytest.raises(ExplanationRejectedError, match="gap"):
        validate_ai_explanation(AIMatchExplanation.model_validate(answer), f)


def test_ai_cannot_claim_missing_experience() -> None:
    f = facts()
    answer = valid_answer(f)
    answer["summary"] = "With your 12 years of experience you exceed the requirement."
    with pytest.raises(ExplanationRejectedError, match="numbers"):
        validate_ai_explanation(AIMatchExplanation.model_validate(answer), f)


def test_ai_cannot_reference_unknown_facts_or_swap_gaps_for_strengths() -> None:
    f = facts()
    answer = valid_answer(f)
    answer["strengths"] = [{"fact_id": "skills:kubernetes", "text": "Good."}]
    with pytest.raises(ExplanationRejectedError, match="strength references"):
        validate_ai_explanation(AIMatchExplanation.model_validate(answer), f)
    answer = valid_answer(f)
    answer["concerns"] = [{"fact_id": "skills:rust", "text": "Missing."}]
    with pytest.raises(ExplanationRejectedError, match="concern references"):
        validate_ai_explanation(AIMatchExplanation.model_validate(answer), f)


def test_ai_cannot_contradict_the_recommendation() -> None:
    f = facts()
    f["recommendation"] = "WEAK_MATCH"
    answer = valid_answer(f)
    answer["summary"] = "This is a strong match."
    with pytest.raises(ExplanationRejectedError, match="claims STRONG_MATCH"):
        validate_ai_explanation(AIMatchExplanation.model_validate(answer), f)
    answer["summary"] = "This is not a strong match."
    validate_ai_explanation(AIMatchExplanation.model_validate(answer), f)


async def test_malformed_ai_output_is_rejected_safely() -> None:
    provider = FakeAIProvider(response={"summary": 42, "strengths": "lots"})
    with pytest.raises(AIInvalidResponseError):
        await generate_ai_explanation(provider, facts())


async def test_provider_errors_propagate_as_ai_errors() -> None:
    provider = FakeAIProvider(response=AINotConfiguredError("disabled"))
    with pytest.raises(AINotConfiguredError):
        await generate_ai_explanation(provider, facts())


def test_recommendation_codes() -> None:
    assert facts()["recommendation"] in ("STRONG_MATCH", "GOOD_MATCH", "POTENTIAL_MATCH")
    f = facts()
    assert f["scores"]["overall_match"] is not None
    assert MatchVerdict.BLOCKED.value == "blocked"
