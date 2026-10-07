"""Field mapping (V0.10): which `ApplicationQuestion` does a form field correspond to?

Deterministic rules first, AI only when they are not enough, the user last:

    1. identity      a question already created for this very field (same stable key)
    2. exact text    an existing question that asks the same thing in the same words
    3. topic         the same V0.9 deterministic topic (email, phone, salary, notice ...)
    4. AI            only among *existing, unclaimed, compatible* questions, structured
                     output, confidence-gated; it can never create or invent a question
    5. new question  nothing existing fits: a neutral `source="browser"` question is
                     created, identified by `external_key` so rediscovery never duplicates it

A mapping is applied automatically only when it is HIGH confidence. Anything less
-- an ambiguous match, a merely similar question, an AI answer below HIGH -- is
NEEDS_REVIEW and waits for the user: it is never filled. The user's own decision
(confirm / change / unmap / create) always wins and is remembered for later sessions.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.provider import AIError, AIProvider
from app.core.logging import get_logger
from app.models.application_question import (
    ApplicationQuestion,
    ApplicationQuestionAnswer,
    QuestionType,
)
from app.models.automation import MappingStatus
from app.schemas.application_question import MAX_QUESTION_CHARS, MAX_USER_ANSWER_CHARS
from app.schemas.automation_ai import AIFieldMapping
from app.services.automation import prompts
from app.services.automation.discovery import (
    DiscoveredField,
    question_options_of,
    question_type_of,
)
from app.services.job_discovery.normalization.text import fold
from app.services.questions.classify import Intent, classify
from app.services.questions.text import normalize_question, question_hash

logger = get_logger(__name__)

BROWSER_SOURCE = "browser"
EXTERNAL_KEY_PREFIX = "browser:"
MAX_QUESTIONS_PER_APPLICATION = 200

_STOPWORDS = frozenset(
    "a an the you your do does did is are was were what why how please enter of for to in on at "
    "and or with we our us i my me this that it be have has can will would should ihr ihre sie "
    "der die das und oder bitte ein eine einen wie was warum fur mit von zu im".split()
)

#: Which question types can be answered into which control.
_COMPATIBLE: dict[str, frozenset[QuestionType]] = {
    "text": frozenset(
        {
            QuestionType.FREE_TEXT,
            QuestionType.UNKNOWN,
            QuestionType.NUMERIC,
            QuestionType.SALARY,
            QuestionType.DATE,
        }
    ),
    "number": frozenset(
        {QuestionType.NUMERIC, QuestionType.SALARY, QuestionType.FREE_TEXT, QuestionType.UNKNOWN}
    ),
    "date": frozenset({QuestionType.DATE, QuestionType.FREE_TEXT, QuestionType.UNKNOWN}),
    "choice": frozenset(
        {
            QuestionType.SINGLE_CHOICE,
            QuestionType.YES_NO,
            QuestionType.FREE_TEXT,
            QuestionType.UNKNOWN,
        }
    ),
    "multi": frozenset({QuestionType.MULTI_CHOICE}),
    "checkbox": frozenset({QuestionType.YES_NO}),
}


def _control_family(kind: str) -> str:
    if kind in ("select", "radio"):
        return "choice"
    if kind == "checkbox_group":
        return "multi"
    if kind == "checkbox":
        return "checkbox"
    if kind in ("number", "date"):
        return kind
    return "text"


def compatible(discovered: DiscoveredField, question: ApplicationQuestion) -> bool:
    return question.question_type in _COMPATIBLE[_control_family(discovered.input_type)]


def _content_tokens(text: str) -> set[str]:
    return {t for t in fold(text).split() if t not in _STOPWORDS and len(t) > 1}


def similarity(a: str, b: str) -> float:
    left, right = _content_tokens(a), _content_tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


@dataclass
class MappingDecision:
    status: MappingStatus
    method: str | None
    confidence: str | None
    reason: str | None
    question: ApplicationQuestion | None = None
    proposed: ApplicationQuestion | None = None
    #: Nothing existing fits: create a browser question for the field.
    create: bool = False
    provenance: dict[str, Any] | None = None


def external_key_of(field_key: str) -> str:
    return f"{EXTERNAL_KEY_PREFIX}{field_key}"[:255]


class FieldMapper:
    """Decides the mapping of discovered fields against one application's questions.

    `claimed` holds the questions already mapped by another field of this page: a
    question answers one field.
    """

    def __init__(
        self,
        questions: list[ApplicationQuestion],
        provider: AIProvider | None,
        *,
        claimed: set[uuid.UUID],
    ) -> None:
        self._questions = questions
        self._provider = provider
        self.claimed = claimed

    def _candidates(self, discovered: DiscoveredField) -> list[ApplicationQuestion]:
        return [
            q
            for q in self._questions
            if q.external_key is None and q.id not in self.claimed and compatible(discovered, q)
        ]

    def decide(self, discovered: DiscoveredField) -> MappingDecision:
        decision = self._decide(discovered)
        if decision.question is not None:
            self.claimed.add(decision.question.id)
        return decision

    def _decide(self, discovered: DiscoveredField) -> MappingDecision:
        key = external_key_of(discovered.field_key)
        # 1. identity: a question already made for this very field.
        for question in self._questions:
            if question.external_key == key:
                return MappingDecision(
                    MappingStatus.AUTO_MAPPED, "IDENTITY", "HIGH", "SAME_FIELD", question=question
                )

        candidates = self._candidates(discovered)
        label = discovered.label
        salary = classify(label, QuestionType.FREE_TEXT) is Intent.SALARY
        qtype = question_type_of(discovered, topic_is_salary=salary)

        # 2. exact text.
        same = [q for q in candidates if q.question_hash == question_hash(label)]
        if len(same) == 1:
            return MappingDecision(
                MappingStatus.AUTO_MAPPED, "EXACT_TEXT", "HIGH", "SAME_QUESTION", question=same[0]
            )
        if len(same) > 1:
            return MappingDecision(
                MappingStatus.NEEDS_REVIEW, "EXACT_TEXT", "MEDIUM", "AMBIGUOUS_MATCH"
            )

        # 3. topic (email, phone, salary, notice period ...).
        intent = classify(label, qtype)
        if intent is not None:
            topical = [
                q for q in candidates if classify(q.question_text, q.question_type) is intent
            ]
            if len(topical) == 1:
                return MappingDecision(
                    MappingStatus.AUTO_MAPPED,
                    "TOPIC",
                    "HIGH",
                    f"SAME_TOPIC:{intent.value}",
                    question=topical[0],
                )
            if len(topical) > 1:
                return MappingDecision(
                    MappingStatus.NEEDS_REVIEW, "TOPIC", "MEDIUM", "AMBIGUOUS_MATCH"
                )

        # 4. the AI -- or, failing that, something similar -- among what exists.
        if candidates:
            if self._provider is not None:
                ai = self._ai_decision(discovered, candidates)
                if ai is not None:
                    return ai
            scored = sorted(
                ((similarity(label, q.question_text), q) for q in candidates),
                key=lambda pair: -pair[0],
            )
            if scored and scored[0][0] >= 0.5:
                tie = len(scored) > 1 and scored[1][0] == scored[0][0]
                return MappingDecision(
                    MappingStatus.NEEDS_REVIEW,
                    "SIMILAR",
                    "MEDIUM",
                    "SIMILAR_QUESTION",
                    proposed=None if tie else scored[0][1],
                )

        # 5. nothing fits: a new question for this field.
        return MappingDecision(
            MappingStatus.AUTO_MAPPED, "CREATED", "HIGH", "NEW_QUESTION", create=True
        )

    # --- AI --------------------------------------------------------------------------------------
    def _ai_decision(
        self, discovered: DiscoveredField, candidates: list[ApplicationQuestion]
    ) -> MappingDecision | None:
        assert self._provider is not None
        provider = self._provider
        context = {
            "field": {
                "label": discovered.label,
                "input_type": discovered.input_type,
                "required": discovered.required,
                "options": discovered.option_labels or None,
                "max_length": discovered.max_length,
            },
            "candidates": [
                {
                    "id": str(q.id),
                    "question": q.question_text,
                    "type": q.question_type.value,
                    "options": q.options,
                }
                for q in candidates[:40]
            ],
        }
        try:
            output = _run(
                provider.generate_structured(
                    system=prompts.system_prompt(),
                    prompt=prompts.user_prompt(context),
                    schema=AIFieldMapping,
                )
            )
        except AIError as exc:
            # Codes only: never a prompt, a label or a provider message.
            logger.warning("Field mapping by AI failed: %s", exc.code)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Field mapping by AI failed unexpectedly (%s)", type(exc).__name__)
            return None
        provenance = {
            "method": "AI",
            "prompt_version": prompts.FIELD_MAPPING_PROMPT_VERSION,
            "prompt_hash": prompts.current_prompt_hash(),
            "ai_provider": provider.name,
            "ai_model": provider.model,
            "confidence": output.confidence,
            "reason": output.reason[:200],
            "mapped_at": datetime.now(UTC).isoformat(),
        }
        if output.application_question_id is None:
            return None  # the AI found nothing: create a new question
        by_id = {str(q.id): q for q in candidates}
        chosen = by_id.get(output.application_question_id)
        if chosen is None:
            # An id that is not one of the candidates is not a mapping.
            logger.warning("Field mapping by AI named an unknown question; ignored")
            return None
        if output.confidence == "HIGH" and compatible(discovered, chosen):
            return MappingDecision(
                MappingStatus.AUTO_MAPPED,
                "AI",
                "HIGH",
                "AI_MATCH",
                question=chosen,
                provenance=provenance,
            )
        return MappingDecision(
            MappingStatus.NEEDS_REVIEW,
            "AI",
            output.confidence,
            "AI_NOT_CERTAIN",
            proposed=chosen,
            provenance=provenance,
        )


def _run(coroutine: Any) -> Any:
    """Run an AI call from a request thread (no loop is running in it)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    # A loop is running in this thread: run the call on a private one.
    result: dict[str, Any] = {}

    def target() -> None:
        try:
            result["value"] = asyncio.run(coroutine)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    import threading

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


