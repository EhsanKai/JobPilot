"""The generic vocabulary mechanism behind every V0.5.1 taxonomy.

A `Vocabulary` is plain data -- categories, entries with aliases, and terms
known to be ambiguous -- plus one deterministic lookup. It knows nothing
about any profession, industry or product: a skill such as "HACCP", "Python"
or "IFRS" is just an entry. Adding entries or categories is a data change,
never a code change.

Lookup compares *folded* text (`fold`: case, diacritics, punctuation and
whitespace differences removed -- the same folding V0.4 uses for
comparison keys) against folded aliases. It is exact alias matching:

- no fuzzy or semantic similarity, no AI, no network;
- an alias that is also something else (``Alias.ambiguity``) never matches
  on its own -- the result is AMBIGUOUS with the entry as a candidate;
- a term registered as ambiguous (``AmbiguousTerm``) is AMBIGUOUS;
- anything else unknown is UNKNOWN, with the original text preserved.
"""

import enum
import hashlib
import json
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field

from app.services.job_discovery.normalization.text import fold

SNAKE_KEY = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class NormalizationStatus(str, enum.Enum):
    MATCH = "match"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class Alias:
    """One way of writing an entry.

    `ambiguity`, when set, explains what else the alias can mean (e.g. "CPA"
    is also "cost per acquisition"). Such an alias needs context: on its own
    it yields AMBIGUOUS, never a match.
    """

    text: str
    ambiguity: str | None = None


@dataclass(frozen=True)
class Category:
    key: str
    label: str


@dataclass(frozen=True)
class Entry:
    """A canonical concept. Its `label` is always one of its aliases."""

    key: str
    label: str
    category: str
    aliases: tuple[Alias | str, ...] = ()
    #: Vocabulary-specific facts, e.g. the country of a city.
    attributes: tuple[tuple[str, str], ...] = ()

    def attribute(self, name: str) -> str | None:
        return dict(self.attributes).get(name)


@dataclass(frozen=True)
class AmbiguousTerm:
    """A term that names several things and must never be resolved alone,
    e.g. a vendor name covering many distinct products."""

    text: str
    reason: str
    #: Entry keys it may refer to, when they are known and in the vocabulary.
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    key: str
    label: str


@dataclass(frozen=True)
class NormalizationResult:
    """The outcome of normalizing one input. The same shape for every
    vocabulary.

    - MATCH: `key`, `label`, `category`, `matched_alias` are set.
    - UNKNOWN: only `input` (preserved as given) and a `reason`.
    - AMBIGUOUS: `input`, `reason` and the `candidates` that are known; no
      key is chosen.
    """

    vocabulary: str
    vocabulary_version: str
    taxonomy_version: str
    status: NormalizationStatus
    input: str
    key: str | None = None
    label: str | None = None
    category: str | None = None
    #: The folded alias that matched.
    matched_alias: str | None = None
    candidates: tuple[Candidate, ...] = ()
    #: Machine-readable cause for UNKNOWN/AMBIGUOUS: "not_in_vocabulary",
    #: "ambiguous_term", "alias_needs_context", "several_entries", ...
    reason: str | None = None
    #: Extras: the human explanation of an ambiguity ("ambiguity"), or
    #: vocabulary-specific facts such as a title's stripped seniority words.
    details: tuple[tuple[str, str], ...] = ()

    @property
    def is_match(self) -> bool:
        return self.status is NormalizationStatus.MATCH

    def detail(self, name: str) -> str | None:
        return dict(self.details).get(name)


class VocabularyError(ValueError):
    """Raised when vocabulary data is inconsistent (at import time)."""


@dataclass(frozen=True)
class AliasForm:
    """One alias of one entry, as indexed: the entry and the alias's ambiguity."""

    entry: Entry
    ambiguity: str | None


