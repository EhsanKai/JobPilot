"""Answer resolution (V0.10): *which value may go into the browser?*

The browser layer never decides what is true -- V0.9 does, and its validation stays
authoritative. This module only decides what has been **approved** for a question,
in this order:

    1. the user's own answer              written or edited by them          (USER)
    2. deterministic application data     salary, availability, notice,      (DETERMINISTIC)
                                          work authorization, contact data,
                                          re-read from the saved data now
    3. an AI answer the user accepted     V0.9 copies it into the answer     (ACCEPTED_AI)
    4. an unreviewed AI suggestion        NEVER filled -- shown for review
    5. nothing                            "Needs your input"

An answer the user accepted or wrote is a V0.9 *final* answer; a suggestion that
was only generated is not, and is never typed into a real form. A deterministic
value the user rejected stays rejected. Required does not change any of this: a
required field with no approved answer is simply "needs your input".
"""

import re
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.application_question import (
    AnswerSource,
    ApplicationQuestion,
    ApplicationQuestionAnswer,
    GenerationMethod,
)
from app.models.user import User
from app.services.documents.service import GenerationInputs
from app.services.job_discovery.normalization.text import fold
from app.services.questions import deterministic
from app.services.questions.classify import classify
from app.services.questions.deterministic import KnownData, QuestionShape, Suggested
from app.services.questions.service import check_user_answer


class AnswerState(str, Enum):
    #: An approved value exists and may be filled.
    APPROVED = "APPROVED"
    #: No trusted answer: the user must provide one.
    NEEDS_INPUT = "NEEDS_INPUT"
    #: Only an unreviewed AI suggestion exists. Shown, never filled.
    SUGGESTION_AVAILABLE = "SUGGESTION_AVAILABLE"


@dataclass(frozen=True)
class ResolvedAnswer:
    state: AnswerState
    #: USER, DETERMINISTIC or ACCEPTED_AI (only for an approved answer).
    source: str | None = None
    text: str | None = None
    options: list[str] | None = None
    #: A stable reason code (needs input).
    reason: str | None = None
    message: str | None = None

    @property
    def approved(self) -> bool:
        return self.state is AnswerState.APPROVED


NEEDS_INPUT = ResolvedAnswer(
    AnswerState.NEEDS_INPUT, reason="NO_ANSWER", message="No approved answer yet."
)

# Topics only the browser layer knows: the form asks for the parts of the name.
_FIRST_NAME = re.compile(r"^(your |ihr |legal )?(first|given|fore) ?name$|^vorname$")
_LAST_NAME = re.compile(r"^(your |ihr |legal )?(last|family|sur) ?name$|^nachname$|^familienname$")


#: A follow-up that asks the user to explain something ("Please explain your visa
#: status") is open-ended even where it names a topic V0.9 answers from structured data:
#: a bare "No" would not answer it. (V0.9's own recognition is unchanged.)
_FOLLOW_UP = re.compile(
    r"^(please |bitte )?(explain|describe|elaborate|detail|specify|erlautern|beschreiben|"
    r"erklaren|begrunden)\b|\b(details|explanation|erlauterung|begrundung)$"
)


def _name_part(label: str, account: tuple[str, str]) -> str | None:
    folded = fold(label)
    if _FIRST_NAME.match(folded):
        return account[0].strip() or None
    if _LAST_NAME.match(folded):
        return account[1].strip() or None
    return None


def _shape(question: ApplicationQuestion) -> QuestionShape:
    return QuestionShape(
        text=question.question_text,
        question_type=question.question_type,
        max_length=question.max_length,
        options=list(question.options) if question.options is not None else None,
    )


def _suggestion_method(answer: ApplicationQuestionAnswer) -> GenerationMethod | None:
    method = (answer.ai_provenance or {}).get("method")
    return GenerationMethod(method) if method else None


