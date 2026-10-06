"""The deduplication decision model.

`compare(incoming, candidate)` decides, for two postings from *different*
sources, whether they are the same job. Rules, in order (first that applies
wins):

    same source                         NO_MATCH  (within a source, the source's
                                                   own id is the identity: two
                                                   ids = two vacancies)
    no company or title key             NO_MATCH
    company_key or title_key differ     NO_MATCH  ("same title" alone, or same
                                                   company + title, never merges)
    same URL identity key               EXACT
    a location missing on either side   POSSIBLE_MATCH
    location keys differ                NO_MATCH  (Cologne != Munich != Remote)
    either body < 30 words              POSSIBLE_MATCH  (too little text to confirm)
    similarity >= 0.80, no conflicts    HIGH_CONFIDENCE
    similarity >= 0.80, conflicts       POSSIBLE_MATCH
    similarity >= 0.50                  POSSIBLE_MATCH
    otherwise                           NO_MATCH  (same title, different role)

"Conflicts" are structured facts both postings state and that disagree:
employment type, work model, salary currency/period, or salary ranges that
don't overlap. Similarity is word 3-gram Jaccard (see similarity.py).

`resolve()` lifts this to canonical jobs: a job's decision is the best
decision over its postings, jobs that already have a posting from the
incoming source can't receive another one (at most POSSIBLE_MATCH), and the
incoming posting is merged only when exactly one job qualifies for EXACT or
HIGH_CONFIDENCE (EXACT outranks HIGH_CONFIDENCE). Ambiguity never merges.
"""

import enum
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.job_source_posting import JobSourcePosting
from app.services.job_discovery.deduplication.identity import PostingIdentity, identity_of
from app.services.job_discovery.deduplication.similarity import body_similarity
from app.services.job_discovery.deduplication.snapshot import content_of_job, decode_content
from app.services.job_discovery.normalization.content import normalized_body


class MatchDecision(str, enum.Enum):
    EXACT = "exact"
    HIGH_CONFIDENCE = "high_confidence"
    POSSIBLE_MATCH = "possible_match"
    NO_MATCH = "no_match"


#: Only these merge automatically.
AUTO_MERGE_DECISIONS = frozenset({MatchDecision.EXACT, MatchDecision.HIGH_CONFIDENCE})

HIGH_CONFIDENCE_SIMILARITY = 0.80
POSSIBLE_MATCH_SIMILARITY = 0.50
MIN_BODY_WORDS = 30

_RANK = {
    MatchDecision.NO_MATCH: 0,
    MatchDecision.POSSIBLE_MATCH: 1,
    MatchDecision.HIGH_CONFIDENCE: 2,
    MatchDecision.EXACT: 3,
}


@dataclass(frozen=True)
class Salary:
    min_amount: Decimal | None
    max_amount: Decimal | None
    currency: str | None
    period: str | None


@dataclass(frozen=True)
class PostingFacts:
    """What the resolver compares: identity keys, normalized body and the
    structured facts used for conflict checks."""

    source: str
    identity: PostingIdentity
    body: str
    employment_type: str | None
    work_model: str | None
    salary: Salary | None

    @classmethod
    def from_content(
        cls, *, source: str, url: str | None, content: dict[str, Any]
    ) -> "PostingFacts":
        """From typed content (`NormalizedJob.content_fields()` or decoded
        snapshot values)."""

        def enum_value(value: object) -> str | None:
            return None if value is None else str(getattr(value, "value", value))

        salary = None
        if content.get("salary_min") is not None or content.get("salary_max") is not None:
            salary = Salary(
                content.get("salary_min"),
                content.get("salary_max"),
                content.get("salary_currency"),
                enum_value(content.get("salary_period")),
            )
        return cls(
            source=source,
            identity=identity_of(
                company_name=content.get("company_name"),
                title=content.get("title"),
                location=content.get("location"),
                url=url,
            ),
            body=normalized_body(
                content.get("description"),
                content.get("requirements") or [],
                content.get("responsibilities") or [],
            ),
            employment_type=enum_value(content.get("employment_type")),
            work_model=enum_value(content.get("work_model")),
            salary=salary,
        )


@dataclass(frozen=True)
class Comparison:
    decision: MatchDecision
    #: Internal diagnostics (logs/tests only; never returned by the API).
    reason: str
    similarity: float | None = None


def _conflicts(a: PostingFacts, b: PostingFacts) -> list[str]:
    conflicts: list[str] = []
    if a.employment_type and b.employment_type and a.employment_type != b.employment_type:
        conflicts.append("employment_type")
    if a.work_model and b.work_model and a.work_model != b.work_model:
        conflicts.append("work_model")
    if a.salary and b.salary:
        if a.salary.currency and b.salary.currency and a.salary.currency != b.salary.currency:
            conflicts.append("salary_currency")
        elif a.salary.period and b.salary.period and a.salary.period != b.salary.period:
            conflicts.append("salary_period")
        else:
            low_a = a.salary.min_amount or a.salary.max_amount
            high_a = a.salary.max_amount or a.salary.min_amount
            low_b = b.salary.min_amount or b.salary.max_amount
            high_b = b.salary.max_amount or b.salary.min_amount
            if (
                low_a is not None
                and high_a is not None
                and low_b is not None
                and high_b is not None
                and (high_a < low_b or high_b < low_a)
            ):
                conflicts.append("salary_range")
    return conflicts


