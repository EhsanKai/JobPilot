"""Generic taxonomy & deterministic normalization (V0.5.1).

Code-owned, versioned vocabularies built on one domain-neutral mechanism
(`model.Vocabulary`): exact alias matching on folded text, explicit
ambiguity, no fuzzy matching, AI, network or geocoding.

    model.py       Vocabulary, Entry, Alias, AmbiguousTerm, NormalizationResult
    versions.py    TAXONOMY_VERSION and per-vocabulary versions
    skills.py      skills (categories are data)
    languages.py   languages (ISO 639-1) and levels (CEFR, native, unknown)
    education.py   education levels
    titles.py      job titles (explicit aliases only)
    locations.py   cities, regions, countries, remote

Every normalizer returns a `NormalizationResult` (MATCH / UNKNOWN /
AMBIGUOUS), or None when there is nothing to normalize. See
docs/taxonomy.md.
"""

from app.services.taxonomy.education import EDUCATION_LEVEL_VOCABULARY, normalize_education_level
from app.services.taxonomy.languages import (
    LANGUAGE_LEVEL_VOCABULARY,
    LANGUAGES,
    normalize_language,
    normalize_language_level,
)
from app.services.taxonomy.locations import (
    LOCATIONS,
    normalize_country,
    normalize_location,
    normalize_location_list,
)
from app.services.taxonomy.model import (
    Candidate,
    NormalizationResult,
    NormalizationStatus,
    Vocabulary,
)
from app.services.taxonomy.skills import SKILLS, normalize_skill
from app.services.taxonomy.titles import JOB_TITLES, normalize_title
from app.services.taxonomy.versions import TAXONOMY_VERSION, VOCABULARY_VERSIONS

VOCABULARIES: tuple[Vocabulary, ...] = (
    SKILLS,
    LANGUAGES,
    LANGUAGE_LEVEL_VOCABULARY,
    EDUCATION_LEVEL_VOCABULARY,
    JOB_TITLES,
    LOCATIONS,
)

__all__ = [
    "TAXONOMY_VERSION",
    "VOCABULARY_VERSIONS",
    "VOCABULARIES",
    "Candidate",
    "NormalizationResult",
    "NormalizationStatus",
    "Vocabulary",
    "SKILLS",
    "LANGUAGES",
    "LANGUAGE_LEVEL_VOCABULARY",
    "EDUCATION_LEVEL_VOCABULARY",
    "JOB_TITLES",
    "LOCATIONS",
    "normalize_skill",
    "normalize_language",
    "normalize_language_level",
    "normalize_education_level",
    "normalize_title",
    "normalize_location",
    "normalize_location_list",
    "normalize_country",
]
