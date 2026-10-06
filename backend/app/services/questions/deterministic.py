"""Deterministic answers to application questions (V0.9).

When the answer is already in structured data, the AI is not called: it could
only invent or alter it. The salary expectation, the start date, the notice
period, the right to work, the application language, the candidate's contact
details and (when the resume's dates fully support it) the total years of
experience are answered here, from

    * the Application's own saved preparation data (V0.7), and
    * the confirmed Resume Profile (V0.2) -- accepted / edited items only.

Rules, all deliberate:

* **Missing is never a value.** No salary, no date, no authorization -> the
  result is `NeedsInput`, not "0", "No" or "unknown".
* **Nothing is derived from the question language.** The application language
  is the Application's own field; the language of an answer follows it (English
  when none is set), never the language the question happens to be in.
* **A choice question is answered only with an option that equals the value**
  (after case / diacritics folding). Otherwise `NeedsInput`: a salary is never
  rounded into a band, a date never mapped to a nearest option.
* **A character limit is respected, never truncated to**: the first rendering
  that fits is used; when none does, `NeedsInput` (`ANSWER_TOO_LONG`).
* The result carries the ids of the facts it was built from, so a deterministic
  suggestion is as traceable as an AI one.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models.application import NoticePeriodUnit, WorkAuthorization
from app.models.application_question import CHOICE_TYPES, QuestionType
from app.schemas.resume_profile import ResumeProfileData
from app.services.documents.context import LANGUAGE_NAMES, Preparation, format_amount
from app.services.documents.facts import (
    YEARS_FACT_ID,
    Fact,
    FactCatalog,
    application_facts,
    build_candidate_facts,
    confirmed_personal_information,
)
from app.services.job_discovery.normalization.text import fold
from app.services.questions.classify import Intent

# --- Why there is no answer ---------------------------------------------------------------------

#: Stable code -> what the user is told. "Needs your input", never a guess.
NEEDS_INPUT_MESSAGES: dict[str, str] = {
    "SALARY_MISSING": (
        "No salary expectation is saved for this application. Add it under Application "
        "preparation, or answer manually."
    ),
    "START_DATE_MISSING": (
        "No start date or notice period is saved for this application. Add it under "
        "Application preparation, or answer manually."
    ),
    "DATE_NOT_STATED": (
        "This asks for a date, but the application states only a notice period. JobPilot does "
        "not calculate dates: enter it yourself."
    ),
    "NOTICE_PERIOD_MISSING": (
        "No notice period is saved for this application. Add it under Application "
        "preparation, or answer manually."
    ),
    "WORK_AUTHORIZATION_MISSING": (
        "Your work authorization for this job isn't stated. Choose it under Application "
        "preparation, or answer manually."
    ),
    "WORK_AUTHORIZATION_AMBIGUOUS": (
        "Your saved work authorization doesn't answer this question directly, so JobPilot "
        "won't guess. Answer it yourself."
    ),
    "LANGUAGE_MISSING": (
        "No application language is selected. Choose it under Application preparation, or "
        "answer manually."
    ),
    "CONTACT_MISSING": (
        "This detail isn't in your confirmed resume. Confirm a resume that contains it, or "
        "answer manually."
    ),
    "EXPERIENCE_NOT_DERIVABLE": (
        "Your resume doesn't establish a total number of years (a role without usable dates "
        "or under a year). JobPilot won't estimate it: enter it yourself."
    ),
    "TYPE_MISMATCH": (
        "The answer format of this question doesn't fit the data JobPilot has. Answer it "
        "yourself."
    ),
    "OPTION_NOT_MATCHED": (
        "None of the options matches your saved data exactly, and JobPilot won't pick the "
        "closest one. Choose it yourself."
    ),
    "UNIT_MISMATCH": (
        "The unit this asks for differs from the one saved, and converting it would be a "
        "guess. Answer it yourself."
    ),
    "ANSWER_TOO_LONG": (
        "The answer would be longer than this question's character limit, and JobPilot never "
        "shortens an answer. Write a shorter one yourself."
    ),
    "FACTS_INSUFFICIENT": (
        "Your confirmed resume and application data don't establish an answer, so JobPilot "
        "won't guess. Answer it yourself."
    ),
}


def needs_input_message(reason: str | None) -> str | None:
    if reason is None:
        return None
    return NEEDS_INPUT_MESSAGES.get(
        reason, "JobPilot can't answer this from your saved data. Answer it yourself."
    )


# --- Results ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Suggested:
    """A deterministic suggestion: text, or the chosen options of a choice."""

    text: str | None
    options: list[str] | None
    fact_ids: list[str]


@dataclass(frozen=True)
class NeedsInput:
    reason: str


Resolution = Suggested | NeedsInput


@dataclass(frozen=True)
class QuestionShape:
    """What a resolver needs to know about the question."""

    text: str
    question_type: QuestionType
    max_length: int | None
    options: list[str] | None


# --- The data -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class KnownData:
    """Everything a deterministic answer may rest on, with its facts."""

    preparation: Preparation
    #: Confirmed personal details by field name; empty without a confirmed resume.
    personal: dict[str, str] = field(default_factory=dict)
    total_years: int | None = None
    facts: FactCatalog = field(default_factory=FactCatalog)

    @property
    def language(self) -> str:
        """The language answers are formatted in: the application's, else English."""
        language = self.preparation.language
        return language if language in ("de", "en") else "en"


