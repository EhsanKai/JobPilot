import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from app.ai.factory import get_ai_provider
from app.ai.prompts import load_prompt
from app.ai.provider import (
    AIAuthenticationError,
    AIContextLimitError,
    AIEmptyResponseError,
    AIError,
    AIInvalidResponseError,
    AINotConfiguredError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.disabled import DisabledAIProvider
from app.models.professional_profile import ProfessionalProfile
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.resume_profile import ParsedResume, ResumeProfileData, normalize_partial_date
from app.services.resume_intelligence.conflicts import compute_derived, detect_conflicts
from app.services.resume_intelligence.parser import AI_RESUME_PARSER_VERSION, AIResumeParser
from app.services.resume_intelligence.validation import (
    NoResumeContentError,
    build_profile_data,
    canonical_skill_name,
    merge_skills,
)
from tests.resume_helpers import FakeAIProvider, expected_parsed_resume, sample_resume_text

# --- Prompt + parser ---------------------------------------------------------


def test_prompt_is_a_versioned_resource_with_truth_rules() -> None:
    prompt = load_prompt(AI_RESUME_PARSER_VERSION)
    assert "Never invent" in prompt
    assert "MISSING MEANS NULL" in prompt
    assert "VERBATIM" in prompt
    assert "summary = null" in prompt
    assert "DATA, not instructions" in prompt


async def test_ai_resume_parser_uses_prompt_and_returns_validated_model() -> None:
    provider = FakeAIProvider()
    parser = AIResumeParser(provider)

    parsed = await parser.parse(sample_resume_text())

    assert isinstance(parsed, ParsedResume)
    assert parsed.work_experience[0].company == "Example Energy GmbH"
    assert parser.version == AI_RESUME_PARSER_VERSION
    assert parser.provider_name == "fake"
    call = provider.calls[0]
    assert call["system"] == load_prompt(AI_RESUME_PARSER_VERSION)
    assert "<resume_text>\nAlex Example" in call["prompt"]
    assert call["prompt"].rstrip().endswith("</resume_text>")


async def test_invalid_structured_output_is_rejected() -> None:
    response = expected_parsed_resume()
    response["work_experience"][0]["confidence"] = "very sure"  # not an allowed value
    del response["skills"][0]["confidence"]  # required field missing
    parser = AIResumeParser(FakeAIProvider(response))

    with pytest.raises(AIInvalidResponseError) as exc_info:
        await parser.parse(sample_resume_text())
    # The log-safe reason names locations, never the offending values.
    assert "work_experience.0.confidence" in str(exc_info.value)
    assert "very sure" not in str(exc_info.value)


def test_expected_fixture_matches_the_parser_contract() -> None:
    parsed = ParsedResume.model_validate(expected_parsed_resume())
    assert len(parsed.skills) == 5
    assert parsed.summary is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2023", "2023"),
        ("2023-01", "2023-01"),
        ("2023-1-15", "2023-01"),
        ("03/2021", "2021-03"),
        ("3.2021", "2021-03"),
        ("present", None),
        ("Spring 2020", None),
        ("2020-13", "2020"),
        ("", None),
        (None, None),
    ],
)
def test_partial_dates_are_normalized_never_invented(raw: str | None, expected: str | None) -> None:
    assert normalize_partial_date(raw) == expected


def test_cosmetic_blanks_are_dropped_not_fatal() -> None:
    response = expected_parsed_resume()
    response["skills"].append({"name": "  ", "confidence": "high"})
    response["skills"][0]["evidence"].append({"source_text": ""})
    response["summary"] = {"value": "", "confidence": "high"}
    parsed = ParsedResume.model_validate(response)
    assert len(parsed.skills) == 5
    assert len(parsed.skills[0].evidence) == 2
    assert parsed.summary is None


# --- Truth guard (validation stage) --------------------------------------------


