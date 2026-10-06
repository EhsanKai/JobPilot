"""V0.6 matching API: calculate / read / stale / batch / explain, list
integration, and user isolation (a match is always the user's own)."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.provider import AINotConfiguredError
from app.models import (
    Job,
    JobIntelligence,
    JobMatch,
    JobMatchExplanation,
    JobPreferences,
    User,
    UserJob,
)
from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel
from app.services.matching import model
from tests.resume_helpers import FakeAIProvider

API = "/api/v1"

DESCRIPTION = """Requirements
• 3+ years of experience in backend development
• Python and SQL
• Fluent English

Nice to have
• Kubernetes"""


@pytest.fixture
def auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {register_and_login()['access_token']}"}


@pytest.fixture
def other_auth(register_and_login) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = register_and_login(email="mallory@example.com")["access_token"]
    return {"Authorization": f"Bearer {token}"}


def user(db: Session, email: str = "jane@example.com") -> User:
    found = db.scalar(select(User).where(User.email == email))
    assert found is not None
    return found


def make_job(db: Session, owner: User | None = None, **overrides: Any) -> Job:
    values: dict[str, Any] = {
        "canonical_url": f"https://jobs.example.com/{uuid.uuid4()}",
        "title": "Senior Software Engineer",
        "company_name": "Example GmbH",
        "location": "Köln",
        "locations": ["Köln"],
        "description": DESCRIPTION,
        "requirements": [],
        "responsibilities": [],
        "technologies": [],
        "work_model": RemotePreference.HYBRID,
        "employment_type": EmploymentTypeValue.FULL_TIME,
        "seniority": SeniorityLevel.SENIOR,
        "salary_min": Decimal(75000),
        "salary_max": Decimal(85000),
        "salary_currency": "EUR",
        "salary_period": SalaryPeriod.YEAR,
    }
    values.update(overrides)
    job = Job(**values)
    db.add(job)
    db.flush()
    if owner is not None:
        db.add(UserJob(user_id=owner.id, job_id=job.id))
    db.commit()
    return job


def set_up_profile(client: TestClient, auth: dict[str, str], **prefs: Any) -> None:
    response = client.put(
        f"{API}/profile",
        json={"current_job_title": "Software Engineer", "years_of_experience": 6,
              "seniority_level": "senior", "location": "Köln"},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    for name in ("Python", "SQL"):
        assert client.post(f"{API}/profile/skills", json={"name": name},
                           headers=auth).status_code == 201
    assert client.post(f"{API}/profile/languages", json={"name": "English",
                       "proficiency": "fluent"}, headers=auth).status_code == 201
    body = {
        "preferred_locations": [{"location": "Cologne"}],
        "work_models": ["hybrid"],
        "employment_types": ["full_time"],
        "minimum_salary": 70000,
        "salary_currency": "EUR",
        "desired_job_titles": [{"title": "Software Engineer", "priority": "primary"}],
    }
    body.update(prefs)
    assert client.put(f"{API}/preferences", json=body, headers=auth).status_code == 200


def calculate(client: TestClient, auth: dict[str, str], job: Job) -> dict[str, Any]:
    response = client.post(f"{API}/jobs/{job.id}/match", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


# --- States -------------------------------------------------------------------------


def test_not_calculated_until_requested(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    job = make_job(db_session, user(db_session))
    response = client.get(f"{API}/jobs/{job.id}/match", headers=auth)
    assert response.status_code == 200
    assert response.json() == {"job_id": str(job.id), "status": "not_calculated", "result": None}
    # Reading never calculates.
    assert db_session.scalar(select(func.count()).select_from(JobMatch)) == 0
    listed = client.get(f"{API}/jobs", headers=auth).json()["items"][0]
    assert listed["match"] is None


def test_calculate_returns_an_explainable_result(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    state = calculate(client, auth, job)
    assert state["status"] == "calculated"
    result = state["result"]
    assert result["recommendation"] == "STRONG_MATCH"
    assert result["band"]["key"] in ("excellent", "strong")
    assert result["job_match"] >= 85 and result["preference_match"] >= 85
    assert result["overall_match"] == result["overall_match_uncapped"]
    assert {i["id"] for i in result["strong_matches"]} >= {"skills:python", "skills:sql",
                                                          "experience", "location"}
    assert [g["id"] for g in result["gaps"]] == ["skills:kubernetes"]
    assert result["deal_breakers"] == []
    assert result["versions"]["matching_algorithm_version"] == "matching_v1"
    assert result["versions"]["model_hash"] == model.MODEL_HASH
    assert result["versions"]["taxonomy_version"] == "taxonomy_v1"
    assert result["versions"]["job_extractor_version"] == "job_extractor_v1"
    assert result["user_job_id"]
    components = {c["key"]: c for c in result["components"]}
    assert components["skills"]["items"][0]["job"]["text"]  # verbatim evidence
    assert result["ai_explanation"] is None


def test_results_are_stored_once_per_input_set(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    first = calculate(client, auth, job)["result"]
    second = calculate(client, auth, job)["result"]
    assert first == second
    assert db_session.scalar(select(func.count()).select_from(JobMatch)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobIntelligence)) == 1


def test_profile_change_makes_the_result_stale(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    calculate(client, auth, job)
    client.post(f"{API}/profile/skills", json={"name": "Kubernetes"}, headers=auth)

    state = client.get(f"{API}/jobs/{job.id}/match", headers=auth).json()
    assert state["status"] == "stale"
    assert state["result"]["stale_reasons"] == ["profile_changed"]
    listed = client.get(f"{API}/jobs", headers=auth).json()["items"][0]["match"]
    assert listed["status"] == "stale"

    recalculated = calculate(client, auth, job)
    assert recalculated["status"] == "calculated"
    assert recalculated["result"]["gaps"] == []
    # History is kept; exactly one result is current.
    matches = db_session.scalars(select(JobMatch)).all()
    assert len(matches) == 2 and sum(m.is_current for m in matches) == 1


def test_job_change_makes_the_result_stale(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    calculate(client, auth, job)
    job.description = DESCRIPTION + "\n\nMust have\n• Kubernetes"
    db_session.commit()
    state = client.get(f"{API}/jobs/{job.id}/match", headers=auth).json()
    assert state["result"]["stale_reasons"] == ["job_changed"]
    result = calculate(client, auth, job)["result"]
    assert result["overall_match"] <= model.REQUIRED_MISSING_CAP
    assert any(c["key"] == "required_missing.skills" for c in result["caps"])


def test_algorithm_change_makes_the_result_stale(
    client: TestClient, auth: dict[str, str], db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    calculate(client, auth, job)
    monkeypatch.setattr(model, "MODEL_HASH", "0" * 64)
    state = client.get(f"{API}/jobs/{job.id}/match", headers=auth).json()
    assert state["result"]["stale_reasons"] == ["algorithm_changed"]


def test_deal_breaker_result(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    set_up_profile(client, auth, relocation="no")
    job = make_job(db_session, user(db_session), location="München", locations=["München"],
                   work_model=RemotePreference.ONSITE)
    result = calculate(client, auth, job)["result"]
    assert result["recommendation"] == "NOT_RECOMMENDED"
    assert result["is_blocked"] is True
    assert result["job_match"] >= 85
    assert result["overall_match"] == model.HARD_BLOCK_CAP
    assert result["overall_match_uncapped"] > result["overall_match"]
    assert [d["id"] for d in result["deal_breakers"]] == ["deal_breaker:relocation_preference"]


# --- List integration -----------------------------------------------------------------


def test_job_list_shows_match_and_sorts_by_it(
    client: TestClient, auth: dict[str, str], db_session: Session
) -> None:
    set_up_profile(client, auth)
    owner = user(db_session)
    good = make_job(db_session, owner)
    weak = make_job(db_session, owner, title="Accountant", description="Requirements\n• IFRS")
    unmatched = make_job(db_session, owner, title="Planner")
    calculate(client, auth, weak)
    calculate(client, auth, good)

    items = client.get(f"{API}/jobs", params={"sort": "match"}, headers=auth).json()["items"]
    assert [i["id"] for i in items] == [str(good.id), str(weak.id), str(unmatched.id)]
    assert items[0]["match"]["recommendation"] == "STRONG_MATCH"
    assert items[0]["match"]["overall_match"] > items[1]["match"]["overall_match"]
    assert items[2]["match"] is None
    detail = client.get(f"{API}/jobs/{good.id}", headers=auth).json()
    assert detail["match"]["status"] == "calculated"


# --- Batch ----------------------------------------------------------------------------


def test_batch_matching(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeAIProvider
) -> None:
    set_up_profile(client, auth)
    owner = user(db_session)
    jobs = [make_job(db_session, owner) for _ in range(3)]
    foreign = make_job(db_session)  # not in the user's list

    response = client.post(
        f"{API}/jobs/match", json={"job_ids": [str(j.id) for j in jobs] + [str(foreign.id)]},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["calculated"] == 3 and body["unchanged"] == 0
    assert body["not_found"] == [str(foreign.id)]
    # Deterministic only: no AI call, however many jobs.
    assert fake_ai.calls == []

    again = client.post(f"{API}/jobs/match", json={}, headers=auth).json()
    assert again["calculated"] == 0  # everything is up to date
    forced = client.post(f"{API}/jobs/match", json={"force": True, "limit": 2},
                         headers=auth).json()
    assert len(forced["results"]) == 2


def test_batch_is_bounded(client: TestClient, auth: dict[str, str]) -> None:
    too_many = [str(uuid.uuid4()) for _ in range(26)]
    assert client.post(f"{API}/jobs/match", json={"job_ids": too_many},
                       headers=auth).status_code == 422
    assert client.post(f"{API}/jobs/match", json={"limit": 100},
                       headers=auth).status_code == 422


# --- Security -----------------------------------------------------------------------------


def test_users_cannot_read_or_create_each_others_matches(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    calculate(client, auth, job)

    # Mallory doesn't have the job in her list: everything is a 404.
    for method, path in (
        ("get", f"/jobs/{job.id}/match"),
        ("post", f"/jobs/{job.id}/match"),
        ("post", f"/jobs/{job.id}/match/explanation"),
    ):
        response = getattr(client, method)(f"{API}{path}", headers=other_auth)
        assert response.status_code == 404, (method, path)
    batch = client.post(f"{API}/jobs/match", json={"job_ids": [str(job.id)]},
                        headers=other_auth).json()
    assert batch["not_found"] == [str(job.id)] and batch["results"] == []


def test_same_job_different_users_different_scores(
    client: TestClient, auth: dict[str, str], other_auth: dict[str, str], db_session: Session
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    db_session.add(UserJob(user_id=user(db_session, "mallory@example.com").id, job_id=job.id))
    db_session.commit()

    jane = calculate(client, auth, job)["result"]
    mallory = calculate(client, other_auth, job)["result"]
    assert jane["user_job_id"] != mallory["user_job_id"]
    assert jane["recommendation"] == "STRONG_MATCH"
    assert mallory["recommendation"] == "INSUFFICIENT_DATA"
    # Each user only ever sees their own result.
    assert client.get(f"{API}/jobs/{job.id}/match", headers=auth).json()["result"] == jane


def test_match_endpoints_require_authentication(client: TestClient) -> None:
    job_id = uuid.uuid4()
    assert client.get(f"{API}/jobs/{job_id}/match").status_code == 401
    assert client.post(f"{API}/jobs/{job_id}/match").status_code == 401
    assert client.post(f"{API}/jobs/match", json={}).status_code == 401
    assert client.post(f"{API}/jobs/{job_id}/match/explanation").status_code == 401


# --- AI explanation over HTTP ---------------------------------------------------------------


def explanation_answer() -> dict[str, Any]:
    return {
        "summary": "Python and SQL are in your profile and your experience meets the "
                   "requirement. Kubernetes is not in your profile yet.",
        "strengths": [{"fact_id": "skills:python", "text": "Python is in your profile."}],
        "concerns": [{"fact_id": "skills:kubernetes", "text": "Kubernetes is a nice-to-have."}],
    }


def test_ai_explanation_is_generated_once_and_versioned(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeAIProvider
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    before = calculate(client, auth, job)["result"]
    fake_ai.response = explanation_answer()

    response = client.post(f"{API}/jobs/{job.id}/match/explanation", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prompt_version"] == "matching_explanation_v1"
    assert (body["ai_provider"], body["ai_model"]) == ("fake", "fake-model-1")
    # Cached: a second request makes no AI call.
    client.post(f"{API}/jobs/{job.id}/match/explanation", headers=auth)
    assert len(fake_ai.calls) == 1
    after = client.get(f"{API}/jobs/{job.id}/match", headers=auth).json()["result"]
    assert after["ai_explanation"]["summary"] == body["summary"]
    # The AI never changes the deterministic result.
    for key in ("overall_match", "job_match", "preference_match", "recommendation", "gaps"):
        assert after[key] == before[key]


def test_rejected_ai_explanation_is_not_stored(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeAIProvider
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    calculate(client, auth, job)
    answer = explanation_answer()
    answer["summary"] = "You are a 99% match with deep Terraform experience."
    fake_ai.response = answer
    response = client.post(f"{API}/jobs/{job.id}/match/explanation", headers=auth)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AI_EXPLANATION_REJECTED"
    assert db_session.scalar(select(func.count()).select_from(JobMatchExplanation)) == 0


def test_ai_not_configured_is_reported_safely(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeAIProvider
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    calculate(client, auth, job)
    fake_ai.response = AINotConfiguredError("disabled")
    response = client.post(f"{API}/jobs/{job.id}/match/explanation", headers=auth)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_NOT_CONFIGURED"
    assert "Resume" not in response.json()["error"]["message"]


def test_explanation_needs_an_up_to_date_match(
    client: TestClient, auth: dict[str, str], db_session: Session, fake_ai: FakeAIProvider
) -> None:
    set_up_profile(client, auth)
    job = make_job(db_session, user(db_session))
    assert client.post(f"{API}/jobs/{job.id}/match/explanation",
                       headers=auth).status_code == 409
    calculate(client, auth, job)
    prefs = db_session.scalar(select(JobPreferences))
    assert prefs is not None
    prefs.minimum_salary = Decimal(90000)
    db_session.commit()
    assert client.post(f"{API}/jobs/{job.id}/match/explanation",
                       headers=auth).status_code == 409
    assert fake_ai.calls == []
