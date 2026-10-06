"""Deterministic scoring components of `matching_v1`.

Each component compares one aspect of the candidate snapshot with the job
fact sheet and returns a `ComponentResult`: a status (match / partial /
mismatch / unknown), a 0..1 score, and the structured evidence it used --
candidate values with their origin and snapshot path, job values with the
posting text they came from. Deal breakers are evaluated separately
(`deal_breakers`). All numbers come from `model.py`.

Two rules hold everywhere:

- **Unknown is not missing.** When the job doesn't state something, or the
  candidate hasn't told JobPilot, the component is UNKNOWN: it is left out
  of the score instead of counting against the candidate.
- **Taxonomy keys, not strings.** Skills, languages, education levels,
  titles and locations are compared by their V0.5.1 taxonomy keys.
  Industries are the one exception (there is no industry taxonomy yet).
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property

from app.models.deal_breaker import DealBreakerType
from app.models.job import SalaryPeriod
from app.models.job_preferences import (
    EmploymentTypeValue,
    RelocationPreference,
    RemotePreference,
    TravelWillingness,
)
from app.models.job_requirement import JobRequirement, RequirementCategory, RequirementKind
from app.models.professional_profile import SeniorityLevel
from app.schemas.candidate_profile import CandidateProfileDocument, Origin
from app.schemas.job_facts import Fact, JobFacts, SalaryValue
from app.schemas.job_match import (
    CandidateEvidence,
    ComponentResult,
    DealBreakerResult,
    DetailValue,
    ItemResult,
    JobEvidence,
    RequirementImportance,
)
from app.services.job_discovery.normalization.text import fold
from app.services.matching import model
from app.services.matching.extraction import ExtractedRequirement
from app.services.taxonomy import (
    EDUCATION_LEVEL_VOCABULARY,
    LANGUAGES,
    SKILLS,
    NormalizationResult,
    normalize_location,
    normalize_title,
)
from app.services.taxonomy.languages import CEFR_ORDER

# --- Inputs -----------------------------------------------------------------------


@dataclass(frozen=True)
class RequirementView:
    """One job requirement as the engine sees it (a stored row or a fresh
    extraction)."""

    kind: RequirementKind
    category: RequirementCategory
    source_field: str
    id: uuid.UUID | None = None
    skill_key: str | None = None
    value_key: str | None = None
    value_text: str | None = None
    level: str | None = None
    min_years: Decimal | None = None
    section_kind: str | None = None
    evidence_text: str | None = None

    @classmethod
    def from_row(cls, row: JobRequirement) -> "RequirementView":
        return cls(
            id=row.id,
            kind=row.kind,
            category=row.category,
            source_field=row.source_field,
            skill_key=row.skill_key,
            value_key=row.value_key,
            value_text=row.value_text,
            level=row.level,
            min_years=row.min_years,
            section_kind=row.section_kind,
            evidence_text=row.evidence_text,
        )

    @classmethod
    def from_extracted(cls, requirement: ExtractedRequirement) -> "RequirementView":
        return cls(
            kind=requirement.kind,
            category=requirement.category,
            source_field=requirement.source_field,
            skill_key=requirement.skill_key,
            value_key=requirement.value_key,
            value_text=requirement.value_text,
            level=requirement.level,
            min_years=requirement.min_years,
            section_kind=requirement.section_kind,
            evidence_text=requirement.evidence_text,
        )

    @property
    def importance(self) -> RequirementImportance:
        return self.kind.value  # type: ignore[return-value]

    @property
    def weight(self) -> float:
        return model.IMPORTANCE_WEIGHTS.get(self.kind.value, 0.0)

    def evidence(self, value: str | None = None) -> JobEvidence:
        return JobEvidence(
            value=(value or self.value_text or self.skill_key or self.value_key or "?")[:500],
            source_field=self.source_field,
            section=self.section_kind,
            text=self.evidence_text,
            requirement_id=self.id,
        )


@dataclass(frozen=True)
class JobView:
    facts: JobFacts
    requirements: tuple[RequirementView, ...] = ()

    def of(self, category: RequirementCategory, *, scored: bool = True) -> list[RequirementView]:
        return [
            r
            for r in self.requirements
            if r.category is category and (not scored or r.kind is not RequirementKind.CONTEXTUAL)
        ]


# --- Labels -----------------------------------------------------------------------

WORK_MODEL_LABELS = {
    RemotePreference.ONSITE: "On-site",
    RemotePreference.REMOTE: "Remote",
    RemotePreference.HYBRID: "Hybrid",
    RemotePreference.FLEXIBLE: "Flexible",
}
EMPLOYMENT_TYPE_LABELS = {
    EmploymentTypeValue.FULL_TIME: "Full-time",
    EmploymentTypeValue.PART_TIME: "Part-time",
    EmploymentTypeValue.CONTRACT: "Contract",
    EmploymentTypeValue.INTERNSHIP: "Internship",
    EmploymentTypeValue.TEMPORARY: "Temporary",
    EmploymentTypeValue.FREELANCE: "Freelance",
    EmploymentTypeValue.WORKING_STUDENT: "Working student",
}
SENIORITY_LABELS = {level: level.value.capitalize() for level in SeniorityLevel}
TRAVEL_LABELS = {
    TravelWillingness.NONE: "No travel",
    TravelWillingness.OCCASIONAL: "Occasional travel",
    TravelWillingness.FREQUENT: "Frequent travel",
    TravelWillingness.FLEXIBLE: "Flexible about travel",
}


def years_label(value: float | Decimal) -> str:
    number = float(value)
    text = f"{number:g}"
    return f"{text} year" if number == 1 else f"{text} years"


def money(amount: Decimal | None) -> str:
    return f"{amount:,.0f}" if amount is not None else "?"


def _status(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= model.MATCH_AT:
        return "match"
    return "partial" if score >= model.MISMATCH_BELOW else "mismatch"


def _component(
    key: str,
    group: str,
    score: float | None,
    reason_code: str,
    *,
    label: str | None = None,
    status: str | None = None,
    candidate: Iterable[CandidateEvidence] = (),
    job: Iterable[JobEvidence] = (),
    items: Iterable[ItemResult] = (),
    details: dict[str, DetailValue] | None = None,
) -> ComponentResult:
    weights = model.JOB_WEIGHTS if group == "job" else model.PREFERENCE_WEIGHTS
    score = None if score is None else round(score, 4)
    return ComponentResult.model_validate(
        {
            "key": key,
            "group": group,
            "status": status or _status(score),
            "label": label,
            "score": score,
            "weight": weights[key],
            "reason_code": reason_code,
            "candidate": list(candidate),
            "job": list(job),
            "items": list(items),
            "details": details or {},
        }
    )


def _sourced(value: str, ref: str, origin: Origin | None) -> CandidateEvidence:
    return CandidateEvidence(
        value=value[:500],
        origin=origin.origin if origin else "user",
        ref=ref,
        evidence_ref=origin.evidence_ref if origin else None,
    )


def _user(value: str, ref: str) -> CandidateEvidence:
    return CandidateEvidence(value=value[:500], origin="user", ref=ref)


def _fact_evidence(fact: Fact, value: str) -> JobEvidence:  # type: ignore[type-arg]
    return JobEvidence(
        value=value[:500], source_field=fact.source_field, text=fact.evidence_text
    )


def _weighted(units: Sequence[tuple[float, float]]) -> float | None:
    """Weighted mean of (weight, score) pairs; None when nothing counts."""
    total = sum(w for w, _ in units)
    if total <= 0:
        return None
    return sum(w * s for w, s in units) / total


# --- Places (no geocoding: taxonomy keys only) ------------------------------------


@dataclass(frozen=True)
class Place:
    text: str
    key: str
    category: str
    label: str
    country: str | None
    region: str | None
    remote: bool

    @classmethod
    def parse(cls, text: str | None) -> "Place | None":
        result = normalize_location(text)
        if result is None or not result.is_match or result.key is None:
            return None
        country = result.detail("country") or None
        if result.category == "country":
            country = result.key.split(":")[1].upper()
        region = result.detail("region") or (result.key if result.category == "region" else None)
        return cls(
            text=result.input,
            key=result.key,
            category=result.category or "",
            label=result.label or result.input,
            country=country,
            region=region,
            remote=result.key == "remote" or result.detail("remote") == "true",
        )

    @property
    def is_place(self) -> bool:
        """A real place, not just "Remote"."""
        return self.key != "remote"


def place_relation(candidate: Place, job: Place) -> str | None:
    """How a job place relates to one of the candidate's places."""
    if not candidate.is_place or not job.is_place:
        return None
    if candidate.key == job.key:
        return "same_place"
    if candidate.category == "country" and job.country == candidate.country:
        return "in_country"
    if candidate.category == "region" and candidate.key in (job.region, job.key):
        return "in_region"
    if candidate.country and job.country:
        return "same_country" if candidate.country == job.country else "other_country"
    return None