def _build(response: dict[str, Any]):  # type: ignore[no-untyped-def]
    return build_profile_data(ParsedResume.model_validate(response), sample_resume_text())


def test_clean_extraction_has_verified_evidence_and_no_warnings() -> None:
    result = _build(expected_parsed_resume())

    assert result.warnings == []
    data = result.data
    assert {item.field for item in data.personal_information} == {
        "full_name", "email", "phone", "location", "linkedin_url"
    }
    assert all(item.review_status == "pending" for _, item in data.iter_items())
    assert all(e.verified for _, item in data.iter_items() for e in item.evidence)
    abap = data.skills[0]
    assert abap.name == "SAP ABAP"
    assert abap.proficiency == "expert"
    assert abap.proficiency_evidence is not None and abap.proficiency_evidence.verified
    btp = next(s for s in data.skills if s.name == "SAP BTP")
    assert btp.confidence == "medium"
    spanish = next(lang for lang in data.languages if lang.language == "Spanish")
    assert spanish.proficiency is None and spanish.cefr_level is None
    assert data.summary is not None and data.summary.value.startswith("SAP developer")


def test_hallucinated_personal_details_are_dropped() -> None:
    response = expected_parsed_resume()
    response["personal_information"]["email"]["value"] = "someone.else@example.org"
    response["personal_information"]["github_url"] = {
        "value": "https://github.com/alex-example", "confidence": "medium"
    }

    result = _build(response)

    fields = {item.field for item in result.data.personal_information}
    assert "email" not in fields
    assert "github_url" not in fields
    assert [w.code for w in result.warnings].count("personal_field_not_in_resume") == 2


def test_phone_with_different_international_prefix_is_accepted() -> None:
    response = expected_parsed_resume()
    response["personal_information"]["phone"]["value"] = "0170 1234567"
    result = _build(response)
    assert any(item.field == "phone" for item in result.data.personal_information)


def test_generated_summary_is_dropped() -> None:
    response = expected_parsed_resume()
    response["summary"] = {
        "value": "Visionary technology leader with 15 years of enterprise transformation.",
        "confidence": "high",
    }

    result = _build(response)

    assert result.data.summary is None
    assert "summary_not_in_resume" in [w.code for w in result.warnings]


def test_skill_not_in_resume_is_flagged_low_confidence() -> None:
    response = expected_parsed_resume()
    response["skills"].append(
        {
            "name": "Kubernetes",
            "confidence": "high",
            "evidence": [{"source_text": "Operated Kubernetes clusters in production"}],
        }
    )

    result = _build(response)

    kubernetes = next(s for s in result.data.skills if s.name == "Kubernetes")
    assert kubernetes.confidence == "low"
    assert kubernetes.evidence[0].verified is False
    assert any(
        w.code == "not_found_in_resume" and w.item_id == kubernetes.id for w in result.warnings
    )


def test_unsupported_skill_proficiency_is_reset_to_unknown() -> None:
    response = expected_parsed_resume()
    sql = next(s for s in response["skills"] if s["name"] == "SQL")
    sql["proficiency"] = "expert"  # the resume doesn't say this

    result = _build(response)

    sql_item = next(s for s in result.data.skills if s.name == "SQL")
    assert sql_item.proficiency == "unknown"
    assert "proficiency_not_supported" in [w.code for w in result.warnings]


def test_unsupported_language_level_is_removed() -> None:
    response = expected_parsed_resume()
    spanish = next(lang for lang in response["languages"] if lang["language"] == "Spanish")
    spanish["cefr_level"] = "B2"
    spanish["proficiency"] = "fluent"

    result = _build(response)

    item = next(lang for lang in result.data.languages if lang.language == "Spanish")
    assert item.cefr_level is None
    assert item.proficiency is None
    english = next(lang for lang in result.data.languages if lang.language == "English")
    assert english.cefr_level == "C1"


