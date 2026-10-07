"""Explaining a match: deterministic summary first, optional AI prose second.

1. `summarize(evidence)` turns the stored structured evidence into four
   lists -- strong matches, gaps, unknowns, deal breakers -- each item with a
   stable id ("skills:python", "salary", "deal_breaker:relocation"), its
   component, status and a short text. Pure templates, no AI. This is what
   the UI shows by default, and the frontend reads state from the
   structured fields, never from the text.

2. `generate_ai_explanation` (optional, on request) hands the AI *only*
   those facts plus the scores and recommendation, through the existing
   `AIProvider` abstraction, and validates the answer before anything is
   stored (`validate_ai_explanation`). The answer's schema has no score or
   recommendation field, and the validator rejects any text that

   - references a fact id that wasn't given (or a gap as a strength),
   - contains a number that isn't in the facts (a different percentage,
     invented years or salaries),
   - names a skill, language or degree that isn't in the facts,
   - names a skill the candidate lacks in a strength, or in a sentence
     that presents it as something the candidate has,
   - claims a different recommendation ("strong match" for a weak one).

   A rejected answer is never stored or shown; the deterministic
   explanation stands on its own.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from app.ai.prompts import load_prompt
from app.ai.provider import AIInvalidResponseError, AIProvider
from app.models.job_match import MatchVerdict
from app.schemas.job_match import AIMatchExplanation, ComponentResult, ItemResult, MatchEvidence
from app.services.job_discovery.normalization.text import fold
from app.services.matching.terms import TermFinder
from app.services.taxonomy import EDUCATION_LEVEL_VOCABULARY, LANGUAGES, SKILLS

#: Versioned like the resume parser prompt: a behavioural change gets a new
#: file and a new version; stored explanations record the version.
MATCH_EXPLANATION_PROMPT_VERSION = "matching_explanation_v1"

ExplanationKind = Literal["strong_match", "gap", "unknown", "deal_breaker"]

RECOMMENDATIONS: dict[MatchVerdict, tuple[str, str]] = {
    MatchVerdict.STRONG: ("STRONG_MATCH", "Strong match"),
    MatchVerdict.GOOD: ("GOOD_MATCH", "Good match"),
    MatchVerdict.PARTIAL: ("POTENTIAL_MATCH", "Potential match"),
    MatchVerdict.WEAK: ("WEAK_MATCH", "Weak match"),
    MatchVerdict.BLOCKED: ("NOT_RECOMMENDED", "Not recommended"),
    MatchVerdict.INSUFFICIENT_DATA: ("INSUFFICIENT_DATA", "Not enough data"),
}


class ExplanationItem(BaseModel):
    id: str
    kind: ExplanationKind
    component: str
    group: str
    status: str
    importance: str | None = None
    text: str


@dataclass(frozen=True)
class MatchSummary:
    strong_matches: list[ExplanationItem]
    gaps: list[ExplanationItem]
    unknowns: list[ExplanationItem]
    deal_breakers: list[ExplanationItem]

    def all_items(self) -> list[ExplanationItem]:
        return [*self.strong_matches, *self.gaps, *self.unknowns, *self.deal_breakers]


# --- Deterministic summary --------------------------------------------------------

_IMPORTANCE_SUFFIX = {
    "required": " (required)",
    "preferred": " (nice to have)",
    "unspecified": " (listed in the requirements)",
}
# Unknowns that only mean "you didn't state a preference" are not worth
# listing: the user doesn't care about that aspect.
_SILENT_REASONS = frozenset(
    {
        "no_work_model_preference",
        "no_employment_type_preference",
        "no_salary_preference",
        "no_desired_titles",
        "no_travel_preference",
        "alternative_satisfied",
        "mentioned_not_required",
        "no_language_requirements",
    }
)

_UNKNOWN_TEXT = {
    "no_skill_requirements": "The posting names no skills JobPilot recognizes",
    "candidate_skills_unknown": "Your skills aren't in your profile yet",
    "experience_not_stated": "Experience requirement not stated",
    "candidate_experience_unknown": "Your years of experience aren't set",
    "job_title_unknown": "Job title not available",
    "candidate_title_unknown": "Your current job title isn't set",
    "role_not_comparable": "Your role and this one can't be compared yet",
    "job_seniority_unknown": "Seniority not stated",
    "candidate_seniority_unknown": "Your seniority level isn't set",
    "job_industry_unknown": "Industry not stated",
    "candidate_industry_unknown": "Your industries aren't set",
    "candidate_languages_unknown": "Your languages aren't in your profile yet",
    "education_not_stated": "Education requirement not stated",
    "candidate_education_unknown": "Your education isn't in a confirmed resume yet",
    "candidate_location_unknown": "Your preferred locations aren't set",
    "candidate_location_unrecognized": "Your locations couldn't be recognized",
    "job_location_unknown": "Location not stated",
    "job_location_unrecognized": "Job location couldn't be recognized",
    "location_not_comparable": "Location can't be compared",
    "job_work_model_unknown": "Work model not stated",
    "salary_not_stated": "Salary not stated",
    "salary_currency_unknown": "Salary currency not stated",
    "salary_currency_differs": "Salary is in a different currency",
    "salary_period_not_comparable": "Salary isn't stated per year or month",
    "job_employment_type_unknown": "Employment type not stated",
    "desired_title_not_comparable": "Can't tell whether this is one of your desired roles",
    "travel_not_stated": "Travel not stated",
}

_DEAL_BREAKER_TEXT = {
    "minimum_salary": "Salary below your minimum (deal breaker)",
    "remote_requirement": "Fully on-site (deal breaker)",
    "employment_type": "An employment type you excluded (deal breaker)",
    "location": "Outside your preferred locations (deal breaker)",
    "relocation": "Requires relocation (deal breaker)",
    "relocation_preference": "Requires relocation, and you don't want to relocate",
    "travel": "Frequent travel (deal breaker)",
}
_DEAL_BREAKER_UNKNOWN_TEXT = {
    "minimum_salary": "Your minimum-salary deal breaker can't be checked",
    "remote_requirement": "Your on-site deal breaker can't be checked: work model not stated",
    "employment_type": "Your employment-type deal breaker can't be checked",
    "location": "Your location deal breaker can't be checked",
    "relocation": "Your relocation deal breaker can't be checked",
    "relocation_preference": "Can't tell whether this job needs relocation",
    "travel": "Your travel deal breaker can't be checked: travel not stated",
}


def _first(values: list[Any]) -> Any:
    return values[0].value if values else None


def _component_text(c: ComponentResult) -> str:
    cand, job = _first(c.candidate), _first(c.job)
    d = c.details
    r = c.reason_code
    if c.key == "experience":
        asked = d.get("required_years")
        have = d.get("candidate_years")
        return f"{have:g} years of experience ({asked:g}+ asked)" if isinstance(
            have, int | float
        ) and isinstance(asked, int | float) else "Experience"
    if c.key == "title":
        if r == "same_role":
            return f"Your current role: {cand}"
        if r == "previous_role":
            return f"You have worked as {cand}"
        return f"A different role from yours ({cand})"
    if c.key == "seniority":
        if r == "same_level":
            return f"{job} role, your level"
        if r == "overqualified":
            return f"{job} role: you are {cand} (overqualified)"
        return f"{job} role (you are {cand})"
    if c.key in ("industry", "preferred_industry"):
        if r == "industry_match":
            prefix = "Industry experience" if c.key == "industry" else "Preferred industry"
            return f"{prefix}: {job}"
        if c.key == "industry":
            return f"No experience in {job} listed"
        return f"{job} isn't one of your preferred industries"
    if c.key == "location":
        place = ", ".join(e.value for e in c.job) or "this location"
        texts = {
            "in_preferred_location": f"{place}: one of your preferred locations",
            "at_current_location": f"{place}: where you live",
            "remote_job": "Remote job",
            "remote_in_other_country": f"Remote, but only within {d.get('remote_countries')}",
            "other_city_same_country": f"{place}: another city than you prefer",
            "other_country": f"{place}: another country",
        }
        return texts.get(r, place)
    if c.key == "work_model":
        wanted = " / ".join(e.value for e in c.candidate)
        return f"Work model: {job}" if r == "work_model_match" else f"{job} (you prefer {wanted})"
    if c.key == "salary":
        texts = {
            "salary_meets_desired": f"Salary {job} meets your desired salary",
            "salary_meets_minimum": f"Salary {job} meets your minimum",
            "salary_below_minimum": f"Salary {job} is below your minimum",
        }
        return texts.get(r, f"Salary {job}")
    if c.key == "employment_type":
        if r == "employment_type_match":
            return f"Employment type: {job}"
        wanted = " / ".join(e.value for e in c.candidate)
        return f"{job} (you want {wanted})"
    if c.key == "desired_title":
        return f"One of your desired roles: {cand}" if r == "desired_title_match" else (
            "Not one of your desired roles"
        )
    if c.key == "travel":
        return f"Travel: {job}" if r == "travel_fits" else f"{job} (you prefer: {cand})"
    return c.key


def _item_text(c: ComponentResult, item: ItemResult) -> str:
    suffix = _IMPORTANCE_SUFFIX.get(item.importance or "", "")
    if item.reason_code == "skill_term_ambiguous":
        return f'"{item.label}" is mentioned, but the term is ambiguous'
    if item.reason_code == "candidate_skills_unknown":
        return f"{item.label}{suffix}: can't compare, your profile lists no skills yet"
    if item.reason_code == "candidate_languages_unknown":
        return f"{item.label}{suffix}: can't compare, your profile lists no languages yet"
    if item.reason_code == "candidate_education_unknown":
        return f"{item.label}{suffix}: can't compare, no education in a confirmed resume yet"
    if item.group and item.status == "missing":
        return f"{item.group} (one of them){suffix}"
    if c.key == "skills":
        if item.reason_code == "fewer_years_than_asked":
            return f"{item.label}: fewer years than the posting asks"
        return item.label if item.status == "match" else f"{item.label}{suffix}"
    if c.key == "languages":
        name = LANGUAGES.entry(item.key).label if LANGUAGES.has_key(item.key) else item.label
        have = item.candidate.value if item.candidate else None
        if have and have.startswith(name):
            have = have[len(name) :].strip() or None
        if item.reason_code == "language_level_not_compared":
            return f"{item.label}: you speak it; your level isn't stated as a CEFR level"
        if item.reason_code == "language_level_below":
            return f"{item.label} asked (you: {have})"
        if item.status == "missing":
            return f"{item.label}{suffix}"
        return f"{item.label} (you: {have})" if have else item.label
    if c.key == "education":
        if item.reason_code == "education_meets":
            have = item.candidate.value if item.candidate else ""
            return f"{have} meets the {item.label} requirement".strip()
        if item.reason_code == "education_below_equivalent_accepted":
            return f"{item.label} or equivalent experience"
        return f"{item.label}{suffix}"
    return item.label


def summarize(evidence: MatchEvidence) -> MatchSummary:
    strong: list[ExplanationItem] = []
    gaps: list[ExplanationItem] = []
    unknowns: list[ExplanationItem] = []
    blocks: list[ExplanationItem] = []

    def add(target: list[ExplanationItem], kind: ExplanationKind, item_id: str,
            c: ComponentResult, status: str, text: str, importance: str | None = None) -> None:
        target.append(
            ExplanationItem(
                id=item_id, kind=kind, component=c.key, group=c.group, status=status,
                importance=importance, text=text,
            )
        )

    for c in evidence.components:
        scored_items = [i for i in c.items if i.importance != "contextual"]
        if c.items:
            for item in scored_items:
                item_id = f"{c.key}:{item.key}"
                if item.reason_code in _SILENT_REASONS:
                    continue
                text = _item_text(c, item)
                if item.status == "match":
                    add(strong, "strong_match", item_id, c, item.status, text, item.importance)
                elif item.status in ("missing", "partial"):
                    add(gaps, "gap", item_id, c, item.status, text, item.importance)
                elif item.status == "unknown":
                    add(unknowns, "unknown", item_id, c, item.status, text, item.importance)
            if c.status == "unknown" and not scored_items and c.reason_code not in _SILENT_REASONS:
                add(unknowns, "unknown", c.key, c, c.status,
                    _UNKNOWN_TEXT.get(c.reason_code, c.reason_code))
            continue
        if c.status == "unknown":
            # Preference unknowns only matter when the user stated a preference.
            if c.reason_code in _SILENT_REASONS or (c.group == "preference" and not c.candidate):
                continue
            add(unknowns, "unknown", c.key, c, c.status,
                _UNKNOWN_TEXT.get(c.reason_code, c.reason_code))
        elif c.status == "match":
            add(strong, "strong_match", c.key, c, c.status, _component_text(c))
        else:
            add(gaps, "gap", c.key, c, c.status, _component_text(c))

    for d in evidence.deal_breakers:
        item_id = f"deal_breaker:{d.type}"
        if d.status == "hard_block":
            blocks.append(
                ExplanationItem(
                    id=item_id, kind="deal_breaker", component="deal_breakers",
                    group="preference", status=d.status,
                    text=_DEAL_BREAKER_TEXT.get(d.type, d.type),
                )
            )
        elif d.status == "unknown":
            unknowns.append(
                ExplanationItem(
                    id=item_id, kind="unknown", component="deal_breakers",
                    group="preference", status=d.status,
                    text=_DEAL_BREAKER_UNKNOWN_TEXT.get(d.type, d.type),
                )
            )
    return MatchSummary(strong_matches=strong, gaps=gaps, unknowns=unknowns, deal_breakers=blocks)


# --- AI explanation -----------------------------------------------------------------


class ExplanationRejectedError(AIInvalidResponseError):
    """The AI's explanation contradicted or went beyond the facts."""

    code = "AI_EXPLANATION_REJECTED"
    user_message = (
        "The AI explanation didn't match JobPilot's verified match facts, so it was discarded. "
        "The explanation below is based on your profile and the posting only."
    )

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def ai_facts(
    *,
    job_title: str,
    company_name: str,
    job_match: int | None,
    preference_match: int | None,
    overall_match: int | None,
    verdict: MatchVerdict,
    summary: MatchSummary,
) -> dict[str, Any]:
    """Everything the AI may know: the deterministic result, nothing else."""

    def facts(items: list[ExplanationItem]) -> list[dict[str, str]]:
        return [{"id": i.id, "text": i.text} for i in items]

    return {
        "job": {"title": job_title, "company": company_name},
        "scores": {
            "job_match": job_match,
            "preference_match": preference_match,
            "overall_match": overall_match,
        },
        "recommendation": RECOMMENDATIONS[verdict][0],
        "strong_matches": facts(summary.strong_matches),
        "gaps": facts(summary.gaps),
        "unknowns": facts(summary.unknowns),
        "deal_breakers": facts(summary.deal_breakers),
    }


