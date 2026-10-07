"""Truth validation of an AI answer to an application question (V0.9).

The same standard as a CV or an Anschreiben, by the same machinery: an answer
is *claimed* content, and nothing becomes a suggestion until it has been
checked; an answer that fails is rejected whole and never repaired.

On top of the V0.8 claim checks (`documents.validation.ValidationRun`: every
number, skill, language, tool, organization, role / credential / achievement
claim, company-knowledge claim and any pay talk must be supported by a cited
fact) the shape of the question adds its own rules:

* **Traceability.** An answer cites at least one trusted fact; every id exists
  in the catalogue; a job fact is never support for a claim about the candidate.
* **Choices.** Only listed options may be selected (matched ignoring case and
  spacing, and stored as the listed option); a single choice selects one; a
  selected option is itself a claim ("Senior", "Python") and must be supported
  by the cited facts.
* **Numbers and dates.** The value must be stated by a cited fact -- nothing is
  calculated or estimated -- and the topic of the question (a technology it
  names) must be present in the cited facts: "5 years" cited from a fact about
  something else is not support for "years of Kubernetes".
* **Yes / No.** "Yes" must be supported; "No" only by a cited fact that says
  so: not finding something is not "No".
* **Length.** An answer longer than the question's limit is rejected,
  never truncated.

Lexical checks cannot prove an answer is *semantically* right (see
docs/application-questions.md); they catch the invented facts that make an
answer untrue, and the structure around them does the rest.
"""

import re
from dataclasses import dataclass
from datetime import date

from app.models.application_question import CHOICE_TYPES, QuestionType
from app.schemas.document_content import ValidationReport
from app.schemas.question_ai import AIQuestionAnswer
from app.services.documents import claims
from app.services.documents.facts import Fact
from app.services.documents.validation import ValidationRun
from app.services.job_discovery.normalization.text import fold
from app.services.questions.context import QuestionContext

