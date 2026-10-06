"""Text normalization.

Display cleaning (`clean_text`, `clean_multiline`, `clean_list`) keeps the
source's wording and only removes formatting noise. Comparison folding
(`fold`, `tokens`, `title_key`) additionally lowercases, strips diacritics
and punctuation so harmless formatting differences disappear -- but never
removes a meaningful word: "Senior SAP Developer" and "SAP Developer" keep
different keys.
"""

import html
import re
import unicodedata

MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_CHARS = 1000

# Characters that are formatting, not content.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
# Letters NFKD can't decompose into base letter + accent.
_LETTER_FOLDS = str.maketrans(
    {"ø": "o", "æ": "ae", "œ": "oe", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "ı": "i"}
)
# Leftover markup in text that was supposed to be plain (e.g. "<br>").
_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]{0,500}>")
# Hyphen and dash variants, slashes and similar separators.
_SEPARATOR_RE = re.compile(r"[\-‐-―−_/\\|·•]+")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#]*")

# German job titles routinely carry a gender marker, e.g. "(m/w/d)". It is
# legally required formatting, not part of the role.
_GENDER_MARKER_RE = re.compile(
    r"[(\[]\s*(?:all\s+genders?|alle\s+geschlechter|gn\*?|"
    r"[mwfdx]\s*[/|,]\s*[mwfdx](?:\s*[/|,]\s*(?:[mwfdx]|div|divers))?)\s*[)\]]"
    r"|(?:^|\s)[-–]?\s*[mwfd]\s*/\s*[mwfd]\s*/\s*(?:[mwfdx]|div|divers)\s*$",
    re.IGNORECASE,
)
# Abbreviations expanded in title keys only ("Sr. Engineer" == "Senior Engineer").
_TITLE_ABBREVIATIONS = {"sr": "senior", "snr": "senior", "jr": "junior", "jnr": "junior"}


# --- Display cleaning -------------------------------------------------------------


def clean_text(value: object, *, max_chars: int | None = None) -> str | None:
    """Single-line text: collapse all whitespace; empty -> None."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("\xa0", " ").translate(_INVISIBLE).split())
    if not text:
        return None
    if max_chars is not None and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def clean_multiline(value: object, *, max_chars: int) -> str | None:
    """Multi-line text: trim each line, collapse runs of spaces and of blank
    lines, keep paragraph structure; empty -> None."""
    if not isinstance(value, str):
        return None
    value = value.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE)
    lines = [" ".join(line.replace("\xa0", " ").split()) for line in value.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        return None
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def clean_list(values: object, *, max_items: int = MAX_LIST_ITEMS) -> list[str]:
    """Clean each entry, drop empties and case-insensitive duplicates."""
    if not isinstance(values, list | tuple):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value, max_chars=MAX_LIST_ITEM_CHARS)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
        if len(result) >= max_items:
            break
    return result


# --- Comparison folding -----------------------------------------------------------


def strip_html_artifacts(text: str) -> str:
    """Decode entities ("&amp;", "&nbsp;") and drop leftover tags."""
    return _TAG_RE.sub(" ", html.unescape(text))


def fold(text: str | None) -> str:
    """The comparison form of `text`.

    HTML artifacts removed, Unicode compatibility-normalized (NFKC: full-width
    letters, ligatures, ...), case-folded, diacritics removed ("Köln" ->
    "koln"), "&" read as "and", hyphens/dashes/slashes and other punctuation
    turned into spaces, whitespace collapsed. "+" and "#" survive inside
    words so "C++" and "C#" stay distinct from "C".
    """
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", strip_html_artifacts(text)).casefold()
    value = value.translate(_INVISIBLE).translate(_LETTER_FOLDS)
    value = "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    )
    value = value.replace("&", " and ")
    value = _SEPARATOR_RE.sub(" ", value)
    return " ".join(_TOKEN_RE.findall(value))


def tokens(text: str | None) -> list[str]:
    """Folded word tokens, in order."""
    folded = fold(text)
    return folded.split() if folded else []


def strip_gender_marker(title: str) -> str:
    return " ".join(_GENDER_MARKER_RE.sub(" ", title).split())


def title_key(title: str | None) -> str:
    """Comparison key for a job title.

    "Senior SAP ABAP-Developer (m/w/d)", "senior sap abap developer" and
    "Sr. SAP ABAP Developer" share a key. Words are never dropped (apart from
    the gender marker), so "Senior SAP Developer" != "SAP Developer".
    """
    if not title:
        return ""
    words = tokens(strip_gender_marker(title))
    return " ".join(_TITLE_ABBREVIATIONS.get(word, word) for word in words)