def build_user_prompt(facts: dict[str, Any]) -> str:
    return (
        "Explain this job match to the candidate using ONLY the facts between the "
        "<match_facts> tags.\n\n<match_facts>\n"
        f"{json.dumps(facts, ensure_ascii=False, indent=2)}\n"
        "</match_facts>"
    )


_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d[\d,.']*")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_NEGATION = re.compile(
    r"\b(not|no|lack\w*|missing|without|gap|gaps|don t|doesn t|isn t|aren t|but|however|"
    r"although|yet|unlike|would need|could learn|to learn|develop)\b"
)
_RECOMMENDATION_CLAIMS: dict[str, str] = {
    "strong match": "STRONG_MATCH",
    "excellent match": "STRONG_MATCH",
    "perfect match": "STRONG_MATCH",
    "great match": "STRONG_MATCH",
    "good match": "GOOD_MATCH",
    "potential match": "POTENTIAL_MATCH",
    "weak match": "WEAK_MATCH",
    "poor match": "WEAK_MATCH",
    "not recommended": "NOT_RECOMMENDED",
}
_FINDERS = (
    TermFinder(SKILLS),
    TermFinder(LANGUAGES, min_alias_length=3),
    TermFinder(EDUCATION_LEVEL_VOCABULARY),
)


def _numbers(text: str) -> set[str]:
    found = set()
    for match in _NUMBER.finditer(text):
        value = match.group(0).rstrip(".,'").replace(",", "").replace("'", "")
        if value.count(".") <= 1 and value:
            found.add(value.rstrip("0").rstrip(".") if "." in value else value)
    return found


