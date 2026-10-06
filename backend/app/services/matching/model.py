"""The `matching_v1` scoring model: every weight, threshold and matrix.

This module is pure data. The engine (`engine.py`, `components.py`) reads
it; nothing else defines a number that influences a score. The whole
definition is hashed (`MODEL_HASH`, stored on every `job_matches` row as
`model_hash`), and `tests/test_matching_scores.py` pins that hash: changing
any value without bumping `MATCHING_ALGORITHM_VERSION` fails the test, so an
old result is never mistaken for one the current definition produced.

The model is deliberately simple and **uncalibrated**: the weights encode
product judgement, not empirical validation. See docs/job-matching.md.
"""

import hashlib
import json
from typing import Any

from app.models.job_match import MatchVerdict
from app.models.job_preferences import RelocationPreference, RemotePreference, TravelWillingness
from app.models.professional_profile import SeniorityLevel
from app.services.taxonomy.education import EDUCATION_LEVELS

MATCHING_ALGORITHM_VERSION = "matching_v1"

# --- Component weights ------------------------------------------------------------
# Relative weights inside each sub-score. A component whose status is
# UNKNOWN is left out of both numerator and denominator, so missing data
# never counts as a mismatch (see `coverage`).

#: Job Match -- "can this candidate realistically perform this job?"
JOB_WEIGHTS: dict[str, int] = {
    "skills": 30,
    "experience": 20,
    "title": 10,
    "seniority": 10,
    "industry": 10,
    "languages": 5,
    "education": 5,
}

#: Preference Match -- "does this job fit what the candidate wants?"
PREFERENCE_WEIGHTS: dict[str, int] = {
    "location": 25,
    "work_model": 20,
    "salary": 20,
    "employment_type": 15,
    "desired_title": 10,
    "preferred_industry": 5,
    "travel": 5,
}

#: Overall Match = 70% Job Match + 30% Preference Match (renormalized when
#: one side is entirely unknown; never computed without a Job Match).
OVERALL_WEIGHTS: dict[str, float] = {"job": 0.7, "preference": 0.3}

# --- Requirements -----------------------------------------------------------------

#: How much one requirement counts inside its component, by how explicitly
#: the posting asked for it. CONTEXTUAL mentions (the team's stack, a
#: responsibility) are shown but never scored.
IMPORTANCE_WEIGHTS: dict[str, float] = {"required": 1.0, "unspecified": 0.7, "preferred": 0.35}

# --- Recommendation & caps --------------------------------------------------------

#: Below this share of the Job Match weight, too little is known about the
#: candidate or the job to recommend anything: INSUFFICIENT_DATA.
MIN_JOB_COVERAGE = 0.3

#: An explicit deal breaker (or "relocation: no" against a job that needs
#: relocation) caps the Overall Match here and makes it NOT_RECOMMENDED.
HARD_BLOCK_CAP = 40
#: Missing a qualification the posting explicitly marks as *required*
#: caps the Overall Match here: at most a POTENTIAL_MATCH.
REQUIRED_MISSING_CAP = 69

#: Overall Match thresholds for the recommendation (first match wins).
RECOMMENDATION_THRESHOLDS: tuple[tuple[int, MatchVerdict], ...] = (
    (80, MatchVerdict.STRONG),
    (70, MatchVerdict.GOOD),
    (60, MatchVerdict.PARTIAL),
    (0, MatchVerdict.WEAK),
)

#: Display bands for any 0-100 score (key, label, lower bound). The UI shows
#: the label the API returns; it never re-derives a band.
SCORE_BANDS: tuple[tuple[str, str, int], ...] = (
    ("excellent", "Excellent", 90),
    ("strong", "Strong", 80),
    ("good", "Good", 70),
    ("potential", "Potential", 60),
    ("weak", "Weak", 0),
)

# --- Component scoring tables -----------------------------------------------------

#: Component score -> status: >= MATCH_AT is a match, below MISMATCH_BELOW a
#: mismatch (a significant gap), anything between partial.
MATCH_AT = 0.8
MISMATCH_BELOW = 0.3

# Skills: a skill the candidate has with fewer years than the posting asks
# for (both stated) is a partial match.
SKILL_PARTIAL_YEARS_SCORE = 0.6

# Experience: candidate years vs. the posting's minimum years.
EXPERIENCE_SCORES: dict[str, float] = {
    "meets": 1.0,  # candidate >= required
    "within_one_year": 0.75,  # required - 1 <= candidate < required
    "at_least_half": 0.4,  # required / 2 <= candidate < required - 1
    "below_half": 0.0,  # candidate < required / 2: a gap
}

