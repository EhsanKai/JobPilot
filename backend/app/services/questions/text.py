"""The comparison forms of a question (V0.9)."""

import hashlib
import unicodedata

from app.models.application_question import ApplicationQuestion
from app.services.documents.context import canonical_hash


def normalize_question(text: str) -> str:
    """The comparison form: NFKC, whitespace collapsed, case-folded, without a
    trailing "?", ":", "." or "*" (a required-field marker). Diacritics are
    kept: this is stored, and "für" and "fur" are different words."""
    value = " ".join(unicodedata.normalize("NFKC", text).split()).casefold()
    return value.rstrip(" ?:.!*").strip()


def question_hash(text: str) -> str:
    """SHA-256 of the normalized text: the same question asked again, wherever
    it was found, has the same hash."""
    return hashlib.sha256(normalize_question(text).encode()).hexdigest()


def question_fingerprint(question: ApplicationQuestion) -> str:
    """SHA-256 of the question *as asked*: text, type, options and limit. A
    suggestion made for another fingerprint is outdated."""
    return canonical_hash(
        {
            "text": question.normalized_question_text,
            "type": question.question_type.value,
            "options": question.options,
            "max_length": question.max_length,
        }
    )