def _plain_number(amount: Decimal) -> str:
    return str(int(amount)) if amount == amount.to_integral_value() else format(amount, "f")


def known_data(
    preparation: Preparation,
    *,
    profile_id: object | None = None,
    profile_data: ResumeProfileData | None = None,
) -> KnownData:
    """The facts behind deterministic answers, from the application's saved data
    and (when there is one) the confirmed resume."""
    catalog = FactCatalog()
    for fact in application_facts(
        available_from=preparation.available_from,
        notice_period=preparation.notice_period,
        work_authorization=preparation.work_authorization,
    ):
        catalog.add(fact)
    if preparation.salary_expectation is not None:
        currency = f" {preparation.salary_currency}" if preparation.salary_currency else ""
        catalog.add(
            Fact(
                "app.salary_expectation",
                f"Salary expectation for this application: "
                f"{_plain_number(preparation.salary_expectation)}{currency} (annual gross)",
                "application",
                ref="application",
            )
        )
    if preparation.language:
        catalog.add(
            Fact(
                "app.language",
                f"Application language: {preparation.language}",
                "application",
                ref="application",
            )
        )

    personal: dict[str, str] = {}
    total_years: int | None = None
    if profile_data is not None:
        personal = confirmed_personal_information(profile_data)
        labels = {
            "full_name": "Name",
            "email": "Email",
            "phone": "Phone",
            "location": "Location",
            "linkedin_url": "LinkedIn",
            "github_url": "GitHub",
            "portfolio_url": "Portfolio",
        }
        for name, label in labels.items():
            if name in personal:
                catalog.add(
                    Fact(
                        f"contact.{name.removesuffix('_url')}",
                        f"{label}: {personal[name]}",
                        "contact",
                        ref=f"resume_profile:{profile_id}",
                    )
                )
        years = build_candidate_facts(profile_id, profile_data).catalog.get(YEARS_FACT_ID)
        if years is not None:
            total_years = int(years.text.split()[0])
            catalog.add(years)
    return KnownData(preparation, personal, total_years, catalog)


# --- Values -------------------------------------------------------------------------------------


@dataclass
class _Value:
    """What one topic says, in each form a question may want it in."""

    fact_ids: list[str]
    #: Free-text renderings, preferred (fullest) first.
    text: list[str] = field(default_factory=list)
    number: str | None = None
    date_iso: str | None = None
    boolean: bool | None = None
    #: What an option must equal (folded) to be a match.
    option_candidates: list[str] = field(default_factory=list)
    #: Why there is no `number` when the question wants one.
    number_reason: str = "TYPE_MISMATCH"


_UNIT_WORDS = {
    "en": {
        NoticePeriodUnit.DAYS: ("day", "days"),
        NoticePeriodUnit.WEEKS: ("week", "weeks"),
        NoticePeriodUnit.MONTHS: ("month", "months"),
    },
    "de": {
        NoticePeriodUnit.DAYS: ("Tag", "Tage"),
        NoticePeriodUnit.WEEKS: ("Woche", "Wochen"),
        NoticePeriodUnit.MONTHS: ("Monat", "Monate"),
    },
}
_LANGUAGE_WORDS = {
    "en": {"de": "German", "en": "English"},
    "de": {"de": "Deutsch", "en": "Englisch"},
}
_YES_NO = {"en": ("Yes", "No"), "de": ("Ja", "Nein")}
_QUESTION_UNIT = {
    NoticePeriodUnit.DAYS: re.compile(r"\b(days?|tage[n]?)\b"),
    NoticePeriodUnit.WEEKS: re.compile(r"\b(weeks?|wochen?)\b"),
    NoticePeriodUnit.MONTHS: re.compile(r"\b(months?|monate[n]?)\b"),
}
_IMMEDIATE = ["immediately", "sofort", "none", "no notice period", "keine", "0"]


def _date_text(value: date, language: str) -> str:
    return value.strftime("%d.%m.%Y") if language == "de" else value.isoformat()