_NEGATION = re.compile(
    r"(?<![a-z])(no|not|never|none|without|cannot|isn t|don t|doesn t|didn t|kein\w*|"
    r"nicht|nie|niemals|ohne)(?![a-z])"
)
NUMBER_FORMAT = re.compile(r"^[+-]?\d{1,3}(?:[.,' ]\d{3})*(?:[.,]\d+)?$|^[+-]?\d+(?:[.,]\d+)?$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DE_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


@dataclass(frozen=True)
class Validated:
    """The outcome of validating an AI answer."""

    report: ValidationReport
    #: The normalized suggestion (text or listed options), only when it passed.
    text: str | None = None
    options: list[str] | None = None
    #: The model said the facts do not support an answer: not a failure.
    needs_input: bool = False

    @property
    def passed(self) -> bool:
        return self.report.status == "passed"


def _valid_date(value: str) -> bool:
    for pattern, order in ((_ISO_DATE, (1, 2, 3)), (_DE_DATE, (3, 2, 1))):
        match = pattern.match(value)
        if match:
            year, month, day = (int(match.group(i)) for i in order)
            try:
                date(year, month, day)
            except ValueError:
                return False
            return True
    return False


def _match_option(option: str, options: list[str]) -> str | None:
    wanted = " ".join(option.split()).casefold()
    for candidate in options:
        if " ".join(candidate.split()).casefold() == wanted:
            return candidate
    return None


def validate_answer(output: AIQuestionAnswer, context: QuestionContext) -> Validated:
    """Check an AI answer against the question and the facts it cites."""
    run = ValidationRun(context.generation)
    question = context.question
    qtype = question.question_type

    if output.needs_user_input:
        # An honest "I can't answer this": nothing to check, nothing to store.
        return Validated(run.report(), needs_input=True)

    # --- Shape: what kind of answer the question wants ---------------------------------------
    options: list[str] | None = None
    text: str | None = None
    if qtype in CHOICE_TYPES:
        text, options = None, _validate_choice(run, output, context)
    else:
        if output.selected_options:
            run.add(
                "INVALID_OPTION",
                "Only choice questions have selected options.",
                "selected_options",
            )
        text = _validate_text_answer(run, output, context)

    # --- Citations ---------------------------------------------------------------------------
    facts = run.cited("answer", output.source_fact_ids)
    for job_fact_id in dict.fromkeys(output.job_fact_ids):
        if job_fact_id not in context.generation.job_fact_ids:
            run.add(
                "UNKNOWN_FACT_ID",
                "The answer refers to a job fact that does not exist.",
                "answer",
                job_fact_id,
            )

    # --- Claims ------------------------------------------------------------------------------
    claimed = text if text is not None else ", ".join(options or [])
    if claimed:
        run.check_text("answer", claimed, facts, letter=True)
        _check_topic(run, context, facts, claimed)
        if qtype is QuestionType.YES_NO and fold(claimed) in ("no", "nein"):
            support = " ".join(fact.text for fact in facts)
            if not _NEGATION.search(fold(support)):
                run.add(
                    "UNSUPPORTED_NEGATIVE",
                    '"No" needs a cited fact that says so: not finding something is not an '
                    "answer.",
                    "answer",
                    claimed,
                )
        if text is not None and len(text) > 0:
            run.check_language()

    report = run.report()
    if report.status == "failed":
        return Validated(report)
    return Validated(report, text=text, options=options)


def _validate_choice(
    run: ValidationRun, output: AIQuestionAnswer, context: QuestionContext
) -> list[str] | None:
    question = context.question
    if (output.answer or "").strip():
        run.add(
            "INVALID_OPTION",
            "A choice question is answered with listed options, not free text.",
            "answer",
        )
    selected: list[str] = []
    for raw in output.selected_options:
        listed = _match_option(raw, question.options or [])
        if listed is None:
            run.add(
                "INVALID_OPTION",
                "The answer selects an option that the question does not list.",
                "selected_options",
                raw,
            )
        elif listed in selected:
            run.add("INVALID_OPTION", "An option is selected twice.", "selected_options", raw)
        else:
            selected.append(listed)
    if not output.selected_options:
        run.add("INVALID_OPTION", "No option was selected.", "selected_options")
    if question.question_type is QuestionType.SINGLE_CHOICE and len(selected) > 1:
        run.add(
            "INVALID_OPTION",
            "A single choice question takes exactly one option.",
            "selected_options",
        )
    return selected if selected else None


def _validate_text_answer(
    run: ValidationRun, output: AIQuestionAnswer, context: QuestionContext
) -> str | None:
    question = context.question
    qtype = question.question_type
    text = (output.answer or "").strip()
    if not text:
        run.add("EMPTY_TEXT", "The answer is empty.", "answer")
        return None
    if question.max_length is not None and len(text) > question.max_length:
        run.add(
            "ANSWER_TOO_LONG",
            f"The answer has {len(text)} characters; the question allows {question.max_length}.",
            "answer",
        )
    if qtype in (QuestionType.NUMERIC, QuestionType.SALARY) and not NUMBER_FORMAT.match(text):
        run.add("INVALID_ANSWER_FORMAT", "A numeric question needs a plain number.", "answer", text)
    if qtype is QuestionType.DATE and not _valid_date(text):
        run.add(
            "INVALID_ANSWER_FORMAT",
            "A date question needs a valid date (YYYY-MM-DD or DD.MM.YYYY).",
            "answer",
            text,
        )
    if qtype is QuestionType.YES_NO:
        options = question.options or ["Yes", "No"]
        listed = _match_option(text, options) or _match_option(text, ["Yes", "No", "Ja", "Nein"])
        if listed is None:
            run.add(
                "INVALID_ANSWER_FORMAT", "A yes/no question is answered Yes or No.", "answer", text
            )
        elif question.options and _match_option(text, question.options) is None:
            run.add(
                "INVALID_OPTION",
                "The answer is not one of the question's options.",
                "answer",
                text,
            )
        else:
            return listed
    return text


def _check_topic(
    run: ValidationRun, context: QuestionContext, facts: list[Fact], claimed: str
) -> None:
    """The cited facts must be about what the question asks: a technology, tool
    or credential *named in the question* has to appear in them. Without this
    a true-but-unrelated fact could "support" any yes or any number."""
    # The position, company and place a question names are references, not topics.
    question = claims.mask_job_references(
        context.question.text,
        titles=context.generation.title_variants,
        names=context.generation.name_variants,
    )
    support = claims.Support.of([fact.text for fact in facts])
    for key, alias in claims.term_keys(question).items():
        if key not in support.terms and not support.has_word(alias):
            run.add(
                "UNSUPPORTED_TOPIC",
                "The cited facts do not mention what the question asks about.",
                "answer",
                alias,
            )
    for token in claims.technology_tokens(question):
        if not support.has_word(token) and not (
            set(claims.term_keys(token)) and set(claims.term_keys(token)) <= support.terms
        ):
            run.add(
                "UNSUPPORTED_TOPIC",
                "The cited facts do not mention what the question asks about.",
                "answer",
                token,
            )
    for code, groups in claims.claim_groups(question).items():
        supported = support.claim_groups.get(code, frozenset())
        for group, word in groups.items():
            if group not in supported:
                run.add(
                    "UNSUPPORTED_TOPIC",
                    "The cited facts do not mention what the question asks about.",
                    "answer",
                    word,
                )