_PREFERRED_RELATIONS = ("same_place", "in_country", "in_region")


# --- Titles -----------------------------------------------------------------------


def same_role(a: NormalizationResult | None, b: NormalizationResult | None) -> bool | None:
    """True / False when two titles are known to name the same / a
    different role (taxonomy keys; or identical role wording outside the
    vocabulary); None when that can't be told."""
    if a is None or b is None:
        return None
    if a.is_match and b.is_match:
        return a.key == b.key
    role_a, role_b = a.detail("role"), b.detail("role")
    if role_a and role_b and role_a == role_b:
        return True
    return None


# --- Context ----------------------------------------------------------------------


@dataclass
class Context:
    candidate: CandidateProfileDocument
    job: JobView
    #: Set by `location`: does taking this job need a move? None = unknown.
    needs_relocation: bool | None = None
    #: Set by `location`: the job is remote.
    remote_job: bool = False
    #: Set by `location`: is a job place within the preferred locations?
    in_preferred_location: bool | None = None
    #: Set by `salary`: (job top amount, candidate minimum) when comparable.
    salary_comparison: tuple[Decimal, Decimal | None] | None = None
    blocking_flags: dict[str, str] = field(default_factory=dict)

    @property
    def facts(self) -> JobFacts:
        return self.job.facts

    @cached_property
    def job_title(self) -> NormalizationResult | None:
        return normalize_title(self.facts.title.value) if self.facts.title else None

    @cached_property
    def work_model(self) -> RemotePreference | None:
        return self.facts.work_model.value if self.facts.work_model else None


# --- Job Match components -----------------------------------------------------------


