"""Deterministic description similarity.

Algorithm: Jaccard similarity of word 3-gram ("shingle") sets of the two
postings' normalized bodies (description + responsibilities + requirements,
folded -- see `normalization.content.normalized_body`):

    J(A, B) = |shingles(A) ∩ shingles(B)| / |shingles(A) ∪ shingles(B)|

Why shingles instead of single words: two different vacancies at the same
company share most of their *vocabulary* (boilerplate, tech stack), but much
less of their word *sequences*. 3-grams keep a little word order without
needing any model. Sets make the measure insensitive to repeated text, so a
source that lists requirements twice (once in the description, once as a
list) still compares fairly.

Cheap (linear in text length), no external dependency, no AI, and the same
inputs always give the same score.
"""

from dataclasses import dataclass

SHINGLE_SIZE = 3


@dataclass(frozen=True)
class Similarity:
    score: float
    #: Number of words in each body; short texts can't confirm identity.
    words_a: int
    words_b: int


def shingles(words: list[str], size: int = SHINGLE_SIZE) -> frozenset[tuple[str, ...]]:
    if not words:
        return frozenset()
    if len(words) < size:
        return frozenset({tuple(words)})
    return frozenset(tuple(words[i : i + size]) for i in range(len(words) - size + 1))


def jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def body_similarity(body_a: str, body_b: str) -> Similarity:
    """Similarity of two normalized bodies (already folded text)."""
    words_a = body_a.split()
    words_b = body_b.split()
    if words_a == words_b and words_a:
        return Similarity(1.0, len(words_a), len(words_b))
    return Similarity(jaccard(shingles(words_a), shingles(words_b)), len(words_a), len(words_b))
