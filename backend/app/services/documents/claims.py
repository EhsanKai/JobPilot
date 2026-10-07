"""Finding the *claims* in generated text (V0.8).

Truth validation does not compare strings -- a legitimate paraphrase would
fail. It extracts the kinds of statements that are most damaging when
invented, and requires each one to be supported by the facts the text cites:

    numbers            "30%", "5 years", "2019", written-out numbers ("drei")
    taxonomy terms     skills, languages and degrees known to the V0.5.1 taxonomy
    technology tokens  acronyms and tool-shaped names the taxonomy does not know
                       ("ABAP", "C++", "S/4HANA", "PostgreSQL")
    organizations      "<Name> GmbH", "<Name> Inc."
    claim classes      leadership, seniority, executive titles, founding,
                       certifications, degrees, awards, publications, patents,
                       scaled results, long-experience claims -- by stem, in
                       German and English
    names (English)    capitalized words in the middle of a sentence

Every extractor here is pure and deterministic. The *support* for a text is
the combined text of the facts it cites; `extract` and `Support` compare the
two. This is the V0.2 truth-guard approach (normalized matching against
source text, whole-term matching for short terms) applied to generated
rather than extracted text, plus the V0.6 taxonomy finders.

Honest scope: lexical checks cannot prove that a sentence is *semantically*
supported. They catch the invented employers, tools, numbers, credentials and
titles that make a document untrue, and the structure around them
(`validation.py`: citations are required and must belong to the entry they
cite) does the rest. See docs/tailored-documents.md.
"""

import re
from dataclasses import dataclass, field
from functools import cache

from app.services.job_discovery.normalization.text import fold
from app.services.matching.terms import TermFinder
from app.services.resume_intelligence.validation import SourceText
from app.services.taxonomy import EDUCATION_LEVEL_VOCABULARY, LANGUAGES, SKILLS

# --- Numbers ------------------------------------------------------------------------------------

_NUMBER = re.compile(r"\d+(?:[.,'’]\d+)*")

#: Written-out numbers (German and English) that make a quantity claim. "one",
#: "ein" and "eine" are left out: they are articles as often as numbers.
_NUMBER_WORDS: dict[str, str] = {
    **{
        w: str(n)
        for n, w in enumerate(
            [
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
                "ten",
                "eleven",
                "twelve",
                "thirteen",
                "fourteen",
                "fifteen",
                "sixteen",
                "seventeen",
                "eighteen",
                "nineteen",
                "twenty",
            ],
            start=2,
        )
    },
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "hundred": "100",
    "thousand": "1000",
    "million": "1000000",
    **{
        w: str(n)
        for n, w in enumerate(
            [
                "zwei",
                "drei",
                "vier",
                "funf",
                "sechs",
                "sieben",
                "acht",
                "neun",
                "zehn",
                "elf",
                "zwolf",
                "dreizehn",
                "vierzehn",
                "funfzehn",
                "sechzehn",
                "siebzehn",
                "achtzehn",
                "neunzehn",
                "zwanzig",
            ],
            start=2,
        )
    },
    "dreissig": "30",
    "vierzig": "40",
    "funfzig": "50",
    "sechzig": "60",
    "siebzig": "70",
    "achtzig": "80",
    "neunzig": "90",
    "hundert": "100",
    "tausend": "1000",
    "millionen": "1000000",
}
_NUMBER_WORD = re.compile(
    r"(?<![a-z])("
    + "|".join(sorted(map(re.escape, _NUMBER_WORDS), key=len, reverse=True))
    + r")(?![a-z])"
)


def _forms(token: str) -> set[str]:
    """The comparable forms of a written number: "78.000" -> {"78000"},
    "7.50" -> {"750", "7.5"}, "03" -> {"3"}. Thousand separators and decimal
    marks are indistinguishable in general, so both readings are kept."""
    token = token.replace("’", "'")
    digits = re.sub(r"[.,']", "", token).lstrip("0") or "0"
    forms = {digits}
    parts = re.split(r"[.,']", token)
    if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
        forms.add(f"{parts[0].lstrip('0') or '0'}.{parts[1].rstrip('0')}".rstrip("."))
    return forms


