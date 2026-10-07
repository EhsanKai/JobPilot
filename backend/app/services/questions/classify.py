"""Which questions have a deterministic answer (V0.9).

Some questions ask for something the application already states: the salary
expectation, the start date, the notice period, the right to work, the
candidate's contact details. Those are answered from structured data and
**never** by the AI -- an AI could only get them wrong (invent or "round" a
salary, guess a date). This module only *recognizes* them; the answer comes
from `deterministic.py`.

Recognition is conservative on purpose: a short field-label-like question
that clearly names the topic, in English or German. Anything else -- a long
or open question, a "why" question -- is not matched and goes down the AI path
(which is itself bound to the trusted facts). A false negative therefore only
costs the AI path (which will say "needs your input" for data it is not
shown); a false positive could answer the wrong question, so the patterns
are specific.
"""

import re
from enum import Enum

from app.models.application_question import QuestionType
from app.services.job_discovery.normalization.text import fold

#: Longer questions are prose, not a field label: never matched.
MAX_LABEL_CHARS = 200


class Intent(str, Enum):
    SALARY = "salary"
    START_DATE = "start_date"
    NOTICE_PERIOD = "notice_period"
    WORK_AUTHORIZATION = "work_authorization"
    SPONSORSHIP = "sponsorship"
    APPLICATION_LANGUAGE = "application_language"
    TOTAL_EXPERIENCE = "total_experience"
    FULL_NAME = "full_name"
    EMAIL = "email"
    PHONE = "phone"
    LOCATION = "location"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    PORTFOLIO = "portfolio"


#: Questions that explain or justify ("why ...") are open-ended by nature.
_OPEN_ENDED = re.compile(
    r"^(why|warum|wieso|weshalb|describe|tell us|erzahlen sie|beschreiben sie|what motivates|"
    r"how would you|wie wurden sie)\b"
)

_PATTERNS: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    (
        Intent.SPONSORSHIP,
        re.compile(r"\b(sponsorship|sponsor you|visa|visum|aufenthaltstitel)\b"),
    ),
    (
        Intent.WORK_AUTHORIZATION,
        re.compile(
            r"(authori[sz]ed|authori[sz]ation|eligible|permitted|legally|entitled|allowed|"
            r"right)\b.*\bto work\b|\bwork (permit|authori[sz]ation|eligibility)\b|"
            r"\bright to work\b|arbeitserlaubnis|arbeitsgenehmigung|arbeitsberechtigung|"
            r"berechtigt.*\barbeiten\b|\barbeiten.*berechtigt\b|aufenthaltserlaubnis"
        ),
    ),
    (
        Intent.SALARY,
        re.compile(
            r"\b(salary|compensation|pay|gehalt\w*|verguetung|vergutung|einkommen)\b.*"
            r"\b(expect\w*|desir\w*|requir\w*|wish\w*|target|vorstellung\w*|wunsch\w*|"
            r"erwart\w*|anforderung\w*|forderung\w*)\b|"
            r"\b(expected|desired|target|required)\s+(annual\s+|gross\s+|base\s+)?"
            r"(salary|compensation|pay)\b|\bgehaltsvorstellung\w*|\bgehaltswunsch\w*|"
            r"\bwunschgehalt\b|\bsalary (expectation|requirement)s?\b"
        ),
    ),
    (Intent.NOTICE_PERIOD, re.compile(r"\bnotice period\b|\bkundigungsfrist\b|\bkundigungszeit\b")),
    (
        Intent.START_DATE,
        re.compile(
            r"\b(when|wann)\b.*\b(start|begin|join|available|anfangen|beginnen|eintreten|"
            r"verfugbar|antreten)\b|\b(earliest|fruhest\w*)\b.*\b(start|starting|date|eintritt\w*|"
            r"beginn|termin|startdatum)\b|\b(start|starting|begin|beginning) date\b|"
            r"\bavailable (from|as of|starting|since)\b|\bavailability\b|\bverfugbarkeit\b|"
            r"\beintrittsdatum\b|\beintrittstermin\b|\bstartdatum\b|\bab wann\b"
        ),
    ),
    (
        Intent.APPLICATION_LANGUAGE,
        re.compile(
            r"\b(preferred|application|communication|correspondence|working)\s+language\b|"
            r"\b(bewerbungssprache|kommunikationssprache|bevorzugte sprache)\b"
        ),
    ),
    (
        Intent.TOTAL_EXPERIENCE,
        re.compile(
            r"^(how many )?years?( of)?( total| overall| professional| work| working| relevant)*"
            r" experience( do you have| do you possess| in total| overall)?$|"
            r"^wie viele jahre berufserfahrung( haben sie| besitzen sie)?$|"
            r"^(gesamte |gesamt)?berufserfahrung( in jahren)?$|^total years of experience$"
        ),
    ),
    (Intent.LINKEDIN, re.compile(r"\blinkedin\b")),
    (Intent.GITHUB, re.compile(r"\bgithub\b")),
    (
        Intent.PORTFOLIO,
        re.compile(
            r"\bportfolio\b|\bpersonal (website|site|homepage)\b|"
            r"\bpersonliche (website|webseite|homepage)\b"
        ),
    ),
    (
        Intent.EMAIL,
        re.compile(
            r"^(your |ihre |deine )?(e ?mail|e ?mail adresse|email address)( address)?$|"
            r"\b(what is |wie lautet )(your|ihre) e ?mail\b"
        ),
    ),
    (
        Intent.PHONE,
        re.compile(
            r"^(your |ihre )?(phone|mobile|cell|telephone|telefon|handy)( number| no| nummer)?$|"
            r"\b(phone|mobile|telephone|cell) number\b|\b(telefon|handy|mobil|ruf)\s?nummer\b"
        ),
    ),
    (
        Intent.FULL_NAME,
        re.compile(
            r"^(your |ihr |please enter your )?(full |complete |legal )?name$|"
            r"^(what is |wie lautet )(your|ihr)( full)? name$|\bvor ?und nachname\b|"
            r"^vollstandiger name$"
        ),
    ),
    (
        Intent.LOCATION,
        re.compile(
            r"\b(current|aktuelle\w*)\s+(location|city|residence|wohnort)\b|"
            r"\bwhere (are you|do you)\s+(currently\s+)?(based|located|live|reside)\b|"
            r"\bwohnort\b|\bwo (wohnen|leben) sie\b"
        ),
    ),
)

#: A question of this type is about this, whatever it says.
_TYPE_INTENT = {QuestionType.SALARY: Intent.SALARY}


def classify(text: str, question_type: QuestionType) -> Intent | None:
    """The deterministic topic of a question, or None.

    The type SALARY always means the salary expectation. Otherwise the text
    must be a short, non-open-ended label that matches a topic pattern.
    """
    if question_type in _TYPE_INTENT:
        return _TYPE_INTENT[question_type]
    folded = fold(text)
    if not folded or len(folded) > MAX_LABEL_CHARS or _OPEN_ENDED.match(folded):
        return None
    for intent, pattern in _PATTERNS:
        if pattern.search(folded):
            return intent
    return None
