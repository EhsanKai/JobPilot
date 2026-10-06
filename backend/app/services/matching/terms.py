"""Finding V0.5.1 taxonomy terms inside posting prose.

The taxonomy normalizes *terms*; a posting is prose. This module is the
extractor's bridge between the two, and the only place that scans text for
taxonomy aliases. It adds no aliases of its own: every match is an alias of
a V0.5.1 vocabulary entry, compared in the same folded form (`fold`).

Context rules (language rules, not domain rules -- no profession, product
or vendor is named anywhere):

- Longest alias wins and matches don't overlap: "Lean Six Sigma" is one
  term, not also "Six Sigma"; "React Native" is not also "React".
- An alias that is ambiguous on its own (`Alias.ambiguity`, or a registered
  `AmbiguousTerm` such as "GCP" or a vendor name) never becomes a match. It
  is reported as an *ambiguous* hit, and only when the posting writes it as
  an acronym (all capitals), so ordinary words ("go", "lean", "swift") stay
  silent.
- Aliases of one or two characters ("JS", "BA") only count when written in
  capitals in the original text.
- A single-word alias used as a verb is skipped: "you excel at", "to excel
  in" (a subject or auxiliary before it, a preposition after it).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property

from app.services.job_discovery.normalization.text import fold
from app.services.taxonomy.model import Vocabulary

_VERB_SUBJECTS = frozenset(
    {"you", "we", "they", "i", "to", "will", "can", "ll", "must", "should", "would"}
)
_VERB_PREPOSITIONS = frozenset({"at", "in", "as", "on", "with"})


@dataclass(frozen=True)
class TermHit:
    """One taxonomy term found in a text (positions are in the folded text)."""

    #: The entry key; None for an ambiguous hit.
    key: str | None
    #: The folded alias that matched.
    alias: str
    start: int
    end: int
    #: For ambiguous hits: what else the term can mean, and the entries it
    #: may refer to.
    ambiguity: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return self.key is None


@dataclass(frozen=True)
class _Form:
    key: str | None
    ambiguity: str | None
    candidates: tuple[str, ...]


class TermFinder:
    """Scans folded text for the aliases of one vocabulary."""

    def __init__(
        self,
        vocabulary: Vocabulary,
        *,
        categories: Iterable[str] | None = None,
        min_alias_length: int = 1,
    ) -> None:
        self.vocabulary = vocabulary
        allowed = set(categories) if categories is not None else None
        forms: dict[str, _Form] = {}
        for key in vocabulary.keys:
            if allowed is not None and vocabulary.entry(key).category not in allowed:
                continue
            for alias in vocabulary.aliases(key):
                if len(alias) < min_alias_length:
                    continue
                entries = vocabulary.forms(alias, allowed)
                notes = sorted({f.ambiguity for f in entries if f.ambiguity})
                if len(entries) == 1 and not notes:
                    forms[alias] = _Form(key, None, ())
                else:
                    forms[alias] = _Form(
                        None,
                        "; ".join(notes) or "names several entries",
                        tuple(sorted({f.entry.key for f in entries})),
                    )
        for term in vocabulary.ambiguous_terms:
            folded = fold(term.text)
            if len(folded) >= min_alias_length:
                forms[folded] = _Form(None, term.reason, tuple(sorted(term.candidates)))
        self._forms = forms

    @cached_property
    def _pattern(self) -> re.Pattern[str]:
        # Longest first: the regex engine tries alternatives in order, so at
        # any position the longest alias wins.
        aliases = sorted(self._forms, key=lambda a: (-len(a), a))
        body = "|".join(re.escape(alias) for alias in aliases)
        return re.compile(rf"(?<![a-z0-9+#])(?:{body})(?![a-z0-9+#])")

    def find(self, raw: str) -> list[TermHit]:
        """Every term in `raw` (original text), in order of appearance."""
        folded = fold(raw)
        if not folded or not self._forms:
            return []
        hits: list[TermHit] = []
        for match in self._pattern.finditer(folded):
            alias = match.group(0)
            form = self._forms[alias]
            if (form.key is None or len(alias) <= 2) and not _written_in_capitals(alias, raw):
                continue
            if " " not in alias and _used_as_verb(folded, match.start(), match.end()):
                continue
            hits.append(
                TermHit(
                    key=form.key,
                    alias=alias,
                    start=match.start(),
                    end=match.end(),
                    ambiguity=form.ambiguity,
                    candidates=form.candidates,
                )
            )
        return hits


def _written_in_capitals(alias: str, raw: str) -> bool:
    """Does the original text write the alias as an acronym ("GCP", "JS")?"""
    if " " in alias:
        return False
    pattern = rf"(?<![A-Za-z0-9]){re.escape(alias.upper())}(?![A-Za-z0-9])"
    return re.search(pattern, raw) is not None


def _used_as_verb(folded: str, start: int, end: int) -> bool:
    before = folded[:start].split()
    after = folded[end:].split()
    return bool(before and after) and (
        before[-1] in _VERB_SUBJECTS and after[0] in _VERB_PREPOSITIONS
    )
