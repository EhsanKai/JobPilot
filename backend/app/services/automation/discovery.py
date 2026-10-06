"""Form discovery (V0.10): what the page's controls are, as neutral data.

    PageSnapshot (raw controls)  ->  DiscoveredField[]

Each field gets a **stable identity** (`field_key`) built from what the page says
about it -- a stable DOM id, else the input's name, else its normalized label --
never from its position. So a field keeps its identity when the form is
rediscovered, when its label changes ("Email" -> "Email address") or when a new
field appears above it. Generated ids (":r1:", "headlessui-...") are not trusted.

The result contains only serializable metadata: label, name, id, type, options and
a locator description. No browser objects, no raw HTML, no values.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.application_question import QuestionType
from app.services.automation import safety
from app.services.automation.browser.base import FieldLocator, PageSnapshot, RawControl
from app.services.job_discovery.normalization.text import fold

#: Controls the assistant knows how to fill.
FILLABLE_KINDS = frozenset(
    {
        "text",
        "email",
        "tel",
        "url",
        "number",
        "date",
        "textarea",
        "select",
        "radio",
        "checkbox",
        "checkbox_group",
        "file",
    }
)
_TEXT_LIKE = frozenset({"text", "email", "tel", "url", "number", "date", "textarea"})
_YES = frozenset({"yes", "ja", "y", "true", "yes i do", "ja ich"})
_NO = frozenset({"no", "nein", "n", "false"})

_MAX_KEY = 240
_SIGNATURE = re.compile(r"\b(signature|sign here|e sign|unterschrift|unterzeichn\w*)\b")


@dataclass
class DiscoveredField:
    field_key: str
    position: int
    input_type: str
    label: str
    name: str | None
    dom_id: str | None
    form_context: str | None
    required: bool
    max_length: int | None
    options: list[dict[str, str]]
    meta: dict[str, Any]
    locator: FieldLocator
    #: LEGAL_CONSENT, CUSTOM_CONTROL, READ_ONLY, UNSUPPORTED_TYPE ... (None: fillable).
    user_action_reason: str | None = None
    #: A file field: "tailored_cv" / "cover_letter" when it clearly asks for one.
    detected_document_type: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def is_file(self) -> bool:
        return self.input_type == "file"

    @property
    def is_choice(self) -> bool:
        return self.input_type in ("select", "radio", "checkbox_group")

    @property
    def has_question(self) -> bool:
        """Whether the field is an answer to a question (a file or a legal
        confirmation is not)."""
        return (
            self.input_type in FILLABLE_KINDS
            and not self.is_file
            and self.user_action_reason is None
        )

    @property
    def option_values(self) -> list[str]:
        return [o["value"] for o in self.options]

    @property
    def option_labels(self) -> list[str]:
        return [o["label"] for o in self.options]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")[:100]


def _identity(control: RawControl) -> str:
    """The stable part of a field's identity, strongest evidence first."""
    group = control.kind in ("radio", "checkbox_group")
    prefix = "group" if group else "field"
    if control.dom_id and control.id_stable and not group:
        return f"{prefix}:id:{control.dom_id}"
    if control.name:
        return f"{prefix}:name:{control.name}"
    return f"{prefix}:label:{_slug(control.label) or 'unlabelled'}:{control.kind}"


def _fit_key(key: str) -> str:
    if len(key) <= _MAX_KEY:
        return key
    return f"{key[: _MAX_KEY - 9]}~{hashlib.sha256(key.encode()).hexdigest()[:8]}"


def _unique_keys(controls: list[RawControl]) -> list[str]:
    seen: dict[str, int] = {}
    keys: list[str] = []
    for control in controls:
        base = _fit_key(_identity(control))
        count = seen.get(base, 0)
        seen[base] = count + 1
        keys.append(base if count == 0 else _fit_key(f"{base}#{count + 1}"))
    return keys