def number_forms(text: str) -> dict[str, set[str]]:
    """Every number in `text` -> its comparable forms. Written-out numbers
    ("drei", "twenty") map to their digits."""
    found: dict[str, set[str]] = {}
    for match in _NUMBER.finditer(text):
        found[match.group(0)] = _forms(match.group(0))
    for match in _NUMBER_WORD.finditer(fold(text)):
        found[match.group(0)] = {_NUMBER_WORDS[match.group(0)]}
    return found


# --- Taxonomy terms -----------------------------------------------------------------------------


@cache
def _finders() -> tuple[TermFinder, ...]:
    return (
        TermFinder(SKILLS),
        TermFinder(LANGUAGES, min_alias_length=3),
        TermFinder(EDUCATION_LEVEL_VOCABULARY),
    )


def term_keys(text: str) -> dict[str, str]:
    """Unambiguous taxonomy terms in `text`: "<vocabulary>:<key>" -> the
    alias as written (folded). Ambiguous hits ("SAP", a bare acronym) are
    skipped here: the technology-token check covers them."""
    found: dict[str, str] = {}
    for finder in _finders():
        for hit in finder.find(text):
            if hit.key is not None:
                written = re.search(re.escape(hit.alias), text, re.IGNORECASE)
                key = f"{finder.vocabulary.name}:{hit.key}"
                found.setdefault(key, written.group(0) if written else hit.alias)
    return found


# --- Technology-shaped tokens and names ---------------------------------------------------------

_TECH_TOKEN = re.compile(
    r"""(?<![\w.])(?:
        [A-Z][A-Z0-9]+(?:[/&][A-Z0-9]+)*          # SAP, SQL, CI/CD, S4
      | [A-Za-z][A-Za-z0-9]*(?:\+\+|\#)           # C++, C#
      | \.[A-Za-z]{2,}                            # .NET
      | [A-Za-z]+\.[A-Za-z]{2,}                   # Node.js
      | [A-Z][A-Za-z]*/[0-9][A-Za-z0-9]+          # S/4HANA
      | [A-Za-z]+[0-9]+[A-Za-z0-9]*               # Python3, S4HANA
      | [A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+         # PostgreSQL, JavaScript
      | [a-z]+[A-Z][A-Za-z0-9]*                   # iOS, jQuery
    )(?![\w])""",
    re.X,
)
#: Tokens that look like names but aren't claims.
_TECH_ALLOWED = frozenset(
    "cv pdf it eu usa us uk dach ok pm am gmbh ag se kg ug mw mwd mfd gmd ca bzw usw etc "
    "e.g i.e z.b d.h u.a".split()
)

_ORGANIZATION = re.compile(
    r"\b((?:[A-ZÄÖÜ][\w&.\-]*\s+){0,3}[A-ZÄÖÜ][\w&.\-]*)\s+"
    r"(GmbH(?:\s*&\s*Co\.?\s*KG)?|AG|SE|KG|UG|Inc\.?|Ltd\.?|LLC|Corp\.?|plc|S\.A\.|B\.V\.|N\.V\.)"
    r"(?![\w])"
)

_MONTHS = frozenset(
    "january february march april may june july august september october november december "
    "monday tuesday wednesday thursday friday saturday sunday".split()
)
_CAPITALIZED = re.compile(r"\b[A-Z][a-z]{1,}\b")
_SENTENCE_END = re.compile(r"(?<=[.!?:])\s+")