def skills(ctx: Context) -> ComponentResult:
    candidate = ctx.candidate
    owned = {s.skill_key: (i, s) for i, s in enumerate(candidate.skills) if s.skill_key}
    has_skills = bool(candidate.skills)
    requirements = ctx.job.of(RequirementCategory.SKILL, scored=False)
    items: list[ItemResult] = []
    units: list[tuple[float, float]] = []
    required_missing = False

    def owned_evidence(key: str) -> CandidateEvidence:
        index, skill = owned[key]
        return _sourced(skill.name, f"skills/{index}", skill)

    def score_one(r: RequirementView) -> tuple[float, str]:
        assert r.skill_key is not None
        _, skill = owned[r.skill_key]
        if r.min_years is not None and skill.years is not None and skill.years < r.min_years:
            return model.SKILL_PARTIAL_YEARS_SCORE, "fewer_years_than_asked"
        return 1.0, "skill_match"

    groups: dict[str, list[RequirementView]] = {}
    for r in requirements:
        if r.skill_key is None:
            label = r.value_text or "?"
            items.append(
                ItemResult(
                    key=fold(label).replace(" ", "_") or "unknown",
                    label=label,
                    importance=r.importance,
                    status="unknown",
                    reason_code="skill_term_ambiguous",
                    job=r.evidence(),
                )
            )
            continue
        label = SKILLS.entry(r.skill_key).label
        if r.kind is RequirementKind.CONTEXTUAL:
            has = r.skill_key in owned
            items.append(
                ItemResult(
                    key=r.skill_key,
                    label=label,
                    importance="contextual",
                    status="match" if has else "unknown",
                    reason_code="mentioned_skill_you_have" if has else "mentioned_not_required",
                    candidate=owned_evidence(r.skill_key) if has else None,
                    job=r.evidence(label),
                )
            )
            continue
        if r.value_key and r.value_key.startswith("any_of:"):
            groups.setdefault(r.value_key, []).append(r)
            continue
        if not has_skills:
            items.append(
                ItemResult(
                    key=r.skill_key, label=label, importance=r.importance, status="unknown",
                    reason_code="candidate_skills_unknown", job=r.evidence(label),
                )
            )
            continue
        if r.skill_key in owned:
            score, reason = score_one(r)
            items.append(
                ItemResult(
                    key=r.skill_key, label=label, importance=r.importance,
                    status="match" if score >= model.MATCH_AT else "partial", score=score,
                    reason_code=reason, candidate=owned_evidence(r.skill_key),
                    job=r.evidence(label),
                )
            )
        else:
            score = 0.0
            required_missing |= r.kind is RequirementKind.REQUIRED
            items.append(
                ItemResult(
                    key=r.skill_key, label=label, importance=r.importance, status="missing",
                    score=0.0, reason_code="skill_missing", job=r.evidence(label),
                )
            )
        units.append((r.weight, score))

    for members in groups.values():
        group_label = " or ".join(SKILLS.entry(m.skill_key).label for m in members if m.skill_key)
        strongest = max(members, key=lambda m: m.weight)
        if not has_skills:
            for m in members:
                assert m.skill_key is not None
                items.append(
                    ItemResult(
                        key=m.skill_key, label=SKILLS.entry(m.skill_key).label,
                        importance=m.importance, status="unknown",
                        reason_code="candidate_skills_unknown", job=m.evidence(),
                        group=group_label[:50],
                    )
                )
            continue
        best = 0.0
        for m in members:
            if m.skill_key in owned:
                best = max(best, score_one(m)[0])
        for m in members:
            assert m.skill_key is not None
            label = SKILLS.entry(m.skill_key).label
            if m.skill_key in owned:
                score, reason = score_one(m)
                item = ItemResult(
                    key=m.skill_key, label=label, importance=m.importance,
                    status="match" if score >= model.MATCH_AT else "partial", score=score,
                    reason_code=reason, candidate=owned_evidence(m.skill_key),
                    job=m.evidence(label), group=group_label[:50],
                )
            elif best > 0:
                item = ItemResult(
                    key=m.skill_key, label=label, importance=m.importance, status="unknown",
                    reason_code="alternative_satisfied", job=m.evidence(label),
                    group=group_label[:50],
                )
            else:
                item = ItemResult(
                    key=m.skill_key, label=label, importance=m.importance, status="missing",
                    score=0.0, reason_code="skill_missing", job=m.evidence(label),
                    group=group_label[:50],
                )
            items.append(item)
        if best == 0:
            required_missing |= strongest.kind is RequirementKind.REQUIRED
        units.append((strongest.weight, best))

    if required_missing:
        ctx.blocking_flags["skills"] = "required_skill_missing"
    if not units:
        asked = any(r.kind is not RequirementKind.CONTEXTUAL for r in requirements)
        reason = (
            "candidate_skills_unknown" if asked and not has_skills else "no_skill_requirements"
        )
        return _component("skills", "job", None, reason, items=items)
    matched = sum(1 for i in items if i.status == "match" and i.importance != "contextual")
    return _component(
        "skills",
        "job",
        _weighted(units),
        "skills_compared",
        items=items,
        details={"matched": matched, "compared": len(units)},
    )


def experience(ctx: Context) -> ComponentResult:
    requirements = ctx.job.of(RequirementCategory.EXPERIENCE)
    asked = [r for r in requirements if r.kind is not RequirementKind.PREFERRED] or requirements
    years = ctx.candidate.role.years_of_experience
    candidate_evidence = (
        [_sourced(years_label(years.value), "role/years_of_experience", years)] if years else []
    )
    if not asked:
        return _component(
            "experience", "job", None, "experience_not_stated", candidate=candidate_evidence
        )
    requirement = max(asked, key=lambda r: r.min_years or 0)
    assert requirement.min_years is not None
    required = float(requirement.min_years)
    job_evidence = [requirement.evidence(f"{required:g}+ years")]
    details: dict[str, DetailValue] = {
        "required_years": required,
        "importance": requirement.importance,
    }
    if years is None:
        return _component(
            "experience", "job", None, "candidate_experience_unknown",
            job=job_evidence, details=details,
        )
    have = years.value
    details["candidate_years"] = have
    if have >= required:
        score, reason = model.EXPERIENCE_SCORES["meets"], "experience_meets"
    elif have >= required - 1:
        score, reason = model.EXPERIENCE_SCORES["within_one_year"], "experience_slightly_below"
    elif have >= required / 2:
        score, reason = model.EXPERIENCE_SCORES["at_least_half"], "experience_below"
    else:
        score, reason = model.EXPERIENCE_SCORES["below_half"], "experience_gap"
        if requirement.kind is RequirementKind.REQUIRED:
            ctx.blocking_flags["experience"] = "required_experience_missing"
    return _component(
        "experience", "job", score, reason,
        candidate=candidate_evidence, job=job_evidence, details=details,
    )


