"""Location normalization -- explicit vocabulary, no geocoding.

    "Köln"              -> city Cologne, country DE, key "de:cologne"
    "Cologne, Germany"  -> city Cologne, country DE, key "de:cologne"
    "Köln, DE"          -> city Cologne, country DE, key "de:cologne"
    "Bonn"              -> city Bonn,    country DE, key "de:bonn"     (never Cologne)
    "Remote - Germany"  -> remote, country DE,       key "remote:de"
    "Remote"            -> remote,                   key "remote"
    "Springfield, IL"   -> not in the vocabulary,    key "text:springfield il"

Rules (conservative on purpose -- a key that is too specific only costs a
missed duplicate, a key that is too broad could merge different jobs):

- A city is recognized only through `CITIES`, whose aliases are spelled out
  (Köln/Koeln/Cologne). Cities are never merged by proximity.
- A location with any part the vocabulary doesn't know, more than one city,
  or a country that contradicts its city falls back to its folded text.
- Country names are recognized in English and the local language.
  Two-letter codes are only accepted when they confirm the country of a
  recognized city ("Köln, DE"): on their own they are ambiguous with US
  states ("Wilmington, DE" is Delaware).
- "Hybrid" is a work model, not a place, so it doesn't change the key.
  "Remote" does: a remote role and an office role in the same city are
  different postings as far as deduplication is concerned.

The original text is always kept for display; the key is for comparison.
"""

import re
from dataclasses import dataclass

from app.services.job_discovery.normalization.text import fold

# canonical name -> (ISO 3166-1 alpha-2, aliases in folded form)
CITIES: dict[str, tuple[str, frozenset[str]]] = {
    name: (country, frozenset(aliases))
    for name, country, aliases in (
        ("Berlin", "DE", {"berlin"}),
        ("Hamburg", "DE", {"hamburg"}),
        ("Munich", "DE", {"munich", "munchen", "muenchen"}),
        ("Cologne", "DE", {"cologne", "koln", "koeln"}),
        ("Frankfurt am Main", "DE", {"frankfurt", "frankfurt am main", "frankfurt a m",
                                      "frankfurt main"}),
        ("Stuttgart", "DE", {"stuttgart"}),
        ("Düsseldorf", "DE", {"dusseldorf", "duesseldorf"}),
        ("Leipzig", "DE", {"leipzig"}),
        ("Dortmund", "DE", {"dortmund"}),
        ("Essen", "DE", {"essen"}),
        ("Bremen", "DE", {"bremen"}),
        ("Dresden", "DE", {"dresden"}),
        ("Hanover", "DE", {"hannover", "hanover"}),
        ("Nuremberg", "DE", {"nuremberg", "nurnberg", "nuernberg"}),
        ("Bonn", "DE", {"bonn"}),
        ("Karlsruhe", "DE", {"karlsruhe"}),
        ("Mannheim", "DE", {"mannheim"}),
        ("Heidelberg", "DE", {"heidelberg"}),
        ("Walldorf", "DE", {"walldorf"}),
        ("Aachen", "DE", {"aachen"}),
        ("Münster", "DE", {"munster", "muenster"}),
        ("Vienna", "AT", {"vienna", "wien"}),
        ("Graz", "AT", {"graz"}),
        ("Zurich", "CH", {"zurich", "zuerich"}),
        ("Geneva", "CH", {"geneva", "geneve", "genf"}),
        ("Basel", "CH", {"basel", "bale"}),
        ("Bern", "CH", {"bern", "berne"}),
        ("Amsterdam", "NL", {"amsterdam"}),
        ("Rotterdam", "NL", {"rotterdam"}),
        ("The Hague", "NL", {"the hague", "den haag", "s gravenhage"}),
        ("Utrecht", "NL", {"utrecht"}),
        ("Brussels", "BE", {"brussels", "bruxelles", "brussel"}),
        ("Antwerp", "BE", {"antwerp", "antwerpen", "anvers"}),
        ("Paris", "FR", {"paris"}),
        ("Lyon", "FR", {"lyon"}),
        ("Milan", "IT", {"milan", "milano", "mailand"}),
        ("Rome", "IT", {"rome", "roma", "rom"}),
        ("Madrid", "ES", {"madrid"}),
        ("Barcelona", "ES", {"barcelona"}),
        ("Lisbon", "PT", {"lisbon", "lisboa", "lissabon"}),
        ("Porto", "PT", {"porto", "oporto"}),
        ("Prague", "CZ", {"prague", "praha", "prag"}),
        ("Warsaw", "PL", {"warsaw", "warszawa", "warschau"}),
        ("Krakow", "PL", {"krakow", "cracow", "krakau"}),
        ("Copenhagen", "DK", {"copenhagen", "kobenhavn", "kopenhagen"}),
        ("Stockholm", "SE", {"stockholm"}),
        ("Gothenburg", "SE", {"gothenburg", "goteborg"}),
        ("Oslo", "NO", {"oslo"}),
        ("Helsinki", "FI", {"helsinki", "helsingfors"}),
        ("Dublin", "IE", {"dublin"}),
        ("London", "GB", {"london"}),
        ("Manchester", "GB", {"manchester"}),
        ("Edinburgh", "GB", {"edinburgh"}),
        ("Budapest", "HU", {"budapest"}),
        ("Bucharest", "RO", {"bucharest", "bucuresti", "bukarest"}),
        ("Athens", "GR", {"athens", "athina", "athen"}),
        ("New York", "US", {"new york", "new york city", "nyc"}),
        ("San Francisco", "US", {"san francisco"}),
        ("Toronto", "CA", {"toronto"}),
    )
}