def technology_tokens(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in _TECH_TOKEN.finditer(text):
        token = match.group(0)
        if token.lower() in _TECH_ALLOWED:
            continue
        seen.setdefault(token, None)
    return list(seen)


def organizations(text: str) -> list[str]:
    """Names with a legal form ("Example GmbH"), as written."""
    return [match.group(0) for match in _ORGANIZATION.finditer(text)]


def midsentence_names(text: str) -> list[str]:
    """Capitalized words after the first word of a sentence. In English that
    is a proper noun (an employer, a product, a place, a title); German
    capitalizes every noun, so callers only use this for English text."""
    seen: dict[str, None] = {}
    for sentence in _SENTENCE_END.split(text):
        words = list(re.finditer(r"\S+", sentence))
        for word in words[1:]:
            for match in _CAPITALIZED.finditer(word.group(0)):
                token = match.group(0)
                if token.lower() not in _MONTHS and token != "I":
                    seen.setdefault(token, None)
    return list(seen)


# --- Claim classes ------------------------------------------------------------------------------

#: group key -> pattern over *folded* text (lower case, no diacritics).
#: A text may use a word of a group only if its support does too.
_B = r"(?<![a-z])"
CLAIM_CLASSES: dict[str, dict[str, re.Pattern[str]]] = {
    "UNSUPPORTED_ROLE_CLAIM": {
        "leadership": re.compile(
            _B + r"(lead|leads|led|leader|leaders|leadership|leading (?:a|the|an)|manag\w*|"
            r"supervis\w*|oversaw|oversee\w*|spearhead\w*|ownership|"
            # German: management compounds (Teamleiter, Projektleitung ...), but not
            # Anleitung / begleiten / Buchführung, and not "führte ... durch" (carried out).
            r"(?:team|projekt|abteilungs|bereichs|gruppen|schicht|fach|produkt|programm|"
            r"entwicklungs|vertriebs|werks|geschafts|betriebs|standort|filial|lager|labor|"
            r"stations|ressort|qualitats|chef)?(?:leiter|leiterin|leitung)|"
            r"leitete|leitet|geleitet|leiten|fuhrungs\w*|"
            r"(?:personal|mitarbeiter|team|projekt)fuhrung)(?![a-z])"
        ),
        "seniority": re.compile(_B + r"(senior|junior|principal)(?![a-z])"),
        "executive": re.compile(
            _B + r"(head of|chief|director|vice president|vp|cto|ceo|cfo|coo|direktor\w*|"
            r"geschaftsfuhr\w*|vorstand|bereichsleit\w*)(?![a-z])"
        ),
        "founder": re.compile(
            _B + r"(founder|co-founder|founded|(?:mit)?grunder\w*|gegrundet|grundete)(?![a-z])"
        ),
        "mentoring": re.compile(_B + r"(mentor\w*|coach\w*)(?![a-z])"),
        "architect": re.compile(_B + r"(architect(?!ure)\w*|[a-z]*architekt\w*)(?![a-z])"),
    },
    "UNSUPPORTED_CREDENTIAL": {
        "certification": re.compile(
            _B + r"(certif\w*|zertifi\w*|licen[cs]\w*|lizenz\w*|accredit\w*|akkredit\w*)(?![a-z])"
        ),
        "degree": re.compile(
            _B + r"(degree\w*|bachelor\w*|master\w*|mba|ph\.?d|doctor\w*|promotion|diplom\w*|"
            r"abschluss\w*|studied|studium|graduat\w*|abitur|apprentice\w*|ausbildung)(?![a-z])"
        ),
    },
    "UNSUPPORTED_ACHIEVEMENT": {
        "award": re.compile(
            _B
            + r"(award\w*|prize\w*|preis\w*|ausgezeichnet|won|winner\w*|gewonnen|gewann)(?![a-z])"
        ),
        "publication": re.compile(
            _B + r"(published|publication\w*|veroffentlich\w*|paper\w*)(?![a-z])"
        ),
        "patent": re.compile(_B + r"patent\w*(?![a-z])"),
        "scale": re.compile(
            _B + r"(doubled|tripled|halved|verdoppel\w*|verdreifach\w*|halbiert\w*|million\w*|"
            r"billion\w*|milliard\w*|record\w*|rekord\w*)(?![a-z])"
        ),
    },
    "UNSUPPORTED_EXPERIENCE_CLAIM": {
        "duration": re.compile(
            _B + r"(decades?|jahrzehnt\w*|extensive experience|umfangreiche erfahrung|"
            r"langjahrig\w*|jahrelang\w*|seasoned|veteran)(?![a-z])"
        ),
    },
}

#: Claims about the company or the writer's life that no resume fact can
#: support. Flagged wherever they appear in a letter.
LETTER_CLAIM_CLASSES: dict[str, re.Pattern[str]] = {
    "COMPANY_KNOWLEDGE_CLAIM": re.compile(
        _B + r"(admire\w*|bewunder\w*|beeindruck\w*|impressed by|have (?:long )?followed|"
        r"follow(?:ed)? your|verfolge\w* (?:ihr|seit)|your (?:company )?(?:culture|values|vision|"
        r"mission|reputation)|ihre (?:unternehmens)?(?:kultur|werte|vision|mission|reputation)|"
        r"market leader|marktfuhrer\w*|leading (?:company|provider|employer)|"
        r"fuhrende[nsmr]? unternehmen|renowned|renommiert\w*|innovative company|"
        r"innovatives unternehmen|schon lange|seit langem)(?![a-z])"
    ),
    "PERSONAL_CIRCUMSTANCE_CLAIM": re.compile(
        _B + r"(relocat\w*|umzieh\w*|umzug|umsiedel\w*|my family|meine familie|children|kinder|"
        r"married|verheiratet|spouse|ehepartner\w*|hometown|heimatstadt|grew up|aufgewachsen|"
        r"personal reasons|personliche grunde|moving to|move to)(?![a-z])"
    ),
}

#: No AI-written text may mention pay: the letter's salary sentence is built
#: from the application's own data, and a CV never carries one.
SALARY_WORDS = re.compile(
    _B + r"(salary|salaries|gehalt\w*|verguetung\w*|vergutung\w*|entlohnung|compensation|brutto|"
    r"gross|netto|jahresgehalt|lohn\w*|eur|euro\w*|usd|chf|gbp)(?![a-z])|[€$£]"
)


def claim_groups(text: str) -> dict[str, dict[str, str]]:
    """class code -> {group key -> the word that triggered it}."""
    folded = fold(text)
    found: dict[str, dict[str, str]] = {}
    for code, groups in CLAIM_CLASSES.items():
        for group, pattern in groups.items():
            if match := pattern.search(folded):
                found.setdefault(code, {}).setdefault(group, match.group(0))
    return found


def letter_claims(text: str) -> dict[str, str]:
    """Company-knowledge / personal-circumstance claims -> triggering words."""
    folded = fold(text)
    return {
        code: match.group(0)
        for code, pattern in LETTER_CLAIM_CLASSES.items()
        if (match := pattern.search(folded))
    }


def salary_mention(text: str) -> str | None:
    match = SALARY_WORDS.search(fold(text))
    return match.group(0) if match else None


# --- Support ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Support:
    """Everything a text may rest on: the combined text of the facts it
    cites, in the forms the extractors compare."""

    source: SourceText
    numbers: frozenset[str]
    terms: frozenset[str]
    claim_groups: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def of(cls, texts: list[str]) -> "Support":
        combined = "\n".join(texts)
        numbers: set[str] = set()
        for forms in number_forms(combined).values():
            numbers |= forms
        groups = {code: frozenset(found) for code, found in claim_groups(combined).items()}
        return cls(SourceText(combined), frozenset(numbers), frozenset(term_keys(combined)), groups)

    def has_word(self, token: str) -> bool:
        """Whole-token presence, ignoring case and diacritics. Unlike
        `SourceText.contains_term` this has no substring fallback: "Java"
        is not supported by "JavaScript"."""
        normalized = re.escape(_norm_token(token))
        return re.search(rf"(?<![0-9a-z]){normalized}(?![0-9a-z])", self.source.norm) is not None

    def has_name(self, name: str) -> bool:
        return self.source.contains(name)


def _norm_token(token: str) -> str:
    return SourceText(token).norm


# --- Masking --------------------------------------------------------------------------------

#: Words that make a following job title a *reference to the position* ("the
#: position of X", "application as X", "Stelle als X"), not a claim about the
#: candidate. "I am a X" and "as a X, I ..." are not references.
_REFERENCE_BEFORE = (
    r"(?:position|role|post|job|vacancy|opening|stelle|rolle|funktion|ausschreibung|"
    r"apply(?:ing)?|application|bewerbe\s+mich|bewerbung)"
    r"(?:\s+(?:of|as|for|als|auf|für|:))?\s+(?:the\s+|die\s+|den\s+|der\s+)?"
)
_REFERENCE_AFTER = (
    r"\s+(?:position|role|post|job|vacancy|opening|stelle|rolle|funktion|ausschreibung)"
)
#: A sentence that says the writer worked for the company they are applying to.
PAST_EMPLOYMENT = re.compile(
    r"(?<![a-z])(worked|was employed|employed|previously|formerly|my (?:previous|former)|"
    r"bisher|fruher|ehemalig\w*|arbeitete|war (?:bei|als)|tatig (?:war|bei))(?![a-z])"
)


def mask_job_references(text: str, *, titles: tuple[str, ...], names: tuple[str, ...]) -> str:
    """Blank out the job's own title -- only where it refers to the position --
    and its company and location, so "I am applying for the position of Senior
    Project Manager at Example GmbH in Köln" is not mistaken for claims by the
    candidate, while "I am a Senior Project Manager" still is one."""
    for title in sorted(titles, key=len, reverse=True):
        if len(title.strip()) < 2:
            continue
        quoted = re.escape(title)
        text = re.sub(rf"({_REFERENCE_BEFORE}){quoted}", r"\1 ", text, flags=re.IGNORECASE)
        text = re.sub(rf"{quoted}(?={_REFERENCE_AFTER})", " ", text, flags=re.IGNORECASE)
    for name in sorted(names, key=len, reverse=True):
        if len(name.strip()) >= 2:
            text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    return text


def sentences_naming(text: str, names: tuple[str, ...]) -> list[str]:
    """The sentences of `text` that contain any of `names`."""
    found = []
    for sentence in _SENTENCE_END.split(text):
        lowered = sentence.lower()
        if any(name.lower() in lowered for name in names if name.strip()):
            found.append(sentence)
    return found


# --- Language -----------------------------------------------------------------------------------

_EN_WORDS = frozenset(
    "the and of to for with on at by from as is are be have has had that this these those my "
    "our your their or which who will would can could you we it its into about over under than "
    "more most been being through while also".split()
)
_DE_WORDS = frozenset(
    "der die das und ist sind mit fur von zu zum zur im auf bei nach aus als auch ich wir ihr "
    "ihre ihrer ihrem ihren meine meinen meiner meinem mein mich mir dem den des ein eine einen "
    "einer eines nicht sich oder wird werden hat haben kann konnen uber durch sowie dass".split()
)
#: Below this many marker words a text is too short to tell its language.
_MIN_MARKERS = 8


def language_mismatch(text: str, expected: str) -> bool:
    """True when `text` is clearly in the other of German / English. Short
    or technical text is never flagged: only a clear majority of marker
    words of the wrong language counts."""
    if expected not in ("de", "en"):
        return False
    words = re.findall(r"[a-z]+", fold(text))
    english = sum(1 for word in words if word in _EN_WORDS)
    german = sum(1 for word in words if word in _DE_WORDS)
    wrong, right = (english, german) if expected == "de" else (german, english)
    return wrong >= _MIN_MARKERS and wrong > 2 * right
