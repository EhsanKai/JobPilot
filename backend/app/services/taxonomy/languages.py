"""Language taxonomy and language-level representation (V0.5.1).

Languages are keyed by ISO 639-1 code -- the same `language_key` format the
V0.5.0 candidate snapshot already validates. Aliases are the English name,
the language's own name and the German name (German is the second most
common posting language in the real data), plus the code itself. Seeded
with the languages the 503 real postings mention.

Levels: CEFR A1-C2, `native`, `unknown`.

- `unknown` is a *value*: a language is stated but its level is not (the
  V0.5.0 snapshot default). Missing data is the absence of a language, or
  `None` from the normalizers -- never `unknown`.
- Only explicit level wording maps to a level ("C1", "B2-Niveau", "native
  speaker", "Muttersprache"). Descriptive words such as "fluent",
  "fließend" or "business level" stay UNKNOWN: there is no authoritative
  mapping to CEFR in this repository, and later phases decide it.
- CEFR's own order (`CEFR_ORDER`) is part of the representation. Where
  `native` stands relative to C2 is a matching decision, left open.
"""

import re
from typing import Literal, get_args

from app.services.taxonomy.model import (
    Alias,
    Category,
    Entry,
    NormalizationResult,
    Vocabulary,
)
from app.services.taxonomy.versions import TAXONOMY_VERSION, VOCABULARY_VERSIONS

_LANGUAGES: tuple[tuple[str, str, tuple[Alias | str, ...]], ...] = (
    ("de", "German", ("deutsch", "deutschkenntnisse")),
    ("en", "English", ("englisch", "englischkenntnisse", "engels")),
    ("fr", "French", ("français", "französisch")),
    ("nl", "Dutch", ("nederlands", "niederländisch")),
    ("es", "Spanish", ("español", "spanisch")),
    ("it", "Italian", ("italiano", "italienisch")),
    ("sv", "Swedish", ("svenska", "schwedisch")),
    ("da", "Danish", ("dansk", "dänisch")),
    ("no", "Norwegian", ("norsk", "norwegisch")),
    ("pt", "Portuguese", ("português", "portugiesisch")),
    ("ja", "Japanese", ("japanisch",)),
    ("el", "Greek", ("griechisch",)),
)

# ISO codes that are also ordinary words or common abbreviations.
_CODE_AMBIGUITY = {
    "it": "also 'IT' (information technology) and an English word",
    "no": "also an ordinary English word",
    "ja": "also an ordinary German word ('yes')",
}

LANGUAGES = Vocabulary(
    name="languages",
    version=VOCABULARY_VERSIONS["languages"],
    taxonomy_version=TAXONOMY_VERSION,
    categories=(Category("language", "Language"),),
    entries=tuple(
        Entry(
            code,
            name,
            "language",
            (*aliases, Alias(code, _CODE_AMBIGUITY.get(code))),
        )
        for code, name, aliases in _LANGUAGES
    ),
    key_pattern=re.compile(r"^[a-z]{2}$"),
)

LanguageLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2", "native", "unknown"]
LANGUAGE_LEVELS: tuple[LanguageLevel, ...] = get_args(LanguageLevel)
CEFR_ORDER: tuple[LanguageLevel, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")


def _cefr_aliases(level: str) -> tuple[str, ...]:
    return (
        f"{level} level",
        f"level {level}",
        f"{level} niveau",
        f"niveau {level}",
        f"cefr {level}",
    )


LANGUAGE_LEVEL_VOCABULARY = Vocabulary(
    name="language_levels",
    version=VOCABULARY_VERSIONS["language_levels"],
    taxonomy_version=TAXONOMY_VERSION,
    categories=(
        Category("cefr", "CEFR level"),
        Category("native", "Native"),
        Category("unknown", "Level not stated"),
    ),
    entries=(
        *(Entry(level, level, "cefr", _cefr_aliases(level)) for level in CEFR_ORDER),
        Entry(
            "native",
            "Native",
            "native",
            (
                "native speaker",
                "native level",
                "mother tongue",
                "muttersprache",
                "muttersprachlich",
                "muttersprachliche",
                "muttersprachler",
            ),
        ),
        Entry("unknown", "Unknown", "unknown"),
    ),
    key_pattern=re.compile(r"^(?:[ABC][12]|native|unknown)$"),
)


def normalize_language(text: str | None) -> NormalizationResult | None:
    """Normalize a language name or code. None for missing input."""
    if text is None or not text.strip():
        return None
    return LANGUAGES.lookup(text)


def normalize_language_level(text: str | None) -> NormalizationResult | None:
    """Normalize explicit level wording to a `LanguageLevel`. None for
    missing input; unmapped wording ("fluent") is UNKNOWN."""
    if text is None or not text.strip():
        return None
    return LANGUAGE_LEVEL_VOCABULARY.lookup(text)