@dataclass
class Vocabulary:
    name: str
    version: str
    taxonomy_version: str
    categories: tuple[Category, ...]
    entries: tuple[Entry, ...]
    ambiguous_terms: tuple[AmbiguousTerm, ...] = ()
    key_pattern: re.Pattern[str] = SNAKE_KEY
    #: Allow one alias in several categories (e.g. "Berlin" the city and the
    #: state). Within one category a shared alias must always be ambiguous.
    allow_cross_category_aliases: bool = False

    _by_key: dict[str, Entry] = field(init=False, repr=False)
    _forms: dict[str, list[AliasForm]] = field(init=False, repr=False)
    _ambiguous: dict[str, AmbiguousTerm] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.name or not self.version or not self.taxonomy_version:
            raise VocabularyError("a vocabulary needs a name, a version and a taxonomy version")
        category_keys = [c.key for c in self.categories]
        _require_unique(category_keys, f"{self.name}: duplicate category")
        for key in category_keys:
            if not SNAKE_KEY.match(key):
                raise VocabularyError(f"{self.name}: invalid category key {key!r}")

        self._by_key = {}
        self._forms = {}
        for entry in self.entries:
            if not self.key_pattern.match(entry.key):
                raise VocabularyError(f"{self.name}: invalid key {entry.key!r}")
            if entry.key in self._by_key:
                raise VocabularyError(f"{self.name}: duplicate key {entry.key!r}")
            if entry.category not in category_keys:
                raise VocabularyError(
                    f"{self.name}: {entry.key!r} has unregistered category {entry.category!r}"
                )
            self._by_key[entry.key] = entry
            for folded, ambiguity in _entry_forms(entry).items():
                self._forms.setdefault(folded, []).append(AliasForm(entry, ambiguity))

        for folded, forms in self._forms.items():
            if len(forms) < 2:
                continue
            same_category = len({f.entry.category for f in forms}) < len(forms)
            if (same_category or not self.allow_cross_category_aliases) and any(
                f.ambiguity is None for f in forms
            ):
                keys = sorted(f.entry.key for f in forms)
                raise VocabularyError(
                    f"{self.name}: alias {folded!r} is shared by {keys} but not declared ambiguous"
                )

        self._ambiguous = {}
        for term in self.ambiguous_terms:
            folded = _fold_required(term.text, self.name)
            if folded in self._forms or folded in self._ambiguous:
                raise VocabularyError(f"{self.name}: ambiguous term {folded!r} is also an alias")
            for key in term.candidates:
                if key not in self._by_key:
                    raise VocabularyError(f"{self.name}: unknown candidate {key!r}")
            self._ambiguous[folded] = term

    # --- Queries ------------------------------------------------------------------

    def entry(self, key: str) -> Entry:
        return self._by_key[key]

    def has_key(self, key: str) -> bool:
        return key in self._by_key

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._by_key)

    def aliases(self, key: str) -> tuple[str, ...]:
        """The folded aliases of an entry (its label included)."""
        return tuple(_entry_forms(self._by_key[key]))

    def forms(self, folded: str, categories: Collection[str] | None = None) -> list[AliasForm]:
        """Every (entry, ambiguity) whose alias folds to `folded`."""
        return [
            form
            for form in self._forms.get(folded, [])
            if categories is None or form.entry.category in categories
        ]

    def lookup(
        self, text: str, *, categories: Collection[str] | None = None
    ) -> NormalizationResult:
        """Normalize one term (see module docs)."""
        folded = fold(text)
        if not folded:
            return self.unknown(text, "no_comparable_text")
        if (term := self._ambiguous.get(folded)) is not None:
            return self.ambiguous(
                text,
                [self._by_key[k] for k in term.candidates],
                "ambiguous_term",
                {"ambiguity": term.reason},
            )
        forms = self.forms(folded, categories)
        if not forms:
            return self.unknown(text, "not_in_vocabulary")
        if len(forms) == 1 and forms[0].ambiguity is None:
            return self.match(text, forms[0].entry, folded)
        notes = sorted({f.ambiguity for f in forms if f.ambiguity})
        return self.ambiguous(
            text,
            [f.entry for f in forms],
            "alias_needs_context" if notes else "several_entries",
            {"ambiguity": "; ".join(notes)} if notes else None,
        )

    # --- Results ------------------------------------------------------------------

    def match(
        self,
        text: str,
        entry: Entry,
        alias: str | None,
        details: Mapping[str, str] | None = None,
    ) -> NormalizationResult:
        return NormalizationResult(
            vocabulary=self.name,
            vocabulary_version=self.version,
            taxonomy_version=self.taxonomy_version,
            status=NormalizationStatus.MATCH,
            input=text,
            key=entry.key,
            label=entry.label,
            category=entry.category,
            matched_alias=alias,
            details=_details(details),
        )

    def unknown(
        self, text: str, reason: str, details: Mapping[str, str] | None = None
    ) -> NormalizationResult:
        return NormalizationResult(
            vocabulary=self.name,
            vocabulary_version=self.version,
            taxonomy_version=self.taxonomy_version,
            status=NormalizationStatus.UNKNOWN,
            input=text,
            reason=reason,
            details=_details(details),
        )

    def ambiguous(
        self,
        text: str,
        entries: Iterable[Entry],
        reason: str,
        details: Mapping[str, str] | None = None,
    ) -> NormalizationResult:
        unique = {entry.key: entry for entry in entries}
        return NormalizationResult(
            vocabulary=self.name,
            vocabulary_version=self.version,
            taxonomy_version=self.taxonomy_version,
            status=NormalizationStatus.AMBIGUOUS,
            input=text,
            candidates=tuple(
                Candidate(e.key, e.label) for e in sorted(unique.values(), key=lambda e: e.key)
            ),
            reason=reason,
            details=_details(details),
        )

    # --- Versioning ---------------------------------------------------------------

    def fingerprint(self) -> str:
        """SHA-256 of the vocabulary's meaning (everything but its version).

        Tests pin it per version, so changing keys, aliases or ambiguity
        without bumping the version fails loudly instead of silently changing
        what an existing version means.
        """
        content = {
            "name": self.name,
            "categories": [[c.key, c.label] for c in self.categories],
            "entries": [
                {
                    "key": e.key,
                    "label": e.label,
                    "category": e.category,
                    "aliases": sorted([f, a or ""] for f, a in _entry_forms(e).items()),
                    "attributes": sorted([list(a) for a in e.attributes]),
                }
                for e in sorted(self.entries, key=lambda e: e.key)
            ],
            "ambiguous_terms": sorted(
                [_fold_required(t.text, self.name), t.reason, sorted(t.candidates)]
                for t in self.ambiguous_terms
            ),
        }
        encoded = json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()


def _entry_forms(entry: Entry) -> dict[str, str | None]:
    """Folded alias -> ambiguity note. An explicit alias overrides the
    label's implicit (unambiguous) one."""
    forms: dict[str, str | None] = {_fold_required(entry.label, entry.key): None}
    for alias in entry.aliases:
        item = alias if isinstance(alias, Alias) else Alias(alias)
        forms[_fold_required(item.text, entry.key)] = item.ambiguity
    return forms


def _fold_required(text: str, owner: str) -> str:
    folded = fold(text)
    if not folded:
        raise VocabularyError(f"{owner}: {text!r} has no comparable text")
    return folded


def _require_unique(values: list[str], message: str) -> None:
    duplicates = {v for v in values if values.count(v) > 1}
    if duplicates:
        raise VocabularyError(f"{message}: {sorted(duplicates)}")


def _details(details: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(details.items())) if details else ()
