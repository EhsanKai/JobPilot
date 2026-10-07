"""Contact-detail pseudonymization for AI requests (V0.13, data minimization).

Resume analysis needs the resume's text, but the AI provider does not need the
candidate's e-mail address or phone number to understand a CV. Before the text is sent,
each distinct e-mail address and phone number is replaced by a placeholder such as
`[EMAIL_1]` / `[PHONE_1]`; the parsed result gets the real values back locally. The
values JobPilot shows and validates are therefore unchanged (the provenance check still
finds them in the original text), while the provider only ever sees placeholders.

Phone numbers are only recognized in two unambiguous forms -- international (`+49 ...`)
or after a phone label (`Tel.`, `Phone:`, `Mobil`, ...) -- so dates, years and IDs are
never mistaken for one. A number in another form is sent as it is (documented limitation).
"""

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
_PHONE_BODY = r"\(?\d[\d \t()./-]{5,}\d"
_PHONE_INTERNATIONAL = re.compile(r"(?<![\w+])\+[ \t]?\d[\d \t()./-]{6,}\d")
_PHONE_LABELLED = re.compile(
    r"(?i)\b(?:tel(?:efon|ephone)?|phone|mobil(?:e|telefon)?|handy|cell|fon|ph)\.?\s*"
    r"(?:\([^)]*\)\s*)?[:.]?\s*(" + _PHONE_BODY + r")"
)
_MIN_PHONE_DIGITS = 7


@dataclass
class Pseudonymized:
    text: str
    #: placeholder -> original value
    mapping: dict[str, str] = field(default_factory=dict)

    def restore_text(self, value: str) -> str:
        for placeholder, original in self.mapping.items():
            value = value.replace(placeholder, original)
        return value


def _digits(value: str) -> int:
    return sum(ch.isdigit() for ch in value)


def pseudonymize_contact_details(text: str) -> Pseudonymized:
    result = Pseudonymized(text=text)
    assigned: dict[str, str] = {}
    counters = {"EMAIL": 0, "PHONE": 0}

    def placeholder(kind: str, value: str) -> str:
        if value not in assigned:
            counters[kind] += 1
            token = f"[{kind}_{counters[kind]}]"
            assigned[value] = token
            result.mapping[token] = value
        return assigned[value]

    def email(match: re.Match[str]) -> str:
        return placeholder("EMAIL", match.group(0))

    def international(match: re.Match[str]) -> str:
        value = match.group(0)
        if _digits(value) < _MIN_PHONE_DIGITS:
            return value
        return placeholder("PHONE", value)

    def labelled(match: re.Match[str]) -> str:
        number = match.group(1)
        if _digits(number) < _MIN_PHONE_DIGITS or number.startswith("[PHONE_"):
            return match.group(0)
        start, end = match.span(1)
        offset = match.start(0)
        whole = match.group(0)
        return whole[: start - offset] + placeholder("PHONE", number) + whole[end - offset :]

    redacted = _EMAIL.sub(email, text)
    redacted = _PHONE_INTERNATIONAL.sub(international, redacted)
    redacted = _PHONE_LABELLED.sub(labelled, redacted)
    result.text = redacted
    return result


def _restore(value: Any, pseudonymized: Pseudonymized) -> Any:
    if isinstance(value, str):
        return pseudonymized.restore_text(value)
    if isinstance(value, list):
        return [_restore(item, pseudonymized) for item in value]
    if isinstance(value, dict):
        return {key: _restore(item, pseudonymized) for key, item in value.items()}
    return value


def restore_model[M: BaseModel](model: M, pseudonymized: Pseudonymized) -> M:
    """The same structured result with every placeholder replaced by its original value."""
    if not pseudonymized.mapping:
        return model
    data = _restore(model.model_dump(mode="python"), pseudonymized)
    return type(model).model_validate(data)
