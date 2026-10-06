"""Work model normalization into JobPilot's vocabulary.

    "Remote", "100% Remote", "Home Office"  -> REMOTE
    "Hybrid", "Hybrid work"                 -> HYBRID
    "On-site", "Vor Ort"                    -> ONSITE

Only explicit work-model values, the title and the location text are read --
never the description, so a posting that mentions "flexible hours" is not
turned into a flexible or remote job. "On-site" is never inferred from a
location: a city name alone doesn't say the job isn't remote. Unknown values
stay unknown (None).
"""

from app.models.job_preferences import RemotePreference
from app.services.job_discovery.vocabulary import (
    WORK_MODEL_LOCATION_PATTERNS,
    WORK_MODEL_PATTERNS,
)


def normalize_work_model(
    value: str | None, *, location_texts: list[str], title: str | None
) -> RemotePreference | None:
    if value:
        for work_model, pattern in WORK_MODEL_PATTERNS:
            if pattern.search(value):
                return work_model
    for text in [title or "", *location_texts]:
        for work_model, pattern in WORK_MODEL_LOCATION_PATTERNS:
            if pattern.search(text):
                return work_model
    return None
