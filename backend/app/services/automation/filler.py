"""Browser filling (V0.10): from an approved value to a verified field.

    approved answer  ->  plan_value()   what exactly goes into this control (or why it can't)
                     ->  decide()       fill / already right / the user changed it
                     ->  write()        one of: type, choose, check, attach
                     ->  verify()       read the control back and compare

The filler is the only code that writes to a page, and it only ever receives a value
that `resolver.AnswerResolver` approved. It never reinterprets whether an answer is
true (V0.9's validation is authoritative) -- it only checks that the value *fits the
control*: an option the form really offers, a number for a number field, a date in
the format the control wants, text within the form's character limit. A value that does
not fit is reported, never altered or cut.

Every write is followed by a read-back. What the browser shows must equal what was
approved, or the field is `FILL_FAILED` -- the assistant does not carry on as if it
had worked.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from app.models.automation import AutomationField
from app.services.automation.browser.base import (
    BrowserError,
    BrowserPage,
    FieldLocator,
    FieldValue,
)
from app.services.automation.resolver import ResolvedAnswer
from app.services.job_discovery.normalization.text import fold
from app.services.questions.validation import NUMBER_FORMAT

_YES = frozenset({"yes", "ja", "y", "true"})
_NO = frozenset({"no", "nein", "n", "false"})


@dataclass(frozen=True)
class Intended:
    """What the control should hold once filled."""

    kind: str  # text | choice | checked | group | file
    #: The canonical comparison form (also what the hash is made of).
    canon: str
    text: str | None = None
    #: The option value of a select / radio.
    option_value: str | None = None
    checked: bool | None = None
    #: The values of a checkbox group's options to check.
    option_values: tuple[str, ...] = ()
    file_name: str | None = None
    file_size: int | None = None


@dataclass(frozen=True)
class Unfillable:
    """The approved answer cannot be entered into this control, as is."""

    code: str


# --- Canonical forms -----------------------------------------------------------------------------


def _norm_ws(text: str) -> str:
    return " ".join(text.split())


def canon_of_value(input_type: str, value: FieldValue) -> str:
    """The canonical comparison form of what a control currently holds."""
    if not value.found:
        return ""
    if input_type in ("select", "radio"):
        return value.text or (value.selected[0] if value.selected else "")
    if input_type == "checkbox":
        return "1" if value.checked else "0"
    if input_type == "checkbox_group":
        return ",".join(sorted(value.selected))
    if input_type == "file":
        return ";".join(f"{name}:{size}" for name, size in value.files)
    return (value.text or "").replace("\r\n", "\n")


def is_empty_canon(input_type: str, canon: str) -> bool:
    if input_type == "checkbox":
        return canon in ("", "0")
    return canon == ""


def hash_value(field_id: object, canon: str) -> str:
    """Salted SHA-256 of a value. The salt is the field id, so the stored hash can only
    be compared against this field; the value itself is never stored."""
    return hashlib.sha256(f"{field_id}:{canon}".encode()).hexdigest()


# --- Planning ------------------------------------------------------------------------------------


def match_option(options: list[dict[str, str]], wanted: str) -> str | None:
    """The value of the option that is exactly what the answer says (by its value or
    its visible label, ignoring case, spacing and diacritics). Never a fuzzy match."""
    target = fold(wanted)
    if not target:
        return None
    for option in options:
        if fold(option["value"]) == target or fold(option["label"]) == target:
            return option["value"]
    return None


def _parse_date(text: str) -> str | None:
    candidate = text.strip()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(candidate, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _answer_texts(answer: ResolvedAnswer) -> list[str]:
    if answer.options:
        return list(answer.options)
    return [answer.text] if answer.text is not None else []


def plan_value(field: AutomationField, answer: ResolvedAnswer) -> Intended | Unfillable:
    """What to put into the field for an approved answer, or why it cannot be put there."""
    kind = field.input_type
    texts = _answer_texts(answer)
    if not texts:
        return Unfillable("UNSUPPORTED_VALUE")
    options = list(field.options or [])

    if kind in ("text", "email", "tel", "url", "textarea", "number", "date"):
        value = ", ".join(texts) if len(texts) > 1 else texts[0]
        value = value.strip()
        if not value:
            return Unfillable("UNSUPPORTED_VALUE")
        if kind == "number" and not NUMBER_FORMAT.match(value):
            return Unfillable("UNSUPPORTED_VALUE")
        if kind == "date":
            iso = _parse_date(value)
            if iso is None:
                return Unfillable("UNSUPPORTED_VALUE")
            value = iso
        if kind != "textarea" and "\n" in value:
            return Unfillable("UNSUPPORTED_VALUE")  # a single-line control would flatten it
        if field.max_length is not None and len(value) > field.max_length:
            return Unfillable("ANSWER_TOO_LONG")
        return Intended("text", value.replace("\r\n", "\n"), text=value)

    if kind in ("select", "radio"):
        if len(texts) != 1:
            return Unfillable("UNSUPPORTED_VALUE")
        chosen = match_option(options, texts[0])
        if chosen is None:
            return Unfillable("OPTION_NOT_FOUND")
        return Intended("choice", chosen, option_value=chosen)

    if kind == "checkbox":
        if len(texts) != 1:
            return Unfillable("UNSUPPORTED_VALUE")
        word = fold(texts[0])
        if word in _YES:
            return Intended("checked", "1", checked=True)
        if word in _NO:
            return Intended("checked", "0", checked=False)
        return Unfillable("UNSUPPORTED_VALUE")

    if kind == "checkbox_group":
        values: list[str] = []
        for text in texts:
            chosen = match_option(options, text)
            if chosen is None:
                return Unfillable("OPTION_NOT_FOUND")
            if chosen not in values:
                values.append(chosen)
        return Intended("group", ",".join(sorted(values)), option_values=tuple(values))

    return Unfillable("UNSUPPORTED_VALUE")


# --- Deciding ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    #: fill | already_correct | user_modified
    action: str


def decide(
    field: AutomationField,
    intended: str,
    current: str,
    *,
    overwrite: bool,
) -> Decision:
    """Whether to write: only to an empty field, a field that still holds what the
    assistant itself wrote, or one the user explicitly asked to have overwritten.
    Anything else was put there by the user (or by the site) and is left alone.
    """
    if current == intended:
        return Decision("already_correct")
    if overwrite:
        return Decision("fill")
    last = field.last_agent_value_hash
    if last is not None:
        # The assistant wrote this field before: if it still shows exactly that, the
        # approved answer has changed since and the new one may replace it. Anything
        # else -- edited, replaced, cleared -- is the user's.
        if hash_value(field.id, current) == last:
            return Decision("fill")
        return Decision("user_modified")
    if is_empty_canon(field.input_type, current):
        return Decision("fill")
    return Decision("user_modified")


# --- Writing -------------------------------------------------------------------------------------


def locator_of(field: AutomationField) -> FieldLocator:
    return FieldLocator.from_dict(field.locator)


def read_current(page: BrowserPage, field: AutomationField) -> tuple[FieldValue, str]:
    value = page.read_value(locator_of(field))
    return value, canon_of_value(field.input_type, value)


def write(page: BrowserPage, field: AutomationField, intended: Intended) -> None:
    """Perform the one write. Raises `BrowserError`."""
    locator = locator_of(field)
    if intended.kind == "text":
        assert intended.text is not None
        page.write_text(locator, intended.text)
    elif intended.kind == "choice":
        assert intended.option_value is not None
        page.write_choice(locator, intended.option_value)
    elif intended.kind == "checked":
        assert intended.checked is not None
        page.write_checked(locator, intended.checked)
    elif intended.kind == "group":
        for option in field.options or []:
            wanted = option["value"] in intended.option_values
            page.write_checked(locator, wanted, option["value"])
    else:  # pragma: no cover - files go through write_file
        raise BrowserError("UNSUPPORTED_VALUE")


@dataclass(frozen=True)
class Verified:
    ok: bool
    #: The canonical value the browser shows now (for the stored hash).
    shown: str


def verify(page: BrowserPage, field: AutomationField, intended: Intended) -> Verified:
    """Read the control back and compare it with what was approved."""
    _, shown = read_current(page, field)
    if intended.kind == "text" and field.input_type == "number":
        # 3.50 and 3.5 are the same number.
        try:
            ok = float(shown) == float(intended.canon)
        except ValueError:
            ok = False
    else:
        ok = shown == intended.canon
    return Verified(ok, shown)


def intended_file(name: str, size: int) -> Intended:
    return Intended("file", f"{name}:{size}", file_name=name, file_size=size)


__all__ = [
    "Decision",
    "Intended",
    "Unfillable",
    "Verified",
    "canon_of_value",
    "decide",
    "hash_value",
    "intended_file",
    "is_empty_canon",
    "match_option",
    "plan_value",
    "read_current",
    "verify",
    "write",
]
