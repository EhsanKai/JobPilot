"""V0.4 deduplication decision model: pure, deterministic, no I/O.

Postings come from the real adapters' normalized output shape
(`NormalizedJob.content_fields()`), so these tests exercise exactly what the
resolver sees in production.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from app.services.job_discovery.deduplication.resolver import (
    HIGH_CONFIDENCE_SIMILARITY,
    MIN_BODY_WORDS,
    POSSIBLE_MATCH_SIMILARITY,
    CandidateJob,
    MatchDecision,
    PostingFacts,
    compare,
    resolve,
)
from app.services.job_discovery.deduplication.similarity import body_similarity, jaccard, shingles
from app.services.job_discovery.normalizer import JobNormalizer
from app.services.job_discovery.raw_job import RawJob, RawSalary
from tests.job_helpers import (
    OTHER_INTRO,
    OTHER_REQUIREMENTS,
    OTHER_RESPONSIBILITIES,
    SAP_INTRO,
    SAP_REQUIREMENTS,
    SAP_RESPONSIBILITIES,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
SAP_DESCRIPTION = "\n\n".join(
    [SAP_INTRO, "What you'll do", *SAP_RESPONSIBILITIES, "What you bring", *SAP_REQUIREMENTS]
)


def raw(source: str = "greenhouse", **overrides: Any) -> RawJob:
    base = RawJob(
        source=source,
        source_job_id=f"{source}-1",
        title="Senior SAP ABAP Developer",
        company_name="Example GmbH",
        url=f"https://{source}.example.com/jobs/1",
        location="Cologne, Germany",
        description=SAP_DESCRIPTION,
        requirements=SAP_REQUIREMENTS,
        responsibilities=SAP_RESPONSIBILITIES,
        employment_type="Full-time",
    )
    return replace(base, **overrides)


def facts(source: str = "greenhouse", **overrides: Any) -> PostingFacts:
    job = JobNormalizer().normalize(raw(source, **overrides), now=NOW)
    return PostingFacts.from_content(
        source=job.source, url=job.canonical_url, content=job.content_fields()
    )


GREENHOUSE = facts("greenhouse")
LEVER = facts("lever", location="Köln", title="Senior SAP ABAP-Developer (m/w/d)")


# --- compare() -----------------------------------------------------------------------


def test_same_job_on_two_sources_is_high_confidence() -> None:
    result = compare(LEVER, GREENHOUSE)
    assert result.decision is MatchDecision.HIGH_CONFIDENCE
    assert result.similarity is not None and result.similarity >= HIGH_CONFIDENCE_SIMILARITY


def test_same_canonical_url_is_exact() -> None:
    url = "https://careers.example.com/jobs/sap-abap?utm_source=greenhouse"
    a = facts("greenhouse", url=url)
    b = facts("aggregator", url="http://careers.example.com/jobs/sap-abap/", location=None)
    assert compare(b, a).decision is MatchDecision.EXACT


def test_same_url_with_a_different_title_is_not_exact() -> None:
    # A source that links every posting to one generic careers page must not
    # merge different jobs.
    url = "https://careers.example.com/"
    a = facts("greenhouse", url=url)
    b = facts("aggregator", url=url, title="Finance Specialist", description=OTHER_INTRO)
    assert compare(b, a).decision is MatchDecision.NO_MATCH


def test_different_location_is_never_a_match() -> None:
    for location in ("Munich", "Bonn", "Remote", "Remote - Germany", "Berlin, Germany"):
        assert compare(facts("lever", location=location), GREENHOUSE).decision is (
            MatchDecision.NO_MATCH
        ), location


def test_same_title_different_company_is_never_a_match() -> None:
    other = facts("lever", company_name="Other GmbH")
    assert compare(other, GREENHOUSE).decision is MatchDecision.NO_MATCH


def test_same_company_different_title_is_never_a_match() -> None:
    for title in ("SAP ABAP Developer", "Junior SAP ABAP Developer", "Senior SAP Developer"):
        assert compare(facts("lever", title=title), GREENHOUSE).decision is (
            MatchDecision.NO_MATCH
        ), title


def test_same_company_title_location_but_different_description_does_not_merge() -> None:
    other = facts(
        "lever",
        description=OTHER_INTRO,
        requirements=OTHER_REQUIREMENTS,
        responsibilities=OTHER_RESPONSIBILITIES,
    )
    result = compare(other, GREENHOUSE)
    assert result.decision is MatchDecision.NO_MATCH
    assert result.similarity is not None and result.similarity < POSSIBLE_MATCH_SIMILARITY


def test_partly_similar_description_is_only_a_possible_match() -> None:
    # The same text plus a whole different role description: related, not proven.
    half = facts(
        "lever",
        description="\n\n".join(
            [SAP_INTRO, *SAP_RESPONSIBILITIES, *SAP_REQUIREMENTS, OTHER_INTRO]
        ),
        requirements=(),
        responsibilities=(),
    )
    result = compare(half, GREENHOUSE)
    assert result.similarity is not None
    assert POSSIBLE_MATCH_SIMILARITY <= result.similarity < HIGH_CONFIDENCE_SIMILARITY
    assert result.decision is MatchDecision.POSSIBLE_MATCH


def test_too_little_text_to_confirm_is_only_a_possible_match() -> None:
    a = facts("greenhouse", description="Join our SAP team.", requirements=(), responsibilities=())
    b = facts("lever", description="Join our SAP team.", requirements=(), responsibilities=())
    result = compare(b, a)
    assert result.decision is MatchDecision.POSSIBLE_MATCH
    assert result.reason == "description_too_short"


def test_missing_location_is_only_a_possible_match() -> None:
    assert compare(facts("lever", location=None), GREENHOUSE).decision is (
        MatchDecision.POSSIBLE_MATCH
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"employment_type": "Working student"},
        {"work_model": "remote"},  # GREENHOUSE states none -> no conflict; see below
        {"salary": RawSalary(30000, 40000, "EUR", "year")},
    ],
)
def test_conflicting_structured_facts_block_a_merge(overrides: dict[str, Any]) -> None:
    base = facts(
        "greenhouse", work_model="onsite", salary=RawSalary(70000, 90000, "EUR", "year")
    )
    result = compare(facts("lever", **overrides), base)
    assert result.decision is MatchDecision.POSSIBLE_MATCH
    assert result.reason.startswith("conflict:")


def test_compatible_structured_facts_still_merge() -> None:
    base = facts("greenhouse", salary=RawSalary(70000, 90000, "EUR", "year"))
    other = facts("lever", salary=RawSalary(75000, 95000, "EUR", "year"))  # overlapping
    assert compare(other, base).decision is MatchDecision.HIGH_CONFIDENCE


def test_same_source_is_never_merged_across_ids() -> None:
    # Two ids on one board are two vacancies, even with identical text.
    twin = facts("greenhouse", source_job_id="greenhouse-2", url="https://x.example.com/2")
    assert compare(twin, GREENHOUSE).decision is MatchDecision.NO_MATCH


def test_postings_without_company_or_title_keys_never_match() -> None:
    a = replace(GREENHOUSE, identity=replace(GREENHOUSE.identity, title_key=""))
    assert compare(LEVER, a).decision is MatchDecision.NO_MATCH


def test_thresholds_are_documented_constants() -> None:
    assert (HIGH_CONFIDENCE_SIMILARITY, POSSIBLE_MATCH_SIMILARITY, MIN_BODY_WORDS) == (
        0.80,
        0.50,
        30,
    )


def test_compare_is_deterministic() -> None:
    assert compare(LEVER, GREENHOUSE) == compare(LEVER, GREENHOUSE)


# --- resolve() ------------------------------------------------------------------------


def job(*postings: PostingFacts, sources: set[str] | None = None) -> CandidateJob:
    return CandidateJob(
        job_id=uuid.uuid4(),
        sources=frozenset(sources or {p.source for p in postings}),
        postings=postings,
    )


def test_resolve_attaches_to_the_single_matching_job() -> None:
    target = job(GREENHOUSE)
    resolution = resolve(LEVER, [job(facts("greenhouse", location="Munich")), target])
    assert resolution.decision is MatchDecision.HIGH_CONFIDENCE
    assert resolution.job_id == target.job_id


def test_resolve_never_guesses_between_two_matching_jobs() -> None:
    resolution = resolve(LEVER, [job(GREENHOUSE), job(facts("aggregator"))])
    assert resolution.decision is MatchDecision.POSSIBLE_MATCH
    assert resolution.job_id is None


def test_exact_outranks_high_confidence() -> None:
    url = "https://careers.example.com/jobs/sap"
    exact = job(facts("aggregator", url=url))
    resolution = resolve(facts("lever", url=url), [job(GREENHOUSE), exact])
    assert (resolution.decision, resolution.job_id) == (MatchDecision.EXACT, exact.job_id)


def test_a_job_already_listed_by_the_same_source_is_not_merged_into() -> None:
    # The job has a Lever posting under another id: Lever says these are
    # two vacancies.
    candidate = job(GREENHOUSE, sources={"greenhouse", "lever"})
    resolution = resolve(LEVER, [candidate])
    assert resolution.decision is MatchDecision.POSSIBLE_MATCH
    assert resolution.job_id is None


def test_no_candidates_is_no_match() -> None:
    assert resolve(LEVER, []).decision is MatchDecision.NO_MATCH


# --- similarity -------------------------------------------------------------------------


def test_jaccard_of_shingles() -> None:
    a = shingles("a b c d".split())
    b = shingles("a b c e".split())
    assert jaccard(a, b) == pytest.approx(1 / 3)
    assert jaccard(a, a) == 1.0
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_similarity_is_order_sensitive_but_ignores_repetition() -> None:
    text = " ".join(SAP_INTRO.lower().replace(".", "").replace(",", "").split())
    assert body_similarity(text, f"{text} {text}").score > 0.9
    assert body_similarity(text, " ".join(reversed(text.split()))).score < 0.05


def test_identity_keys_fit_their_columns() -> None:
    from app.services.job_discovery.deduplication.identity import (
        MAX_COMPANY_KEY,
        MAX_TITLE_KEY,
        MAX_URL_KEY,
        identity_of,
    )

    identity = identity_of(
        company_name="R&D " * 63,  # 252 chars, folds to "r and d ..." (> 255)
        title="A&B " * 125,  # 500 chars, folds to more than 500
        location="Köln",
        url="http://example.com/" + "a" * 1981,  # 2000 chars; the key would be https
    )
    assert len(identity.company_key) <= MAX_COMPANY_KEY
    assert len(identity.title_key) <= MAX_TITLE_KEY
    assert identity.url_key is None or len(identity.url_key) <= MAX_URL_KEY