def _salary(data: KnownData, question: QuestionShape) -> _Value | str:
    prep = data.preparation
    if prep.salary_expectation is None:
        return "SALARY_MISSING"
    amount = format_amount(prep.salary_expectation, data.language)
    currency = f" {prep.salary_currency}" if prep.salary_currency else ""
    per_year = "brutto pro Jahr" if data.language == "de" else "gross per year"
    return _Value(
        ["app.salary_expectation"],
        text=[f"{amount}{currency} {per_year}", f"{amount}{currency}", amount],
        number=_plain_number(prep.salary_expectation),
    )


def _start_date(data: KnownData, question: QuestionShape) -> _Value | str:
    prep = data.preparation
    language = data.language
    if prep.available_from is not None:
        text = _date_text(prep.available_from, language)
        sentence = f"Verfügbar ab {text}" if language == "de" else f"Available from {text}"
        return _Value(
            ["app.available_from"],
            text=[sentence, text],
            date_iso=prep.available_from.isoformat(),
            option_candidates=[text, prep.available_from.isoformat()],
        )
    if prep.notice_period is not None:
        if question.question_type is QuestionType.DATE:
            return "DATE_NOT_STATED"
        notice = _notice(data, question)
        if isinstance(notice, str):
            return notice
        # No date is stated, so the answer is the notice period -- worded as such.
        prefix = "Kündigungsfrist" if language == "de" else "Notice period"
        return _Value(
            notice.fact_ids,
            text=[f"{prefix}: {text}" for text in notice.text],
            option_candidates=notice.option_candidates,
        )
    return "START_DATE_MISSING"


def _notice(data: KnownData, question: QuestionShape) -> _Value | str:
    prep = data.preparation
    if prep.notice_period is None:
        return "NOTICE_PERIOD_MISSING"
    value, unit = prep.notice_period
    language = data.language
    singular, plural = _UNIT_WORDS[language][unit]
    english_singular, english_plural = _UNIT_WORDS["en"][unit]
    german_singular, german_plural = _UNIT_WORDS["de"][unit]
    if value == 0:
        text = "Sofort verfügbar" if language == "de" else "Immediately (no notice period)"
        return _Value(
            ["app.notice_period"],
            text=[text, "Sofort" if language == "de" else "Immediately"],
            number="0",
            option_candidates=list(_IMMEDIATE),
        )
    words = singular if value == 1 else plural
    candidates = [
        f"{value} {english_singular if value == 1 else english_plural}",
        f"{value} {german_singular if value == 1 else german_plural}",
        f"{value} {unit.value}",
    ]
    number: str | None = None
    folded = fold(question.text)
    asked = [u for u, pattern in _QUESTION_UNIT.items() if pattern.search(folded)]
    if len(asked) == 1:
        if asked[0] is unit:
            number = str(value)
        elif unit is NoticePeriodUnit.WEEKS and asked[0] is NoticePeriodUnit.DAYS:
            number = str(value * 7)
        else:
            number = None  # converting (e.g. months -> weeks) would be a guess
    return _Value(
        ["app.notice_period"],
        text=[f"{value} {words}"],
        number=number,
        option_candidates=candidates,
        number_reason="UNIT_MISMATCH",
    )


def _work_authorization(data: KnownData, question: QuestionShape) -> _Value | str:
    state = data.preparation.work_authorization
    language = data.language
    if state is None or state is WorkAuthorization.UNKNOWN:
        return "WORK_AUTHORIZATION_MISSING"
    if state is not WorkAuthorization.AUTHORIZED:
        # "Needs sponsorship" does not say they are *not* allowed to work.
        return "WORK_AUTHORIZATION_AMBIGUOUS"
    sentence = (
        "Ich bin für diese Stelle arbeitsberechtigt."
        if language == "de"
        else "I am authorized to work for this job."
    )
    return _Value(
        ["app.work_authorization"],
        text=[sentence],
        boolean=True,
        option_candidates=["yes", "ja", "authorized", "authorised", "arbeitsberechtigt"],
    )


def _sponsorship(data: KnownData, question: QuestionShape) -> _Value | str:
    state = data.preparation.work_authorization
    language = data.language
    if state is None or state is WorkAuthorization.UNKNOWN:
        return "WORK_AUTHORIZATION_MISSING"
    needs = state is WorkAuthorization.REQUIRES_SPONSORSHIP
    if language == "de":
        sentence = (
            "Ja, ich benötige eine Sponsoring-Unterstützung."
            if needs
            else "Nein, ich benötige kein Sponsoring."
        )
    else:
        sentence = "Yes, I would need sponsorship." if needs else "No, I do not need sponsorship."
    return _Value(
        ["app.work_authorization"],
        text=[sentence],
        boolean=needs,
        option_candidates=["yes", "ja"] if needs else ["no", "nein"],
    )


