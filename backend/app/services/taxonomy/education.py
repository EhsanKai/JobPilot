"""Education-level taxonomy (V0.5.1).

Stable level keys with explicit aliases for degree *types* ("Bachelor's",
"B.Sc.", "MBA", "PhD", "Ausbildung", "high school diploma"). It never
guesses a level from a subject or an unspecific word: "degree",
"university degree" and "Studium" (completed studies, level unstated) are
UNKNOWN; "diploma" and the German "Diplom" are AMBIGUOUS.

Seeded from the real postings: US hourly roles ask for a high school
diploma/GED or an associate's degree, German roles for an "Ausbildung",
professional roles for bachelor's/master's degrees, a few for a PhD.
"""

from typing import Literal, get_args

from app.services.taxonomy.model import (
    Alias,
    AmbiguousTerm,
    Category,
    Entry,
    NormalizationResult,
    Vocabulary,
)
from app.services.taxonomy.versions import TAXONOMY_VERSION, VOCABULARY_VERSIONS

EducationLevel = Literal[
    "secondary_school",
    "vocational_training",
    "associate_degree",
    "bachelor",
    "master",
    "doctorate",
]
EDUCATION_LEVELS: tuple[EducationLevel, ...] = get_args(EducationLevel)

EDUCATION_LEVEL_VOCABULARY = Vocabulary(
    name="education_levels",
    version=VOCABULARY_VERSIONS["education_levels"],
    taxonomy_version=TAXONOMY_VERSION,
    categories=(Category("education_level", "Education level"),),
    entries=(
        Entry(
            "secondary_school",
            "Secondary school",
            "education_level",
            ("high school", "high school diploma", "high school graduate", "ged"),
        ),
        Entry(
            "vocational_training",
            "Vocational training",
            "education_level",
            (
                "vocational qualification",
                "apprenticeship",
                "ausbildung",
                "berufsausbildung",
                "abgeschlossene ausbildung",
            ),
        ),
        Entry(
            "associate_degree",
            "Associate degree",
            "education_level",
            ("associate's degree", "associates degree"),
        ),
        Entry(
            "bachelor",
            "Bachelor's degree",
            "education_level",
            (
                "bachelor",
                "bachelors",
                "bachelor's",
                "bachelor degree",
                "bachelors degree",
                "bachelorstudium",
                "bachelor of science",
                "bachelor of arts",
                "bachelor of engineering",
                "bsc",
                "b.sc.",
                "bs",
                "b.s.",
                "ba",
                "b.a.",
                "beng",
                "b.eng.",
            ),
        ),
        Entry(
            "master",
            "Master's degree",
            "education_level",
            (
                "master",
                "masters",
                "master's",
                "master degree",
                "masters degree",
                "master of science",
                "master of arts",
                "master of engineering",
                "master of business administration",
                "mba",
                "msc",
                "m.sc.",
                Alias("ms", "also an abbreviation of Microsoft (e.g. 'MS Excel')"),
                "m.s.",
                "ma",
                "m.a.",
                "meng",
                "m.eng.",
            ),
        ),
        Entry(
            "doctorate",
            "Doctorate",
            "education_level",
            ("doctoral degree", "doctor of philosophy", "phd", "ph.d.", "dphil"),
        ),
    ),
    ambiguous_terms=(
        AmbiguousTerm(
            "diploma",
            "level depends on the diploma (high school, postgraduate, German Diplom, ...)",
        ),
        AmbiguousTerm(
            "Diplom",
            "German Diplom: university or university-of-applied-sciences level",
            ("bachelor", "master"),
        ),
    ),
)


def normalize_education_level(text: str | None) -> NormalizationResult | None:
    """Normalize a degree or qualification type. None for missing input."""
    if text is None or not text.strip():
        return None
    return EDUCATION_LEVEL_VOCABULARY.lookup(text)