def title(ctx: Context) -> ComponentResult:
    role = ctx.candidate.role
    job_title = ctx.job_title
    job_evidence = (
        [_fact_evidence(ctx.facts.title, ctx.facts.title.value)] if ctx.facts.title else []
    )
    if job_title is None:
        return _component("title", "job", None, "job_title_unknown")
    current = normalize_title(role.current_title.value) if role.current_title else None
    candidate_evidence = (
        [_sourced(role.current_title.value, "role/current_title", role.current_title)]
        if role.current_title
        else []
    )
    relation = same_role(current, job_title)
    if relation:
        return _component(
            "title", "job", model.TITLE_SCORES["strong_match"], "same_role",
            label="strong_match", candidate=candidate_evidence, job=job_evidence,
        )
    for index, previous in enumerate(role.previous_titles):
        if same_role(normalize_title(previous.value), job_title):
            return _component(
                "title", "job", model.TITLE_SCORES["related_role"], "previous_role",
                label="related_role",
                candidate=[_sourced(previous.value, f"role/previous_titles/{index}", previous)],
                job=job_evidence,
            )
    if relation is False:
        return _component(
            "title", "job", model.TITLE_SCORES["weak_match"], "different_role",
            label="weak_match", candidate=candidate_evidence, job=job_evidence,
        )
    reason = "candidate_title_unknown" if role.current_title is None else "role_not_comparable"
    return _component(
        "title", "job", None, reason, label="unknown",
        candidate=candidate_evidence, job=job_evidence,
    )


def seniority(ctx: Context) -> ComponentResult:
    level = ctx.candidate.role.seniority_level
    fact = ctx.facts.seniority
    candidate_evidence = (
        [_sourced(SENIORITY_LABELS[level.value], "role/seniority_level", level)] if level else []
    )
    job_evidence = [_fact_evidence(fact, SENIORITY_LABELS[fact.value])] if fact else []
    if fact is None:
        return _component(
            "seniority", "job", None, "job_seniority_unknown", candidate=candidate_evidence
        )
    if level is None:
        return _component(
            "seniority", "job", None, "candidate_seniority_unknown", job=job_evidence
        )
    score = model.SENIORITY_MATRIX[level.value][fact.value]
    difference = model.SENIORITY_RANK[level.value] - model.SENIORITY_RANK[fact.value]
    if difference == 0:
        reason = "same_level"
    elif difference > 0:
        reason = "above_level" if score >= model.MATCH_AT else "overqualified"
    else:
        reason = "slightly_below_level" if score >= model.MISMATCH_BELOW else "below_level"
    return _component(
        "seniority", "job", score, reason,
        candidate=candidate_evidence, job=job_evidence,
        details={"candidate_level": level.value.value, "job_level": fact.value.value},
    )


def _industry(
    key: str, names: Sequence[str], refs: str, fact: Fact | None  # type: ignore[type-arg]
) -> ComponentResult:
    group = "job" if key == "industry" else "preference"
    candidate_evidence = [_user(name, f"{refs}/{i}") for i, name in enumerate(names)]
    if fact is None:
        return _component(key, group, None, "job_industry_unknown", candidate=candidate_evidence)
    job_evidence = [_fact_evidence(fact, fact.value)]
    if not names:
        return _component(key, group, None, "candidate_industry_unknown", job=job_evidence)
    for index, name in enumerate(names):
        if fold(name) == fold(fact.value):
            return _component(
                key, group, model.INDUSTRY_SCORES["match"], "industry_match",
                candidate=[_user(name, f"{refs}/{index}")], job=job_evidence,
            )
    return _component(
        key, group, model.INDUSTRY_SCORES["no_match"], "industry_different",
        candidate=candidate_evidence, job=job_evidence,
    )


def industry(ctx: Context) -> ComponentResult:
    return _industry(
        "industry", ctx.candidate.role.industries, "role/industries", ctx.facts.industry
    )