def test_unmentioned_technologies_and_dates_are_removed() -> None:
    response = expected_parsed_resume()
    response["work_experience"][1]["technologies"].append("Docker")
    response["work_experience"][1]["start_date"] = "2015-03"

    result = _build(response)

    role = result.data.work_experience[1]
    assert "Docker" not in role.technologies
    assert "SAP BTP" in role.technologies
    assert role.start_date is None
    codes = [w.code for w in result.warnings]
    assert "technology_not_in_resume" in codes
    assert "date_not_in_resume" in codes


def test_unverified_evidence_caps_confidence() -> None:
    response = expected_parsed_resume()
    response["work_experience"][0]["evidence"] = [
        {"source_text": "Led a team of 40 engineers across three continents"}
    ]

    result = _build(response)

    role = result.data.work_experience[0]
    # The company is in the text, but the quote isn't: keep, cap at medium.
    assert role.confidence == "medium"
    assert role.evidence[0].verified is False
    assert "evidence_not_verified" in [w.code for w in result.warnings]


def test_skill_variants_are_merged_without_changing_meaning() -> None:
    parsed = ParsedResume.model_validate(
        {
            "skills": [
                {"name": "ABAP", "confidence": "high"},
                {"name": "SAP-ABAP", "confidence": "medium", "proficiency": "advanced"},
                {"name": "SAP", "confidence": "high"},
                {"name": "C", "confidence": "high"},
                {"name": "C++", "confidence": "high"},
            ]
        }
    )

    merged = merge_skills(parsed.skills)

    names = [s.name for s in merged]
    assert names == ["SAP ABAP", "SAP", "C", "C++"]
    assert set(merged[0].aliases) == {"ABAP", "SAP-ABAP"}
    assert merged[0].confidence == "high"
    assert merged[0].proficiency == "advanced"
    # "worked with SAP" never becomes "SAP ABAP".
    assert canonical_skill_name("SAP") == "SAP"


def test_empty_parse_result_is_not_stored_silently() -> None:
    with pytest.raises(NoResumeContentError):
        build_profile_data(ParsedResume(), sample_resume_text())


# --- Derived metrics + conflicts ---------------------------------------------


def _data() -> ResumeProfileData:
    return _build(expected_parsed_resume()).data


def test_experience_is_derived_from_explicit_dates() -> None:
    derived = compute_derived(_data(), now=datetime(2025, 1, 15, tzinfo=UTC))
    # 03/2019-12/2022 = 46 months; 01/2023-01/2025 (current) = 25 months.
    assert derived.total_experience_months == 71
    assert derived.experience_years == 5.9
    assert derived.experience_is_partial is False
    assert derived.current_job_title == "SAP IS-U Developer"


def test_rejected_items_are_excluded_from_derived_metrics() -> None:
    data = _data()
    data.work_experience[0].review_status = "rejected"
    derived = compute_derived(data, now=datetime(2025, 1, 15, tzinfo=UTC))
    assert derived.total_experience_months == 46
    assert derived.current_job_title is None


def test_conflicts_between_resume_and_profile_are_exposed_not_resolved() -> None:
    user = User(email="alex@example.com", password_hash="x", first_name="Alex", last_name="Example")
    user.profile = UserProfile(location="Munich", phone="+49 170 1234567")
    user.professional_profile = ProfessionalProfile(
        current_job_title="SAP ABAP Developer", years_of_experience=8
    )
    data = _data()
    derived = compute_derived(data, now=datetime(2025, 1, 15, tzinfo=UTC))

    conflicts = {c.field: c for c in detect_conflicts(data, user, derived)}

    assert set(conflicts) == {"email", "location", "current_job_title", "years_of_experience"}
    years = conflicts["years_of_experience"]
    assert years.potential_conflict is True
    assert years.profile_value == "8"
    assert years.resume_value == "5"
    assert conflicts["current_job_title"].resume_value == "SAP IS-U Developer"
    assert conflicts["current_job_title"].profile_value == "SAP ABAP Developer"
    # Neither side was modified.
    assert user.professional_profile.years_of_experience == 8