def compare(incoming: PostingFacts, candidate: PostingFacts) -> Comparison:
    """Are these two postings the same job? (See module docs for the rules.)"""
    a, b = incoming.identity, candidate.identity
    if incoming.source == candidate.source:
        return Comparison(MatchDecision.NO_MATCH, "same_source")
    if not a.is_matchable or not b.is_matchable:
        return Comparison(MatchDecision.NO_MATCH, "missing_keys")
    if a.company_key != b.company_key:
        return Comparison(MatchDecision.NO_MATCH, "different_company")
    if a.title_key != b.title_key:
        return Comparison(MatchDecision.NO_MATCH, "different_title")
    if a.url_key and a.url_key == b.url_key:
        return Comparison(MatchDecision.EXACT, "same_url")
    if a.location_key is None or b.location_key is None:
        return Comparison(MatchDecision.POSSIBLE_MATCH, "location_unknown")
    if a.location_key != b.location_key:
        return Comparison(MatchDecision.NO_MATCH, "different_location")

    similarity = body_similarity(incoming.body, candidate.body)
    if similarity.words_a < MIN_BODY_WORDS or similarity.words_b < MIN_BODY_WORDS:
        return Comparison(MatchDecision.POSSIBLE_MATCH, "description_too_short", similarity.score)
    if similarity.score >= HIGH_CONFIDENCE_SIMILARITY:
        conflicts = _conflicts(incoming, candidate)
        if conflicts:
            return Comparison(
                MatchDecision.POSSIBLE_MATCH, "conflict:" + ",".join(conflicts), similarity.score
            )
        return Comparison(MatchDecision.HIGH_CONFIDENCE, "similar_description", similarity.score)
    if similarity.score >= POSSIBLE_MATCH_SIMILARITY:
        return Comparison(MatchDecision.POSSIBLE_MATCH, "somewhat_similar", similarity.score)
    return Comparison(MatchDecision.NO_MATCH, "different_description", similarity.score)


@dataclass(frozen=True)
class CandidateJob:
    job_id: uuid.UUID
    #: Every source the job already has a posting from.
    sources: frozenset[str]
    #: The job's postings that share the incoming posting's block key.
    postings: tuple[PostingFacts, ...]


@dataclass(frozen=True)
class Resolution:
    decision: MatchDecision
    #: The job to attach the posting to; set only for EXACT / HIGH_CONFIDENCE.
    job_id: uuid.UUID | None = None


def resolve(incoming: PostingFacts, candidates: list[CandidateJob]) -> Resolution:
    best: dict[uuid.UUID, MatchDecision] = {}
    for candidate in candidates:
        decision = MatchDecision.NO_MATCH
        for posting in candidate.postings:
            result = compare(incoming, posting).decision
            if _RANK[result] > _RANK[decision]:
                decision = result
        if decision in AUTO_MERGE_DECISIONS and incoming.source in candidate.sources:
            # The job already has a posting from this source, under another
            # id: the source itself says these are two vacancies.
            decision = MatchDecision.POSSIBLE_MATCH
        best[candidate.job_id] = decision

    for level in (MatchDecision.EXACT, MatchDecision.HIGH_CONFIDENCE):
        jobs = [job_id for job_id, decision in best.items() if decision is level]
        if len(jobs) == 1:
            return Resolution(level, jobs[0])
        if len(jobs) > 1:
            return Resolution(MatchDecision.POSSIBLE_MATCH)  # ambiguous: never guess
    if any(decision is MatchDecision.POSSIBLE_MATCH for decision in best.values()):
        return Resolution(MatchDecision.POSSIBLE_MATCH)
    return Resolution(MatchDecision.NO_MATCH)


def facts_of_posting(posting: JobSourcePosting, job: Job) -> PostingFacts:
    """Facts of a stored posting. Postings migrated from V0.3 have no
    snapshot of their own; their job's content *is* their content."""
    content = (
        decode_content(posting.normalized_data)
        if posting.normalized_data is not None
        else content_of_job(job)
    )
    # The posting's own display values are authoritative for its identity.
    content = {
        **content,
        "title": posting.title,
        "company_name": posting.company_name,
        "location": posting.location,
    }
    return PostingFacts.from_content(
        source=posting.source, url=posting.canonical_source_url, content=content
    )


def find_candidates(db: Session, identity: PostingIdentity) -> list[CandidateJob]:
    """Jobs with at least one posting in the same block (company + title).
    Must run while holding the block's identity lock."""
    if not identity.is_matchable:
        return []
    rows = db.execute(
        select(JobSourcePosting, Job)
        .join(Job, Job.id == JobSourcePosting.job_id)
        .where(
            JobSourcePosting.normalized_company_name == identity.company_key,
            JobSourcePosting.normalized_title == identity.title_key,
        )
        .order_by(JobSourcePosting.job_id, JobSourcePosting.id)
    ).all()
    if not rows:
        return []
    by_job: dict[uuid.UUID, list[PostingFacts]] = defaultdict(list)
    for posting, job in rows:
        by_job[job.id].append(facts_of_posting(posting, job))
    sources: dict[uuid.UUID, set[str]] = defaultdict(set)
    for job_id, source in db.execute(
        select(JobSourcePosting.job_id, JobSourcePosting.source).where(
            JobSourcePosting.job_id.in_(by_job)
        )
    ):
        sources[job_id].add(source)
    return [
        CandidateJob(job_id=job_id, sources=frozenset(sources[job_id]), postings=tuple(facts))
        for job_id, facts in by_job.items()
    ]