def languages(ctx: Context) -> ComponentResult:
    owned = {
        entry.language_key: (i, entry) for i, entry in enumerate(ctx.candidate.languages)
    }
    requirements = ctx.job.of(RequirementCategory.LANGUAGE)
    if not requirements:
        return _component("languages", "job", None, "no_language_requirements")
    items: list[ItemResult] = []
    units: list[tuple[float, float]] = []
    for r in requirements:
        code = r.value_key or ""
        label = LANGUAGES.entry(code).label if LANGUAGES.has_key(code) else code
        asked = f"{label} {r.level}" if r.level else label
        if not owned:
            items.append(
                ItemResult(
                    key=code, label=asked, importance=r.importance, status="unknown",
                    reason_code="candidate_languages_unknown", job=r.evidence(asked),
                )
            )
            continue
        if code not in owned:
            if r.kind is RequirementKind.REQUIRED:
                ctx.blocking_flags["languages"] = "required_language_missing"
            items.append(
                ItemResult(
                    key=code, label=asked, importance=r.importance, status="missing",
                    score=0.0, reason_code="language_missing", job=r.evidence(asked),
                )
            )
            units.append((r.weight, model.LANGUAGE_SCORES["missing"]))
            continue
        index, entry = owned[code]
        have = entry.level
        evidence = _sourced(
            f"{label} {have}" if have != "unknown" else label, f"languages/{index}", entry
        )
        if r.level is None or have == "unknown":
            score = model.LANGUAGE_SCORES["level_not_comparable" if r.level else "meets"]
            reason = "language_level_not_compared" if r.level else "language_match"
        elif have == "native" or (
            r.level != "native" and CEFR_ORDER.index(have) >= CEFR_ORDER.index(r.level)  # type: ignore[arg-type]
        ):
            score, reason = model.LANGUAGE_SCORES["meets"], "language_level_meets"
        else:
            score, reason = model.LANGUAGE_SCORES["below_level"], "language_level_below"
        items.append(
            ItemResult(
                key=code, label=asked, importance=r.importance,
                status="match" if score >= model.MATCH_AT else "partial", score=score,
                reason_code=reason, candidate=evidence, job=r.evidence(asked),
            )
        )
        units.append((r.weight, score))
    if not units:
        return _component("languages", "job", None, "candidate_languages_unknown", items=items)
    return _component("languages", "job", _weighted(units), "languages_compared", items=items)


def education(ctx: Context) -> ComponentResult:
    requirements = ctx.job.of(RequirementCategory.EDUCATION)
    entries = [
        (i, e) for i, e in enumerate(ctx.candidate.credentials.education) if e.degree_level
    ]
    best = (
        max(entries, key=lambda pair: model.EDUCATION_ORDER.index(pair[1].degree_level or ""))
        if entries
        else None
    )
    if not requirements:
        return _component("education", "job", None, "education_not_stated")
    candidate_evidence = []
    if best is not None:
        index, entry = best
        label = EDUCATION_LEVEL_VOCABULARY.entry(entry.degree_level or "").label
        candidate_evidence = [
            _sourced(entry.degree or label, f"credentials/education/{index}", entry)
        ]
    items: list[ItemResult] = []
    units: list[tuple[float, float]] = []
    for r in requirements:
        level = r.value_key or ""
        label = EDUCATION_LEVEL_VOCABULARY.entry(level).label
        if best is None:
            items.append(
                ItemResult(
                    key=level, label=label, importance=r.importance, status="unknown",
                    reason_code="candidate_education_unknown", job=r.evidence(label),
                )
            )
            continue
        have = model.EDUCATION_ORDER.index(best[1].degree_level or "")
        if have >= model.EDUCATION_ORDER.index(level):
            score, status, reason = model.EDUCATION_SCORES["meets"], "match", "education_meets"
        elif r.level == "or_equivalent":
            score, status, reason = (
                model.EDUCATION_SCORES["below_or_equivalent_accepted"],
                "partial",
                "education_below_equivalent_accepted",
            )
        else:
            score, status, reason = model.EDUCATION_SCORES["below"], "missing", "education_below"
            if r.kind is RequirementKind.REQUIRED:
                ctx.blocking_flags["education"] = "required_education_missing"
        items.append(
            ItemResult(
                key=level, label=label, importance=r.importance, status=status,  # type: ignore[arg-type]
                score=score, reason_code=reason, candidate=candidate_evidence[0],
                job=r.evidence(label),
            )
        )
        units.append((r.weight, score))
    if not units:
        return _component("education", "job", None, "candidate_education_unknown", items=items)
    return _component(
        "education", "job", _weighted(units), "education_compared",
        candidate=candidate_evidence, items=items,
    )


# --- Preference Match components ----------------------------------------------------