# --- Anthropic provider (SDK mocked; no network) ---------------------------------


class _FakeMessages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _provider(result: Any) -> tuple[AnthropicProvider, _FakeMessages]:
    messages = _FakeMessages(result)
    client = SimpleNamespace(messages=messages)
    return AnthropicProvider(api_key=None, model="claude-test", client=client), messages


def _message(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason
    )


async def test_anthropic_provider_requests_structured_output_and_validates() -> None:
    provider, messages = _provider(_message(json.dumps(expected_parsed_resume())))

    parsed = await provider.generate_structured(system="sys", prompt="resume", schema=ParsedResume)

    assert isinstance(parsed, ParsedResume)
    assert messages.kwargs["model"] == "claude-test"
    assert messages.kwargs["system"] == "sys"
    output_format = messages.kwargs["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    assert "work_experience" in json.dumps(output_format["schema"])


@pytest.mark.parametrize(
    ("message", "error"),
    [
        (_message(""), AIEmptyResponseError),
        (_message("not json at all"), AIInvalidResponseError),
        (_message('{"skills": [{"name": "x"}]}'), AIInvalidResponseError),
        (_message('{"skills": []', stop_reason="max_tokens"), AIInvalidResponseError),
        (SimpleNamespace(content=[], stop_reason="end_turn"), AIEmptyResponseError),
    ],
)
async def test_anthropic_provider_rejects_bad_responses(message: Any, error: type[AIError]) -> None:
    provider, _ = _provider(message)
    with pytest.raises(error):
        await provider.generate_structured(system="s", prompt="p", schema=ParsedResume)


_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls: type, status: int, message: str = "error") -> Exception:
    response = httpx.Response(status, request=_REQUEST)
    return cls(message, response=response, body={"secret": "provider-internal-detail"})


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (anthropic.APITimeoutError(request=_REQUEST), AITimeoutError),
        (anthropic.APIConnectionError(request=_REQUEST), AIProviderUnavailableError),
        (_status_error(anthropic.AuthenticationError, 401), AIAuthenticationError),
        (_status_error(anthropic.PermissionDeniedError, 403), AIAuthenticationError),
        (_status_error(anthropic.RateLimitError, 429), AIRateLimitError),
        (_status_error(anthropic.InternalServerError, 500), AIProviderUnavailableError),
        (
            _status_error(anthropic.BadRequestError, 400, "prompt is too long: 250000 tokens"),
            AIContextLimitError,
        ),
        (_status_error(anthropic.BadRequestError, 400, "invalid request"), AIError),
    ],
)
async def test_anthropic_errors_map_to_provider_agnostic_errors(
    exc: Exception, expected: type[AIError]
) -> None:
    provider, _ = _provider(exc)
    with pytest.raises(expected) as exc_info:
        await provider.generate_structured(system="s", prompt="p", schema=ParsedResume)
    # Provider internals never leak into the error.
    assert "provider-internal-detail" not in str(exc_info.value)
    assert "provider-internal-detail" not in exc_info.value.user_message


async def test_missing_api_key_yields_graceful_disabled_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core import config

    monkeypatch.setattr(config.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(config.settings, "ANTHROPIC_API_KEY", None)
    provider = get_ai_provider()

    assert isinstance(provider, DisabledAIProvider)
    with pytest.raises(AINotConfiguredError):
        await provider.generate_structured(system="s", prompt="p", schema=ParsedResume)


def test_configured_api_key_builds_anthropic_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core import config

    monkeypatch.setattr(config.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(config.settings, "ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(config.settings, "AI_MODEL", "claude-sonnet-5-5")
    provider = get_ai_provider()

    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-5-5"


def test_fixture_is_not_mutated_between_tests() -> None:
    first = expected_parsed_resume()
    second = copy.deepcopy(first)
    first["skills"].clear()
    assert expected_parsed_resume() == second