# ISO code -> full-name aliases (folded). Codes themselves are handled separately.
COUNTRIES: dict[str, frozenset[str]] = {
    code: frozenset(names)
    for code, names in (
        ("DE", {"germany", "deutschland", "federal republic of germany"}),
        ("AT", {"austria", "osterreich", "oesterreich"}),
        ("CH", {"switzerland", "schweiz", "suisse", "svizzera"}),
        ("NL", {"netherlands", "the netherlands", "nederland", "holland"}),
        ("BE", {"belgium", "belgien", "belgique", "belgie"}),
        ("FR", {"france", "frankreich"}),
        ("IT", {"italy", "italia", "italien"}),
        ("ES", {"spain", "espana", "spanien"}),
        ("PT", {"portugal"}),
        ("CZ", {"czech republic", "czechia", "tschechien", "cesko"}),
        ("PL", {"poland", "polska", "polen"}),
        ("DK", {"denmark", "danmark", "danemark"}),
        ("SE", {"sweden", "sverige", "schweden"}),
        ("NO", {"norway", "norge", "norwegen"}),
        ("FI", {"finland", "suomi", "finnland"}),
        ("IE", {"ireland", "irland", "eire"}),
        ("GB", {"united kingdom", "uk", "great britain", "england", "scotland", "wales",
                "vereinigtes konigreich", "grossbritannien"}),
        ("HU", {"hungary", "ungarn", "magyarorszag"}),
        ("RO", {"romania", "rumanien"}),
        ("GR", {"greece", "griechenland"}),
        ("US", {"united states", "united states of america", "usa", "u s a", "u s"}),
        ("CA", {"canada", "kanada"}),
    )
}

# Sub-national regions that may appear between a known city and its country
# ("Cologne, North Rhine-Westphalia, Germany"). Region -> country.
REGIONS: dict[str, str] = {
    **dict.fromkeys(
        ("nrw", "north rhine westphalia", "nordrhein westfalen", "bavaria", "bayern",
         "hesse", "hessen", "baden wurttemberg", "baden wuerttemberg", "lower saxony",
         "niedersachsen", "saxony", "sachsen", "rhineland palatinate", "rheinland pfalz"),
        "DE",
    ),
}

_CITY_BY_ALIAS: dict[str, str] = {
    alias: name for name, (_, aliases) in CITIES.items() for alias in aliases
}
_COUNTRY_BY_NAME: dict[str, str] = {
    name: code for code, names in COUNTRIES.items() for name in names
}
_COUNTRY_CODES = frozenset(COUNTRIES)

_REMOTE_RE = re.compile(
    r"\b(?:100\s*%?\s*|fully\s+|full\s+)?remote(?:ly)?\b(?:\s+first)?|\bhome\s*office\b"
    r"|\bwork\s+from\s+home\b|\bwfh\b|\bmobiles\s+arbeiten\b",
)
_HYBRID_RE = re.compile(r"\bhybrid(?:\s+work(?:ing)?)?\b")
_ANYWHERE_RE = re.compile(r"\b(?:anywhere|worldwide|global(?:ly)?|weltweit)\b")
# Segments: commas, slashes, pipes, semicolons, parentheses, or a *spaced*
# dash ("Remote - Germany"; "Baden-Württemberg" stays one segment).
_SEGMENT_RE = re.compile(r"\s*[,/|;()\[\]]\s*|\s+[-–—]\s+")