def location(ctx: Context) -> ComponentResult:
    mobility = ctx.candidate.location_and_mobility
    preferred = [
        (i, Place.parse(value.text)) for i, value in enumerate(mobility.preferred_locations)
    ]
    current = (
        Place.parse(mobility.current_location.value.text) if mobility.current_location else None
    )
    candidate_evidence = [
        _user(value.text, f"location_and_mobility/preferred_locations/{i}")
        for i, value in enumerate(mobility.preferred_locations)
    ]
    if mobility.current_location:
        candidate_evidence.append(
            _sourced(
                mobility.current_location.value.text,
                "location_and_mobility/current_location",
                mobility.current_location,
            )
        )
    relocation = mobility.relocation
    details: dict[str, DetailValue] = {
        "relocation": relocation.value if relocation else None,
        "work_model": ctx.work_model.value if ctx.work_model else None,
    }
    fact = ctx.facts.location
    job_places = [Place.parse(v.text) for v in fact.value] if fact else []
    job_evidence = [_fact_evidence(fact, v.text) for v in fact.value] if fact else []
    preferred_places = [p for _, p in preferred if p is not None]
    known_places = [p for p in [*preferred_places, current] if p is not None]

    remote_flagged = [p for p in job_places if p is not None and p.remote]
    remote = ctx.work_model is RemotePreference.REMOTE or (
        bool(job_places)
        and len(remote_flagged) == len(job_places)
        and ctx.work_model not in model.PRESENCE_WORK_MODELS
    )
    ctx.remote_job = remote
    if remote:
        ctx.needs_relocation = False
        ctx.in_preferred_location = True
    if not mobility.preferred_locations and current is None:
        return _component(
            "location", "preference", None,
            "candidate_location_unknown" if not mobility.current_location
            else "candidate_location_unrecognized",
            job=job_evidence, details=details,
        )
    if remote:
        countries = {p.country for p in remote_flagged if p.country}
        wants_remote = any(not p.is_place for p in preferred_places)
        candidate_countries = {p.country for p in known_places if p.country}
        if countries and candidate_countries and not wants_remote and not (
            candidate_countries & countries
        ):
            return _component(
                "location", "preference", model.LOCATION_SCORES["remote_other_country"],
                "remote_in_other_country", candidate=candidate_evidence, job=job_evidence,
                details={**details, "remote_countries": ", ".join(sorted(countries))},
            )
        return _component(
            "location", "preference", model.LOCATION_SCORES["remote"], "remote_job",
            candidate=candidate_evidence, job=job_evidence, details=details,
        )
    places = [p for p in job_places if p is not None and p.is_place]
    if not places:
        ctx.needs_relocation = None
        return _component(
            "location", "preference", None,
            "job_location_unknown" if not fact else "job_location_unrecognized",
            candidate=candidate_evidence, job=job_evidence, details=details,
        )
    if not known_places:
        return _component(
            "location", "preference", None, "candidate_location_unrecognized",
            candidate=candidate_evidence, job=job_evidence, details=details,
        )
    # Only an on-site or hybrid job is known to need the candidate there.
    presence = True if ctx.work_model in model.PRESENCE_WORK_MODELS else None

    def relations(candidates: Iterable[Place]) -> set[str]:
        return {
            rel
            for c in candidates
            for j in places
            if (rel := place_relation(c, j)) is not None
        }

    preferred_relations = relations(preferred_places)
    if preferred_places:
        ctx.in_preferred_location = bool(preferred_relations & set(_PREFERRED_RELATIONS))
    if preferred_relations & set(_PREFERRED_RELATIONS):
        ctx.needs_relocation = False
        return _component(
            "location", "preference", model.LOCATION_SCORES["preferred_location"],
            "in_preferred_location", candidate=candidate_evidence, job=job_evidence,
            details=details,
        )
    current_relations = relations([current]) if current else set()
    if current_relations & set(_PREFERRED_RELATIONS):
        ctx.needs_relocation = False
        return _component(
            "location", "preference", model.LOCATION_SCORES["current_location"],
            "at_current_location", candidate=candidate_evidence, job=job_evidence,
            details=details,
        )
    all_relations = preferred_relations | current_relations
    answer = relocation.value if relocation else "unstated"
    if "same_country" in all_relations:
        score = model.LOCATION_SAME_COUNTRY[answer]
        reason = "other_city_same_country"
    elif "other_country" in all_relations:
        score = model.LOCATION_OTHER_COUNTRY[answer]
        reason = "other_country"
    else:
        ctx.needs_relocation = None
        return _component(
            "location", "preference", None, "location_not_comparable",
            candidate=candidate_evidence, job=job_evidence, details=details,
        )
    ctx.needs_relocation = presence
    details["relocation_needed"] = presence
    return _component(
        "location", "preference", score, reason,
        candidate=candidate_evidence, job=job_evidence, details=details,
    )


def work_model(ctx: Context) -> ComponentResult:
    accepted = ctx.candidate.preferences.work_models
    candidate_evidence = [
        _user(WORK_MODEL_LABELS[m], "preferences/work_models") for m in accepted
    ]
    fact = ctx.facts.work_model
    if fact is None:
        return _component(
            "work_model", "preference", None, "job_work_model_unknown",
            candidate=candidate_evidence,
        )
    job_evidence = [_fact_evidence(fact, WORK_MODEL_LABELS[fact.value])]
    if not accepted:
        return _component(
            "work_model", "preference", None, "no_work_model_preference", job=job_evidence
        )
    job_model = fact.value
    if (
        job_model is RemotePreference.FLEXIBLE
        or RemotePreference.FLEXIBLE in accepted
        or job_model in accepted
    ):
        score, reason = 1.0, "work_model_match"
    else:
        table = model.WORK_MODEL_SCORES[job_model]
        score = max(table.get(m, 0.0) for m in accepted)
        reason = (
            "work_model_partly_compatible"
            if score >= model.MISMATCH_BELOW
            else "work_model_mismatch"
        )
    return _component(
        "work_model", "preference", score, reason,
        candidate=candidate_evidence, job=job_evidence,
    )


def salary_label(value: SalaryValue) -> str:
    """"70,000–90,000 EUR per year" (the posting's own figures)."""
    if value.minimum is not None and value.maximum is not None:
        shown = f"{money(value.minimum)}–{money(value.maximum)}"
    elif value.maximum is not None:
        shown = f"up to {money(value.maximum)}"
    else:
        shown = f"from {money(value.minimum)}"
    parts = [shown, value.currency or "", f"per {value.period.value}" if value.period else ""]
    return " ".join(part for part in parts if part)


def _annual(amount: Decimal | None, period: SalaryPeriod | None) -> Decimal | None:
    if amount is None:
        return None
    if period is SalaryPeriod.YEAR:
        return amount
    if period is SalaryPeriod.MONTH:
        return amount * 12
    return None