# Seniority compatibility matrix: SENIORITY_MATRIX[candidate][job].
# Rows are what the candidate is, columns what the job asks for. A level
# above the job is fine (0.9), far above is "overqualified" (partial); one
# level below is a stretch, two or more a significant gap.
_SENIORITY_ORDER = (
    SeniorityLevel.INTERN,
    SeniorityLevel.JUNIOR,
    SeniorityLevel.MID,
    SeniorityLevel.SENIOR,
    SeniorityLevel.LEAD,
    SeniorityLevel.MANAGER,
    SeniorityLevel.DIRECTOR,
)
_SENIORITY_ROWS: tuple[tuple[float, ...], ...] = (
    #  intern junior mid  senior lead  manager director   <- job
    (1.0, 0.6, 0.2, 0.0, 0.0, 0.0, 0.0),  # intern
    (0.8, 1.0, 0.6, 0.2, 0.0, 0.0, 0.0),  # junior
    (0.5, 0.8, 1.0, 0.6, 0.3, 0.2, 0.0),  # mid
    (0.3, 0.5, 0.9, 1.0, 0.7, 0.5, 0.2),  # senior
    (0.2, 0.4, 0.7, 0.9, 1.0, 0.7, 0.4),  # lead
    (0.2, 0.3, 0.6, 0.8, 0.8, 1.0, 0.6),  # manager
    (0.1, 0.2, 0.4, 0.6, 0.7, 0.9, 1.0),  # director
)
SENIORITY_MATRIX: dict[SeniorityLevel, dict[SeniorityLevel, float]] = {
    candidate: dict(zip(_SENIORITY_ORDER, row, strict=True))
    for candidate, row in zip(_SENIORITY_ORDER, _SENIORITY_ROWS, strict=True)
}
SENIORITY_RANK: dict[SeniorityLevel, int] = {
    level: index for index, level in enumerate(_SENIORITY_ORDER)
}

# Title / role (taxonomy keys; see components.title).
TITLE_SCORES: dict[str, float] = {
    "strong_match": 1.0,  # current title is the job's role
    "related_role": 0.6,  # held the job's role before (confirmed resume history)
    "weak_match": 0.0,  # a different, known role
}
DESIRED_TITLE_SCORES: dict[str, float] = {"primary": 1.0, "secondary": 0.9, "other": 0.0}

# Industry. JobPilot has no industry taxonomy yet: industries are compared
# by folded name, so different wording can hide a real match. A non-match
# therefore never scores zero.
INDUSTRY_SCORES: dict[str, float] = {"match": 1.0, "no_match": 0.3}

# Languages.
LANGUAGE_SCORES: dict[str, float] = {
    "meets": 1.0,
    "level_not_comparable": 1.0,  # spoken, level not stated on one side: not penalized
    "below_level": 0.4,
    "missing": 0.0,
}

# Education: ordered levels (the V0.5.1 taxonomy keys). Vocational
# training and an associate degree are different systems; they are ordered
# here only so a requirement can be compared at all.
EDUCATION_ORDER: tuple[str, ...] = EDUCATION_LEVELS
EDUCATION_SCORES: dict[str, float] = {
    "meets": 1.0,
    "below_or_equivalent_accepted": 0.5,  # "... or equivalent experience"
    "below": 0.0,
}

# Location: by relation to the candidate's places and their relocation answer.
LOCATION_SCORES: dict[str, float] = {
    "preferred_location": 1.0,
    "current_location": 0.9,
    "remote": 1.0,
    "remote_other_country": 0.5,
}
#: Job in another city of a country the candidate prefers or lives in.
LOCATION_SAME_COUNTRY: dict[str, float] = {
    RelocationPreference.YES.value: 0.7,
    RelocationPreference.MAYBE.value: 0.5,
    "unstated": 0.4,
    RelocationPreference.NO.value: 0.0,
}
#: Job in another country.
LOCATION_OTHER_COUNTRY: dict[str, float] = {
    RelocationPreference.YES.value: 0.5,
    RelocationPreference.MAYBE.value: 0.3,
    "unstated": 0.2,
    RelocationPreference.NO.value: 0.0,
}