def _terms(text: str) -> set[str]:
    return {
        f"{finder.vocabulary.name}:{hit.key}"
        for finder in _FINDERS
        for hit in finder.find(text)
        if hit.key is not None
    }


def validate_ai_explanation(explanation: AIMatchExplanation, facts: dict[str, Any]) -> None:
    """Raise `ExplanationRejectedError` unless the AI text stays within the
    facts (see module docs)."""
    strong_ids = {f["id"] for f in facts["strong_matches"]}
    concern_ids = {f["id"] for group in ("gaps", "unknowns", "deal_breakers") for f in facts[group]}
    for point in explanation.strengths:
        if point.fact_id not in strong_ids:
            raise ExplanationRejectedError(f"strength references unknown fact {point.fact_id!r}")
    for point in explanation.concerns:
        if point.fact_id not in concern_ids:
            raise ExplanationRejectedError(f"concern references unknown fact {point.fact_id!r}")

    fact_texts = [f["text"] for group in ("strong_matches", "gaps", "unknowns", "deal_breakers")
                  for f in facts[group]]
    strong_texts = [f["text"] for f in facts["strong_matches"]]
    job = facts["job"]
    allowed_numbers = _numbers(" ".join([*fact_texts, job["title"], job["company"]])) | {
        str(v) for v in facts["scores"].values() if v is not None
    }
    allowed_terms = _terms(" ".join([*fact_texts, facts["job"]["title"]]))
    strong_terms = _terms(" ".join([*strong_texts, facts["job"]["title"]]))
    lacking = allowed_terms - strong_terms

    texts = [explanation.summary, *(p.text for p in explanation.strengths),
             *(p.text for p in explanation.concerns)]
    for text in texts:
        if extra := _numbers(text) - allowed_numbers:
            raise ExplanationRejectedError(f"numbers not in the facts: {sorted(extra)}")
        if extra_terms := _terms(text) - allowed_terms:
            raise ExplanationRejectedError(f"terms not in the facts: {sorted(extra_terms)}")
        folded = fold(text)
        for phrase, claim in _RECOMMENDATION_CLAIMS.items():
            if re.search(rf"\b{phrase}\b", folded) and claim != facts["recommendation"]:
                negated = re.search(rf"\bnot (?:a |an )?{phrase}\b", folded)
                if not negated:
                    raise ExplanationRejectedError(f"claims {claim}")
    for point in explanation.strengths:
        if wrong := _terms(point.text) & lacking:
            raise ExplanationRejectedError(f"strength names what the candidate lacks: {wrong}")
    for sentence in _SENTENCES.split(explanation.summary):
        if _terms(sentence) & lacking and not _NEGATION.search(fold(sentence)):
            raise ExplanationRejectedError("summary presents a gap as something the candidate has")


async def generate_ai_explanation(
    provider: AIProvider, facts: dict[str, Any]
) -> AIMatchExplanation:
    """Ask the AI to explain the facts, and validate its answer."""
    result = await provider.generate_structured(
        system=load_prompt(MATCH_EXPLANATION_PROMPT_VERSION),
        prompt=build_user_prompt(facts),
        schema=AIMatchExplanation,
    )
    validate_ai_explanation(result, facts)
    return result