def salary(ctx: Context) -> ComponentResult:
    prefs = ctx.candidate.preferences
    minimum, desired, currency = prefs.minimum_salary, prefs.desired_salary, prefs.currency
    candidate_evidence = []
    if minimum is not None:
        candidate_evidence.append(
            _user(
                f"Minimum {money(minimum)} {currency or ''}".strip(), "preferences/minimum_salary"
            )
        )
    if desired is not None:
        candidate_evidence.append(
            _user(
                f"Desired {money(desired)} {currency or ''}".strip(), "preferences/desired_salary"
            )
        )
    fact = ctx.facts.salary
    if fact is None:
        return _component(
            "salary", "preference", None, "salary_not_stated", candidate=candidate_evidence
        )
    value = fact.value
    job_evidence = [_fact_evidence(fact, salary_label(value))]
    details: dict[str, DetailValue] = {
        "job_minimum": float(value.minimum) if value.minimum is not None else None,
        "job_maximum": float(value.maximum) if value.maximum is not None else None,
        "job_currency": value.currency,
        "job_period": value.period.value if value.period else None,
    }
    if minimum is None and desired is None:
        return _component(
            "salary", "preference", None, "no_salary_preference", job=job_evidence, details=details
        )
    if currency is None or value.currency is None:
        return _component(
            "salary", "preference", None, "salary_currency_unknown",
            candidate=candidate_evidence, job=job_evidence, details=details,
        )
    if currency != value.currency:
        return _component(
            "salary", "preference", None, "salary_currency_differs",
            candidate=candidate_evidence, job=job_evidence, details=details,
        )
    top_amount = value.maximum if value.maximum is not None else value.minimum
    top = _annual(top_amount, value.period)
    if top is None:
        return _component(
            "salary", "preference", None, "salary_period_not_comparable",
            candidate=candidate_evidence, job=job_evidence, details=details,
        )
    details["job_annual_top"] = float(top)
    ctx.salary_comparison = (top, minimum)
    floor = minimum if minimum is not None else desired
    assert floor is not None
    if desired is not None and top >= desired:
        score, reason = model.SALARY_SCORES["meets_desired"], "salary_meets_desired"
    elif top >= floor:
        score, reason = model.SALARY_SCORES["meets_minimum"], "salary_meets_minimum"
    else:
        score, reason = model.SALARY_SCORES["below_minimum"], "salary_below_minimum"
    return _component(
        "salary", "preference", score, reason,
        candidate=candidate_evidence, job=job_evidence, details=details,
    )


def employment_type(ctx: Context) -> ComponentResult:
    accepted = ctx.candidate.preferences.employment_types
    candidate_evidence = [
        _user(EMPLOYMENT_TYPE_LABELS[t], "preferences/employment_types") for t in accepted
    ]
    fact = ctx.facts.employment_type
    if fact is None:
        return _component(
            "employment_type", "preference", None, "job_employment_type_unknown",
            candidate=candidate_evidence,
        )
    job_evidence = [_fact_evidence(fact, EMPLOYMENT_TYPE_LABELS[fact.value])]
    if not accepted:
        return _component(
            "employment_type", "preference", None, "no_employment_type_preference",
            job=job_evidence,
        )
    if fact.value in accepted:
        return _component(
            "employment_type", "preference", 1.0, "employment_type_match",
            candidate=candidate_evidence, job=job_evidence,
        )
    return _component(
        "employment_type", "preference", 0.0, "employment_type_mismatch",
        candidate=candidate_evidence, job=job_evidence,
    )


def desired_title(ctx: Context) -> ComponentResult:
    targets = ctx.candidate.role.target_titles
    candidate_evidence = [_user(t.title, f"role/target_titles/{i}") for i, t in enumerate(targets)]
    if not targets:
        return _component("desired_title", "preference", None, "no_desired_titles")
    if ctx.job_title is None or ctx.facts.title is None:
        return _component(
            "desired_title", "preference", None, "job_title_unknown", candidate=candidate_evidence
        )
    job_evidence = [_fact_evidence(ctx.facts.title, ctx.facts.title.value)]
    relations = [(t, same_role(normalize_title(t.title), ctx.job_title)) for t in targets]
    matches = [t for t, related in relations if related]
    if matches:
        best = max(matches, key=lambda t: model.DESIRED_TITLE_SCORES[t.priority.value])
        index = targets.index(best)
        return _component(
            "desired_title", "preference", model.DESIRED_TITLE_SCORES[best.priority.value],
            "desired_title_match", label=best.priority.value,
            candidate=[_user(best.title, f"role/target_titles/{index}")], job=job_evidence,
        )
    if all(related is False for _, related in relations):
        return _component(
            "desired_title", "preference", model.DESIRED_TITLE_SCORES["other"],
            "not_a_desired_title", candidate=candidate_evidence, job=job_evidence,
        )
    return _component(
        "desired_title", "preference", None, "desired_title_not_comparable",
        candidate=candidate_evidence, job=job_evidence,
    )


def preferred_industry(ctx: Context) -> ComponentResult:
    return _industry(
        "preferred_industry",
        ctx.candidate.preferences.industries,
        "preferences/industries",
        ctx.facts.industry,
    )


def travel(ctx: Context) -> ComponentResult:
    willingness = ctx.candidate.location_and_mobility.travel
    candidate_evidence = (
        [_user(TRAVEL_LABELS[willingness], "location_and_mobility/travel")] if willingness else []
    )
    fact = ctx.facts.travel
    if fact is None:
        return _component(
            "travel", "preference", None, "travel_not_stated", candidate=candidate_evidence
        )
    job_evidence = [_fact_evidence(fact, TRAVEL_LABELS[fact.value])]
    if willingness is None:
        return _component(
            "travel", "preference", None, "no_travel_preference", job=job_evidence
        )
    job_travel = (
        TravelWillingness.OCCASIONAL if fact.value is TravelWillingness.FLEXIBLE else fact.value
    )
    score = model.TRAVEL_SCORES[willingness][job_travel]
    reason = "travel_fits" if score >= model.MATCH_AT else "more_travel_than_wanted"
    return _component(
        "travel", "preference", score, reason, candidate=candidate_evidence, job=job_evidence
    )