class AnswerResolver:
    """Resolves the approved answer of the questions of one application.

    Built once per request: the application's data is read once. Resolving never
    writes anything and never calls an AI.
    """

    def __init__(self, db: Session, user: User, application: Application) -> None:
        inputs = GenerationInputs(db, user, application)
        self._data: KnownData = deterministic.known_data(
            inputs.preparation,
            profile_id=inputs.profile.id if inputs.profile is not None else None,
            profile_data=inputs.profile_data,
        )
        self._account = (user.first_name, user.last_name)

    # --- the hierarchy ---------------------------------------------------------------------------
    def resolve(self, question: ApplicationQuestion) -> ResolvedAnswer:
        answer = question.answer
        if answer is None:  # pragma: no cover - created together with the question
            return NEEDS_INPUT

        # 1. The user's own words.
        if answer.has_final and answer.answer_source in (AnswerSource.USER, AnswerSource.HYBRID):
            return self._final(question, answer, "USER")

        # 2. Deterministic data, unless the user turned that very suggestion down.
        rejected = (
            answer.suggestion_dismissed
            and _suggestion_method(answer) is GenerationMethod.DETERMINISTIC
        )
        det_reason: str | None = None
        if not rejected:
            outcome = self._deterministic(question)
            if isinstance(outcome, ResolvedAnswer):
                return outcome
            det_reason = outcome

        # 3. An AI (or accepted deterministic) answer the user accepted.
        if answer.has_final:
            source = "ACCEPTED_AI" if answer.answer_source is AnswerSource.AI else "DETERMINISTIC"
            return self._final(question, answer, source)

        # 4. Only a suggestion: for the user to review.
        if answer.has_suggestion and not answer.suggestion_dismissed:
            return ResolvedAnswer(
                AnswerState.SUGGESTION_AVAILABLE,
                text=answer.ai_suggestion,
                options=list(answer.ai_selected_options) if answer.ai_selected_options else None,
                reason="SUGGESTION_NOT_REVIEWED",
                message="A suggestion exists. Accept or edit it before it is filled.",
            )

        # 5. Nothing trusted.
        reason = det_reason or answer.needs_input_reason or "NO_ANSWER"
        return ResolvedAnswer(
            AnswerState.NEEDS_INPUT,
            reason=reason,
            message=deterministic.needs_input_message(reason)
            if reason != "NO_ANSWER"
            else NEEDS_INPUT.message,
        )

    # --- pieces ----------------------------------------------------------------------------------
    def _final(
        self, question: ApplicationQuestion, answer: ApplicationQuestionAnswer, source: str
    ) -> ResolvedAnswer:
        return ResolvedAnswer(
            AnswerState.APPROVED,
            source=source,
            text=answer.final_answer,
            options=list(answer.final_selected_options) if answer.final_selected_options else None,
        )

    def _deterministic(self, question: ApplicationQuestion) -> ResolvedAnswer | str | None:
        """An approved deterministic answer, a needs-input reason code, or None
        (the question is not a deterministic topic)."""
        part = _name_part(question.question_text, self._account)
        if part is not None and question.question_type.value in ("FREE_TEXT", "UNKNOWN"):
            if question.max_length is not None and len(part) > question.max_length:
                return "ANSWER_TOO_LONG"
            return ResolvedAnswer(AnswerState.APPROVED, source="DETERMINISTIC", text=part)
        if _FOLLOW_UP.search(fold(question.question_text)):
            return None
        intent = classify(question.question_text, question.question_type)
        if intent is None:
            return None
        resolution = deterministic.resolve(intent, _shape(question), self._data)
        if not isinstance(resolution, Suggested):
            return resolution.reason
        problem = check_user_answer(question, resolution.text, resolution.options)
        if problem is not None:
            return problem.code
        return ResolvedAnswer(
            AnswerState.APPROVED,
            source="DETERMINISTIC",
            text=resolution.text,
            options=resolution.options,
        )