def _label_for(control: RawControl) -> str:
    if control.label:
        return control.label
    # No label at all: fall back to what the page does call it, humanized.
    for candidate in (control.meta.get("placeholder"), control.name, control.dom_id):
        if candidate and (
            control.dom_id is None or control.id_stable or candidate != control.dom_id
        ):
            text = re.sub(r"[_\-\[\]]+", " ", str(candidate)).strip()
            if text:
                return text[:500]
    return ""


def classify(control: RawControl, label: str) -> tuple[str | None, str | None]:
    """(user_action_reason, detected_document_type) of a control."""
    if control.kind == "file":
        document = safety.detect_document_type(label, control.name, control.dom_id)
        if control.disabled:
            return "READ_ONLY", document
        return None, document
    if control.kind == "custom":
        return "CUSTOM_CONTROL", None
    if control.kind not in FILLABLE_KINDS:
        return "UNSUPPORTED_TYPE", None
    option_text = " ".join(o.label for o in control.options)
    consent_kinds = ("checkbox", "radio", "select", "checkbox_group")
    if control.kind in consent_kinds and safety.is_consent_label(label, control.name):
        return "LEGAL_CONSENT", None
    if control.kind == "checkbox_group" and safety.is_consent_label(option_text):
        return "LEGAL_CONSENT", None
    if control.kind in _TEXT_LIKE and _SIGNATURE.search(fold(f"{label} {control.name or ''}")):
        return "LEGAL_CONSENT", None
    if control.disabled or control.readonly:
        return "READ_ONLY", None
    return None, None


def discover_fields(snapshot: PageSnapshot) -> list[DiscoveredField]:
    keys = _unique_keys(snapshot.controls)
    fields: list[DiscoveredField] = []
    for position, (control, key) in enumerate(zip(snapshot.controls, keys, strict=True)):
        label = _label_for(control)
        reason, document = classify(control, label)
        fields.append(
            DiscoveredField(
                field_key=key,
                position=position,
                input_type=control.kind,
                label=label,
                name=control.name,
                dom_id=control.dom_id if control.id_stable else None,
                form_context=control.form,
                required=control.required,
                max_length=control.max_length,
                options=[{"value": o.value, "label": o.label} for o in control.options],
                meta=dict(control.meta),
                locator=FieldLocator(
                    kind=control.kind,
                    dom_id=control.dom_id if control.id_stable else None,
                    name=control.name,
                    label=control.label,
                    ordinal=control.ordinal,
                    form_index=control.form_index,
                ),
                user_action_reason=reason,
                detected_document_type=document,
            )
        )
    return fields


# --- The question a field is ---------------------------------------------------------------------


def _is_yes_no(labels: list[str]) -> bool:
    if len(labels) != 2:
        return False
    first, second = (fold(label) for label in labels)
    return first in _YES and second in _NO


def question_type_of(discovered: DiscoveredField, topic_is_salary: bool = False) -> QuestionType:
    """The V0.9 question type a field corresponds to."""
    kind = discovered.input_type
    if kind in ("select", "radio"):
        if discovered.meta.get("multiple"):
            return QuestionType.MULTI_CHOICE if discovered.options else QuestionType.UNKNOWN
        if _is_yes_no(discovered.option_labels):
            return QuestionType.YES_NO
        return QuestionType.SINGLE_CHOICE if discovered.options else QuestionType.UNKNOWN
    if kind == "checkbox_group":
        return QuestionType.MULTI_CHOICE if discovered.options else QuestionType.UNKNOWN
    if kind == "checkbox":
        return QuestionType.YES_NO
    if kind == "number":
        return QuestionType.SALARY if topic_is_salary else QuestionType.NUMERIC
    if kind == "date":
        return QuestionType.DATE
    if kind in ("text",) and topic_is_salary:
        return QuestionType.SALARY
    return QuestionType.FREE_TEXT


def question_options_of(discovered: DiscoveredField, qtype: QuestionType) -> list[str] | None:
    """The options of the question: the labels the user sees, in the form's order.
    (A yes/no pair keeps its two labels, yes first.)"""
    if qtype in (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE):
        return list(discovered.option_labels)
    if qtype is QuestionType.YES_NO and discovered.input_type in ("select", "radio"):
        return list(discovered.option_labels)
    return None