# --- Creating and refreshing browser questions ---------------------------------------------------


def source_metadata_of(discovered: DiscoveredField) -> dict[str, Any]:
    """What the question remembers of its field: small, neutral, no page source."""
    strategy = "id" if discovered.dom_id else "name" if discovered.name else "label"
    return {
        "input_type": discovered.input_type,
        "normalized_label": normalize_question(discovered.label)[:200],
        "name": discovered.name,
        "id": discovered.dom_id,
        "form": discovered.form_context,
        "option_values": discovered.option_values[:30] or None,
        "selector_strategy": strategy,
        "discovered_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _options_for_question(
    discovered: DiscoveredField, qtype: QuestionType
) -> tuple[QuestionType, list[str] | None]:
    options = question_options_of(discovered, qtype)
    if options is None:
        return qtype, None
    cleaned = [" ".join(o.split()) for o in options]
    folded = [o.casefold() for o in cleaned]
    if any(not o for o in cleaned) or len(set(folded)) != len(folded):
        # Options a question cannot represent: keep the field answerable by text only.
        return QuestionType.UNKNOWN, None
    return qtype, cleaned


def create_browser_question(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID, discovered: DiscoveredField
) -> ApplicationQuestion | None:
    """A neutral `source="browser"` question for a field nothing existing fits.
    None when the application already has the maximum number of questions."""
    count, newest = db.execute(
        select(func.count(), func.max(ApplicationQuestion.sort_order)).where(
            ApplicationQuestion.application_id == application_id
        )
    ).one()
    if count >= MAX_QUESTIONS_PER_APPLICATION:
        return None
    topic_is_salary = classify(discovered.label, QuestionType.FREE_TEXT) is Intent.SALARY
    qtype, options = _options_for_question(
        discovered, question_type_of(discovered, topic_is_salary=topic_is_salary)
    )
    text = " ".join(discovered.label.split())[:MAX_QUESTION_CHARS]
    max_length = discovered.max_length
    if max_length is not None and not (1 <= max_length <= MAX_USER_ANSWER_CHARS * 5):
        max_length = None
    question = ApplicationQuestion(
        application_id=application_id,
        user_id=user_id,
        question_text=text,
        normalized_question_text=normalize_question(text),
        question_hash=question_hash(text),
        question_type=qtype,
        required=discovered.required,
        max_length=max_length,
        options=options,
        external_key=external_key_of(discovered.field_key),
        field_name=(discovered.name or discovered.dom_id or None),
        source=BROWSER_SOURCE,
        source_metadata=source_metadata_of(discovered),
        sort_order=(newest + 1) if newest is not None else 0,
    )
    question.answer = ApplicationQuestionAnswer(user_id=user_id)
    db.add(question)
    db.flush()
    return question


