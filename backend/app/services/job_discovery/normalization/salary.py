"""Salary normalization: minimum, maximum, currency, period.

Structured amounts from the source win; free text is parsed only when no
amount is given, and only when it is unambiguous:

    "€70,000 - €90,000 per year"  -> 70000 / 90000 EUR, year
    "60k-75k EUR"                 -> 60000 / 75000 EUR
    "up to €90,000"               -> max 90000 EUR
    "CHF 9'500 per month"         -> 9500 CHF, month
    "€60k - €70k + €10k bonus"    -> not structured (three amounts: ambiguous)
    "Competitive salary"          -> not structured

Nothing is ever invented: a value the posting doesn't state stays NULL. The
source's own salary text is kept separately (`salary_text`) so ambiguous
salaries are still shown as written.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.models.job import SalaryPeriod
from app.services.job_discovery.raw_job import RawSalary
from app.services.job_discovery.vocabulary import (
    CURRENCY_CODES,
    CURRENCY_SYMBOLS,
    SALARY_PERIOD_PATTERNS,
)

# Largest value NUMERIC(12, 2) can hold, rounded down.
MAX_SALARY = Decimal("9999999999")

_SEPARATORS = ".,'’   "
_AMOUNT_RE = re.compile(
    r"(?P<number>\d{1,3}(?:[.,'’   ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?)"
    r"\s*(?P<k>[kK]\b|tsd\.?|thousand)?"
)
_THOUSANDS_ONLY_RE = re.compile(r"\d{1,3}(?:[.,'’   ]\d{3})+")
_THOUSANDS_DECIMALS_RE = re.compile(r"\d{1,3}(?:[.,'’   ]\d{3})+[.,]\d{1,2}")
_UP_TO_RE = re.compile(r"\b(?:up\s+to|bis\s+zu|bis|max(?:imum|imal)?\.?)\s*$", re.I)


@dataclass(frozen=True)
class NormalizedSalary:
    min_amount: Decimal | None
    max_amount: Decimal | None
    currency: str | None
    period: SalaryPeriod | None


def normalize_currency(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[text]
    code = text.upper()
    return code if code in CURRENCY_CODES else None


def normalize_salary_period(value: object) -> SalaryPeriod | None:
    if not isinstance(value, str) or not value.strip():
        return None
    for period, pattern in SALARY_PERIOD_PATTERNS:
        if pattern.search(value):
            return period
    return None


def to_amount(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, Decimal):
            amount = value
        elif isinstance(value, int | float):
            amount = Decimal(str(value))
        elif isinstance(value, str) and value.strip():
            amount = Decimal(value.strip().replace(",", ""))
        else:
            return None
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount > MAX_SALARY:
        return None
    return amount.quantize(Decimal("0.01"))


def _parse_number(number: str, *, thousands: bool) -> Decimal | None:
    if _THOUSANDS_ONLY_RE.fullmatch(number) and not thousands:
        # Thousands separators only, e.g. "60,000", "60.000" or "9'500".
        number = re.sub(f"[{_SEPARATORS}]", "", number)
    elif _THOUSANDS_DECIMALS_RE.fullmatch(number):
        # Thousands separators plus decimals, e.g. "60.000,00".
        number = re.sub(f"[{_SEPARATORS}]", "", number[:-3]) + "." + number[-2:]
    else:
        number = number.replace(",", ".")
    try:
        value = Decimal(number)
    except InvalidOperation:
        return None
    return value


def parse_salary_text(text: str) -> NormalizedSalary | None:
    """Parse simple salary text (see module docs). None when no amount is
    found, or when the text holds more than two amounts (ambiguous)."""
    found: list[tuple[Decimal, int]] = []
    for match in _AMOUNT_RE.finditer(text):
        is_thousands = bool(match.group("k"))
        value = _parse_number(match.group("number"), thousands=is_thousands)
        if value is None:
            continue
        amount = to_amount(value * (Decimal(1000) if is_thousands else Decimal(1)))
        if amount is not None:
            found.append((amount, match.start()))
    if not found or len(found) > 2:
        return None

    currency = None
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            break
    if currency is None:
        for token in re.findall(r"\b[A-Z]{3}\b", text.upper()):
            if token in CURRENCY_CODES:
                currency = token
                break
    period = normalize_salary_period(text)

    if len(found) == 2:
        return NormalizedSalary(found[0][0], found[1][0], currency, period)
    amount, position = found[0]
    if _UP_TO_RE.search(re.sub(r"[^\w\s.]", " ", text[:position]).strip()):
        return NormalizedSalary(None, amount, currency, period)
    return NormalizedSalary(amount, None, currency, period)


def normalize_salary(raw: RawSalary | None) -> NormalizedSalary | None:
    """Structured amounts win; free text is parsed only when no amount is
    given. Inconsistent ranges (min > max) are dropped, not guessed at."""
    if raw is None:
        return None
    min_amount = to_amount(raw.min_amount)
    max_amount = to_amount(raw.max_amount)
    currency = normalize_currency(raw.currency)
    period = normalize_salary_period(raw.period)

    if min_amount is None and max_amount is None and raw.text:
        parsed = parse_salary_text(raw.text)
        if parsed is not None:
            min_amount, max_amount = parsed.min_amount, parsed.max_amount
            currency = currency or parsed.currency
            period = period or parsed.period

    if min_amount is None and max_amount is None:
        return None
    if min_amount == 0 and max_amount in (None, 0):
        return None
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        return None
    return NormalizedSalary(min_amount, max_amount, currency, period)