# Work model: WORK_MODEL_SCORES[job][accepted by the candidate]; the best
# accepted option counts. A model the candidate selected scores 1.0;
# FLEXIBLE on either side is compatible with everything.
WORK_MODEL_SCORES: dict[RemotePreference, dict[RemotePreference, float]] = {
    RemotePreference.HYBRID: {RemotePreference.REMOTE: 0.5, RemotePreference.ONSITE: 0.7},
    RemotePreference.REMOTE: {RemotePreference.ONSITE: 0.3, RemotePreference.HYBRID: 0.6},
    RemotePreference.ONSITE: {RemotePreference.HYBRID: 0.3, RemotePreference.REMOTE: 0.0},
}
#: Job models that need the candidate physically present (relocation applies).
PRESENCE_WORK_MODELS: frozenset[RemotePreference] = frozenset(
    {RemotePreference.ONSITE, RemotePreference.HYBRID}
)

# Salary (annual, same currency only; monthly figures x12, hourly/daily/
# weekly never converted).
SALARY_SCORES: dict[str, float] = {
    "meets_desired": 1.0,
    "meets_minimum": 0.8,
    "below_minimum": 0.0,
}

# Travel: TRAVEL_SCORES[willingness][job travel].
TRAVEL_SCORES: dict[TravelWillingness, dict[TravelWillingness, float]] = {
    TravelWillingness.NONE: {
        TravelWillingness.NONE: 1.0,
        TravelWillingness.OCCASIONAL: 0.4,
        TravelWillingness.FREQUENT: 0.0,
    },
    TravelWillingness.OCCASIONAL: {
        TravelWillingness.NONE: 1.0,
        TravelWillingness.OCCASIONAL: 1.0,
        TravelWillingness.FREQUENT: 0.2,
    },
    TravelWillingness.FREQUENT: {
        TravelWillingness.NONE: 1.0,
        TravelWillingness.OCCASIONAL: 1.0,
        TravelWillingness.FREQUENT: 1.0,
    },
    TravelWillingness.FLEXIBLE: {
        TravelWillingness.NONE: 1.0,
        TravelWillingness.OCCASIONAL: 1.0,
        TravelWillingness.FREQUENT: 1.0,
    },
}


def band(score: int | None) -> tuple[str, str] | None:
    """(key, label) of the display band a 0-100 score falls into."""
    if score is None:
        return None
    for key, label, lower in SCORE_BANDS:
        if score >= lower:
            return key, label
    return SCORE_BANDS[-1][0], SCORE_BANDS[-1][1]


def model_definition() -> dict[str, Any]:
    """Everything that determines a score, as plain JSON-able data."""

    def enum_table(table: dict[Any, dict[Any, float]]) -> dict[str, dict[str, float]]:
        return {
            row.value: {column.value: value for column, value in columns.items()}
            for row, columns in table.items()
        }

    return {
        "version": MATCHING_ALGORITHM_VERSION,
        "job_weights": JOB_WEIGHTS,
        "preference_weights": PREFERENCE_WEIGHTS,
        "overall_weights": OVERALL_WEIGHTS,
        "importance_weights": IMPORTANCE_WEIGHTS,
        "min_job_coverage": MIN_JOB_COVERAGE,
        "hard_block_cap": HARD_BLOCK_CAP,
        "required_missing_cap": REQUIRED_MISSING_CAP,
        "recommendation_thresholds": [[t, v.value] for t, v in RECOMMENDATION_THRESHOLDS],
        "score_bands": [list(b) for b in SCORE_BANDS],
        "match_at": MATCH_AT,
        "mismatch_below": MISMATCH_BELOW,
        "skill_partial_years_score": SKILL_PARTIAL_YEARS_SCORE,
        "experience_scores": EXPERIENCE_SCORES,
        "seniority_matrix": enum_table(SENIORITY_MATRIX),
        "title_scores": TITLE_SCORES,
        "desired_title_scores": DESIRED_TITLE_SCORES,
        "industry_scores": INDUSTRY_SCORES,
        "language_scores": LANGUAGE_SCORES,
        "education_order": list(EDUCATION_ORDER),
        "education_scores": EDUCATION_SCORES,
        "location_scores": LOCATION_SCORES,
        "location_same_country": LOCATION_SAME_COUNTRY,
        "location_other_country": LOCATION_OTHER_COUNTRY,
        "work_model_scores": enum_table(WORK_MODEL_SCORES),
        "presence_work_models": sorted(m.value for m in PRESENCE_WORK_MODELS),
        "salary_scores": SALARY_SCORES,
        "travel_scores": enum_table(TRAVEL_SCORES),
    }


def _hash(definition: dict[str, Any]) -> str:
    encoded = json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


MODEL_HASH = _hash(model_definition())