@dataclass
class RefreshOutcome:
    updated: bool = False
    #: The form changed in a way the user's existing answer no longer fits.
    conflict: bool = False
    fields: list[str] = field(default_factory=list)


def refresh_browser_question(
    question: ApplicationQuestion, discovered: DiscoveredField
) -> RefreshOutcome:
    """Bring a browser-created question up to date with its field (a label that
    became "Email address", a new option) **without ever touching the answer**: a
    change the user's answer would no longer satisfy is not applied at all."""
    from app.services.questions.service import check_user_answer

    outcome = RefreshOutcome()
    if question.source != BROWSER_SOURCE:
        return outcome  # the user's own questions are never rewritten
    topic_is_salary = classify(discovered.label, QuestionType.FREE_TEXT) is Intent.SALARY
    qtype, options = _options_for_question(
        discovered, question_type_of(discovered, topic_is_salary=topic_is_salary)
    )
    text = " ".join(discovered.label.split())[:MAX_QUESTION_CHARS]
    changes: dict[str, Any] = {}
    if text and text != question.question_text:
        changes["question_text"] = text
    if qtype is not question.question_type:
        changes["question_type"] = qtype
    if options != question.options:
        changes["options"] = options
    if discovered.required != question.required:
        changes["required"] = discovered.required
    if discovered.max_length != question.max_length and (
        discovered.max_length is None or discovered.max_length >= 1
    ):
        changes["max_length"] = discovered.max_length
    if not changes:
        question.source_metadata = {
            **(question.source_metadata or {}),
            **source_metadata_of(discovered),
        }
        return outcome

    # Would the user's answer still satisfy the changed question?
    original = {name: getattr(question, name) for name in changes}
    for name, value in changes.items():
        setattr(question, name, value)
    answer = question.answer
    if answer is not None and answer.has_final:
        if check_user_answer(question, answer.final_answer, answer.final_selected_options):
            for name, value in original.items():
                setattr(question, name, value)
            outcome.conflict = True
            return outcome
    if "question_text" in changes:
        question.normalized_question_text = normalize_question(question.question_text)
        question.question_hash = question_hash(question.question_text)
    question.source_metadata = {
        **(question.source_metadata or {}),
        **source_metadata_of(discovered),
    }
    outcome.updated = True
    outcome.fields = sorted(changes)
    return outcome
