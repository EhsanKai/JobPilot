"""Building blocks shared by the V0.5 JSONB documents.

Used by the Candidate Profile snapshot (`app.schemas.candidate_profile`),
Job Intelligence facts (`app.schemas.job_facts`) and Job Match results
(`app.schemas.job_match`). Every document model forbids unknown keys, so a
document can only ever contain the fields declared for it -- in particular,
none of the personal attributes the candidate snapshot deliberately leaves
out.

Vocabularies are validated, never inferred: a value that isn't recognized is
rejected, not guessed.
"""

import re
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

# ISO 3166-1 alpha-2, officially assigned codes.
ISO_3166_ALPHA2 = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO
    BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ
    DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP
    GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG
    KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML
    MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE
    PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL
    SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM
    US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
    """.split()
)

_LANGUAGE_KEY = re.compile(r"^[a-z]{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_PARTIAL_DATE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")
_MONEY_QUANTUM = Decimal("0.01")

# Verbatim evidence quotes are capped like Resume Intelligence evidence.
EVIDENCE_MAX_CHARS = 600


def _country_code(value: str) -> str:
    code = value.strip().upper()
    if code not in ISO_3166_ALPHA2:
        raise ValueError(f"{value!r} is not an ISO 3166-1 alpha-2 country code")
    return code


def _language_key(value: str) -> str:
    key = value.strip().lower()
    if not _LANGUAGE_KEY.match(key):
        raise ValueError(f"{value!r} is not an ISO 639-1 language code")
    return key


def _currency(value: str) -> str:
    code = value.strip().upper()
    if not _CURRENCY.match(code):
        raise ValueError(f"{value!r} is not a 3-letter ISO 4217 currency code")
    return code


def _partial_date(value: str) -> str:
    """Accept only 'YYYY' or 'YYYY-MM'. Unlike `normalize_partial_date` (used
    on parser output), stored documents never get a silent re-interpretation."""
    match = _PARTIAL_DATE.match(value.strip())
    if not match or (match.group(2) is not None and not 1 <= int(match.group(2)) <= 12):
        raise ValueError(f"{value!r} is not a 'YYYY' or 'YYYY-MM' date")
    return value.strip()


#: ISO 3166-1 alpha-2, normalized to upper case ("de" -> "DE").
CountryCode = Annotated[str, AfterValidator(_country_code)]
#: ISO 639-1, normalized to lower case ("DE" -> "de").
LanguageKey = Annotated[str, AfterValidator(_language_key)]
#: ISO 4217, normalized to upper case.
CurrencyCode = Annotated[str, AfterValidator(_currency)]
PartialDate = Annotated[str, AfterValidator(_partial_date)]


def quantize_money(value: Decimal | None) -> Decimal | None:
    """Money is stored with exactly two decimals, so equal amounts always
    serialize (and hash) identically."""
    return None if value is None else value.quantize(_MONEY_QUANTUM)


def unique_in_order(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


class DocumentModel(BaseModel):
    """Base for every V0.5 JSONB document part: unknown keys are rejected,
    strings are whitespace-stripped."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LocationValue(DocumentModel):
    """One location, deterministically normalized (no geocoding, no
    distances). Mirrors V0.4's `NormalizedLocation`: the text as given is
    kept; `city` / `country` are only set when the location vocabulary
    recognizes them."""

    text: str = Field(min_length=1, max_length=500)
    #: Canonical city name from the location vocabulary, e.g. "Cologne".
    city: str | None = Field(default=None, max_length=255)
    country: CountryCode | None = None
    is_remote: bool = False

    @field_validator("city", mode="before")
    @classmethod
    def _blank_city(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value
