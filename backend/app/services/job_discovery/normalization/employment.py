"""Employment type normalization into JobPilot's vocabulary.

    "full-time", "Full Time", "FULL_TIME", "Vollzeit"  -> FULL_TIME
    "Werkstudent", "Working Student"                   -> WORKING_STUDENT
    "Teilzeit", "part_time"                            -> PART_TIME

Patterns live in `vocabulary.py` (first match wins). Values outside the
vocabulary stay unknown (None) -- no new types are invented. From a job
*title*, only unambiguous signals are used ("Contract Manager" is not a
contract job).
"""

from app.models.job_preferences import EmploymentTypeValue
from app.services.job_discovery.vocabulary import (
    EMPLOYMENT_TYPE_PATTERNS,
    EMPLOYMENT_TYPE_TITLE_PATTERNS,
)


def normalize_employment_type(
    value: str | None, *, title: str | None
) -> EmploymentTypeValue | None:
    if value:
        for employment_type, pattern in EMPLOYMENT_TYPE_PATTERNS:
            if pattern.search(value):
                return employment_type
    if title:
        for employment_type, pattern in EMPLOYMENT_TYPE_TITLE_PATTERNS:
            if pattern.search(title):
                return employment_type
    return None