JOB_COMPONENTS = (skills, experience, title, seniority, industry, languages, education)
PREFERENCE_COMPONENTS = (
    location, work_model, salary, employment_type, desired_title, preferred_industry, travel
)


# --- Deal breakers --------------------------------------------------------------------


def _parse_employment_types(value: str | None) -> set[EmploymentTypeValue]:
    types = set()
    for part in (value or "").replace(";", ",").split(","):
        text = part.strip().lower().replace("-", "_").replace(" ", "_")
        try:
            types.add(EmploymentTypeValue(text))
        except ValueError:
            continue
    return types


def deal_breakers(ctx: Context) -> list[DealBreakerResult]:
    """Evaluate the user's *explicitly enabled* deal breakers, plus a
    "relocation: no" answer. Nothing else can block a job."""
    results: list[DealBreakerResult] = []

    def add(kind: str, source: str, status: str, reason: str, **details: DetailValue) -> None:
        results.append(
            DealBreakerResult.model_validate(
                {
                    "type": kind,
                    "source": source,
                    "status": status,
                    "reason_code": reason,
                    "details": details,
                }
            )
        )

    enabled = {d.type for d in ctx.candidate.deal_breakers if d.enabled}
    values = {d.type: d.value for d in ctx.candidate.deal_breakers if d.enabled}
    prefs = ctx.candidate.preferences
    job_model = ctx.work_model
    job_type = ctx.facts.employment_type.value if ctx.facts.employment_type else None
    job_travel = ctx.facts.travel.value if ctx.facts.travel else None

    for kind in sorted(enabled, key=lambda k: k.value):
        name = kind.value
        if kind is DealBreakerType.MINIMUM_SALARY:
            if prefs.minimum_salary is None:
                add(name, "deal_breaker", "unknown", "no_minimum_salary_set")
            elif ctx.salary_comparison is None:
                add(name, "deal_breaker", "unknown", "salary_not_comparable")
            elif ctx.salary_comparison[0] < prefs.minimum_salary:
                add(
                    name, "deal_breaker", "hard_block", "salary_below_minimum",
                    job_annual_top=float(ctx.salary_comparison[0]),
                    minimum=float(prefs.minimum_salary),
                )
            else:
                add(name, "deal_breaker", "clear", "salary_meets_minimum")
        elif kind is DealBreakerType.REMOTE_REQUIREMENT:
            if job_model is None:
                add(name, "deal_breaker", "unknown", "job_work_model_unknown")
            elif job_model is RemotePreference.ONSITE:
                add(name, "deal_breaker", "hard_block", "fully_onsite")
            else:
                add(name, "deal_breaker", "clear", "not_fully_onsite", work_model=job_model.value)
        elif kind is DealBreakerType.EMPLOYMENT_TYPE:
            blocked = _parse_employment_types(values.get(kind))
            if job_type is None:
                add(name, "deal_breaker", "unknown", "job_employment_type_unknown")
            elif blocked:
                if job_type in blocked:
                    add(name, "deal_breaker", "hard_block", "excluded_employment_type",
                        employment_type=job_type.value)
                else:
                    add(name, "deal_breaker", "clear", "employment_type_allowed")
            elif not prefs.employment_types:
                add(name, "deal_breaker", "unknown", "no_employment_types_selected")
            elif job_type not in prefs.employment_types:
                add(name, "deal_breaker", "hard_block", "employment_type_not_selected",
                    employment_type=job_type.value)
            else:
                add(name, "deal_breaker", "clear", "employment_type_selected")
        elif kind is DealBreakerType.LOCATION:
            if not ctx.candidate.location_and_mobility.preferred_locations:
                add(name, "deal_breaker", "unknown", "no_preferred_locations")
            elif ctx.remote_job:
                add(name, "deal_breaker", "clear", "remote_job")
            elif ctx.in_preferred_location is True:
                add(name, "deal_breaker", "clear", "in_preferred_location")
            elif ctx.in_preferred_location is False:
                add(name, "deal_breaker", "hard_block", "outside_preferred_locations")
            else:
                add(name, "deal_breaker", "unknown", "location_not_comparable")
        elif kind is DealBreakerType.RELOCATION:
            _relocation(ctx, add, name, "deal_breaker")
        elif kind is DealBreakerType.TRAVEL:
            if job_travel is None:
                add(name, "deal_breaker", "unknown", "travel_not_stated")
            elif job_travel is TravelWillingness.FREQUENT:
                add(name, "deal_breaker", "hard_block", "frequent_travel")
            else:
                add(name, "deal_breaker", "clear", "no_frequent_travel")

    if (
        ctx.candidate.location_and_mobility.relocation is RelocationPreference.NO
        and DealBreakerType.RELOCATION not in enabled
    ):
        _relocation(ctx, add, "relocation_preference", "preference")
    return results


def _relocation(ctx: Context, add, name: str, source: str) -> None:  # type: ignore[no-untyped-def]
    if ctx.needs_relocation is True:
        add(name, source, "hard_block", "relocation_required")
    elif ctx.needs_relocation is False:
        add(name, source, "clear", "no_relocation_needed")
    else:
        add(name, source, "unknown", "relocation_need_unknown")
