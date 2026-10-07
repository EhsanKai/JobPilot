"""The state machine of an answer (V0.9): one pure function.

    a final answer exists      ->  ACCEPTED    (its source is DETERMINISTIC or AI)
                                   USER_EDITED (its source is USER or HYBRID)
    else a suggestion exists   ->  REJECTED if the user dismissed it, else SUGGESTED
    else the latest attempt
    found nothing supported    ->  NEEDS_INPUT
    else                       ->  NOT_STARTED

The status is stored (and the database checks it against the data) but is
only ever *set* from here, after every change, so it cannot disagree with the
columns it summarizes.
"""

from app.models.application_question import (
    AnswerSource,
    AnswerStatus,
    ApplicationQuestionAnswer,
)


def derive_status(answer: ApplicationQuestionAnswer) -> AnswerStatus:
    if answer.has_final:
        if answer.answer_source in (AnswerSource.DETERMINISTIC, AnswerSource.AI):
            return AnswerStatus.ACCEPTED
        return AnswerStatus.USER_EDITED
    if answer.has_suggestion:
        return AnswerStatus.REJECTED if answer.suggestion_dismissed else AnswerStatus.SUGGESTED
    if answer.needs_input_reason:
        return AnswerStatus.NEEDS_INPUT
    return AnswerStatus.NOT_STARTED