def _application_language(data: KnownData, question: QuestionShape) -> _Value | str:
    language = data.preparation.language
    if language is None:
        return "LANGUAGE_MISSING"
    # Only the Application's own choice is an answer: never the language the
    # question was asked in.
    words = {_LANGUAGE_WORDS["en"].get(language), _LANGUAGE_WORDS["de"].get(language)}
    names = [word for word in (LANGUAGE_NAMES.get(language), *words) if word]
    return _Value(
        ["app.language"],
        text=[_LANGUAGE_WORDS[data.language].get(language, LANGUAGE_NAMES.get(language, language))],
        option_candidates=[*names, language],
    )


def _total_experience(data: KnownData, question: QuestionShape) -> _Value | str:
    if data.total_years is None:
        return "EXPERIENCE_NOT_DERIVABLE"
    years = data.total_years
    word = (
        ("Jahr" if years == 1 else "Jahre")
        if data.language == "de"
        else ("year" if years == 1 else "years")
    )
    return _Value(
        [YEARS_FACT_ID],
        text=[f"{years} {word}"],
        number=str(years),
        option_candidates=[str(years), f"{years} years", f"{years} jahre"],
    )


_Resolver = Callable[[KnownData, QuestionShape], "_Value | str"]


def _contact(field_name: str, fact_id: str) -> _Resolver:
    def resolve(data: KnownData, question: QuestionShape) -> _Value | str:
        value = data.personal.get(field_name)
        if not value:
            return "CONTACT_MISSING"
        return _Value([fact_id], text=[value], option_candidates=[value])

    return resolve


_RESOLVERS: dict[Intent, _Resolver] = {
    Intent.SALARY: _salary,
    Intent.START_DATE: _start_date,
    Intent.NOTICE_PERIOD: _notice,
    Intent.WORK_AUTHORIZATION: _work_authorization,
    Intent.SPONSORSHIP: _sponsorship,
    Intent.APPLICATION_LANGUAGE: _application_language,
    Intent.TOTAL_EXPERIENCE: _total_experience,
    Intent.FULL_NAME: _contact("full_name", "contact.full_name"),
    Intent.EMAIL: _contact("email", "contact.email"),
    Intent.PHONE: _contact("phone", "contact.phone"),
    Intent.LOCATION: _contact("location", "contact.location"),
    Intent.LINKEDIN: _contact("linkedin_url", "contact.linkedin"),
    Intent.GITHUB: _contact("github_url", "contact.github"),
    Intent.PORTFOLIO: _contact("portfolio_url", "contact.portfolio"),
}


# --- Rendering a value for the question's type ----------------------------------------------------


def _fit(candidates: list[str], max_length: int | None) -> str | None:
    """The first rendering within the limit. Never a truncation."""
    for candidate in candidates:
        if candidate and (max_length is None or len(candidate) <= max_length):
            return candidate
    return None


def _match_options(candidates: list[str], options: list[str]) -> list[str]:
    wanted = {fold(candidate) for candidate in candidates if candidate}
    return [option for option in options if fold(option) in wanted]


def _render(value: _Value, question: QuestionShape, data: KnownData) -> Resolution:
    qtype = question.question_type
    facts = value.fact_ids

    if qtype in CHOICE_TYPES:
        matches = _match_options(value.option_candidates, question.options or [])
        if len(matches) != 1:
            return NeedsInput("OPTION_NOT_MATCHED")
        return Suggested(None, matches, facts)

    if qtype is QuestionType.YES_NO:
        if value.boolean is None:
            return NeedsInput("TYPE_MISMATCH")
        labels = question.options if question.options else list(_YES_NO[data.language])
        # Options are given "yes first, then no" (see the question schema).
        return Suggested(labels[0] if value.boolean else labels[1], None, facts)

    if qtype in (QuestionType.NUMERIC, QuestionType.SALARY):
        if value.number is None:
            return NeedsInput(value.number_reason)
        number = _fit([value.number], question.max_length)
        return Suggested(number, None, facts) if number else NeedsInput("ANSWER_TOO_LONG")

    if qtype is QuestionType.DATE:
        if value.date_iso is None:
            return NeedsInput("TYPE_MISMATCH")
        date_text = _fit([value.date_iso], question.max_length)
        return Suggested(date_text, None, facts) if date_text else NeedsInput("ANSWER_TOO_LONG")

    # FREE_TEXT / UNKNOWN
    text = _fit(value.text, question.max_length)
    return Suggested(text, None, facts) if text else NeedsInput("ANSWER_TOO_LONG")


def resolve(intent: Intent, question: QuestionShape, data: KnownData) -> Resolution:
    """The deterministic answer of a recognized topic, or why there is none."""
    value = _RESOLVERS[intent](data, question)
    if isinstance(value, str):
        return NeedsInput(value)
    return _render(value, question, data)