# Shared with the V0.5.1 location taxonomy (app.services.taxonomy.locations),
# which builds on these rules and tables without changing V0.4's keys.
REMOTE_PATTERN = _REMOTE_RE
HYBRID_PATTERN = _HYBRID_RE
ANYWHERE_PATTERN = _ANYWHERE_RE
SEGMENT_PATTERN = _SEGMENT_RE


@dataclass(frozen=True)
class NormalizedLocation:
    original: str
    #: Canonical display name from the vocabulary, e.g. "Cologne".
    city: str | None
    #: ISO 3166-1 alpha-2.
    country: str | None
    is_remote: bool
    is_hybrid: bool
    #: True when every part of the text was understood.
    recognized: bool
    #: Comparison key (see module docs).
    key: str


def _country_of_segment(segment: str, *, allow_code: bool) -> str | None:
    if segment in _COUNTRY_BY_NAME:
        return _COUNTRY_BY_NAME[segment]
    code = segment.upper()
    if allow_code and len(segment) == 2 and code in _COUNTRY_CODES:
        return code
    if allow_code and code in {"DEU", "AUT", "CHE", "NLD", "FRA", "ESP", "ITA", "POL", "GBR",
                               "USA"}:
        return {"DEU": "DE", "AUT": "AT", "CHE": "CH", "NLD": "NL", "FRA": "FR", "ESP": "ES",
                "ITA": "IT", "POL": "PL", "GBR": "GB", "USA": "US"}[code]
    return None


def normalize_location(text: str | None) -> NormalizedLocation | None:
    """Parse one location string (see module docs). None for empty input."""
    if not text or not text.strip():
        return None
    original = " ".join(text.split())
    lowered = original.casefold()
    is_remote = bool(_REMOTE_RE.search(lowered))
    is_hybrid = bool(_HYBRID_RE.search(lowered))
    stripped = _HYBRID_RE.sub(" ", _REMOTE_RE.sub(" ", lowered))
    if is_remote:
        stripped = _ANYWHERE_RE.sub(" ", stripped)

    segments = [fold(part) for part in _SEGMENT_RE.split(stripped)]
    segments = [s for s in segments if s and s not in {"and", "or", "only", "work", "office"}]

    cities: list[str] = []
    countries: list[str] = []
    regions: list[str] = []
    codes: list[str] = []
    unknown: list[str] = []
    for segment in segments:
        if segment in _CITY_BY_ALIAS:
            cities.append(_CITY_BY_ALIAS[segment])
        elif (named := _country_of_segment(segment, allow_code=False)) is not None:
            countries.append(named)
        elif segment in REGIONS:
            regions.append(REGIONS[segment])
        elif _country_of_segment(segment, allow_code=True) is not None:
            codes.append(segment)
        else:
            unknown.append(segment)

    city = cities[0] if len(set(cities)) == 1 else None
    country: str | None = None
    recognized = not unknown and len(set(cities)) <= 1
    if city is not None:
        country = CITIES[city][0]
        stated = set(countries) | set(regions)
        stated |= {_country_of_segment(c, allow_code=True) or "" for c in codes}
        if stated - {country}:
            recognized = False  # e.g. "Cologne, US": not the Cologne we know
    else:
        if codes or regions:
            recognized = False  # a bare code or region is ambiguous
        if len(set(countries)) == 1:
            country = countries[0]
        elif countries:
            recognized = False

    if not recognized:
        city, country = None, None
        key = f"text:{fold(original)}"
    elif city is not None:
        key = f"{country.lower()}:{fold(city)}" if country else f"city:{fold(city)}"
        if is_remote:
            key = f"remote:{key}"
    elif country is not None:
        key = f"remote:{country.lower()}" if is_remote else country.lower()
    elif is_remote:
        key = "remote"
    else:
        # Only words like "Hybrid": nothing that identifies a place.
        key = f"text:{fold(original)}"
        recognized = False

    return NormalizedLocation(
        original=original,
        city=city,
        country=country,
        is_remote=is_remote,
        is_hybrid=is_hybrid,
        recognized=recognized,
        key=key,
    )


def location_key(text: str | None) -> str | None:
    location = normalize_location(text)
    return location.key if location else None
