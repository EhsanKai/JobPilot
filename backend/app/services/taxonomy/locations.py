"""Location vocabulary (V0.5.1): cities, regions, countries, remote.

Vocabulary normalization only -- no geocoding, coordinates, distances or
maps APIs. Keys are stable and country-qualified:

    country:de             Germany
    region:us:illinois     Illinois
    city:de:cologne        Cologne
    city:us:aurora_illinois / city:us:aurora_colorado   (same name, two cities)
    remote

It extends V0.4's location vocabulary (`job_discovery.normalization.
location`: its city and country tables, folding and remote rules) with the
forms the V0.4.1 live data showed but V0.4 left unrecognized: "City,
Region, Country" (regions and their postal abbreviations), ISO alpha-3
country codes ("DEU"), and the cities that recur in the real postings.
V0.4's own tables are not changed, so its deduplication keys stay exactly
as they were.

Parsing is positional and conservative:

- The first part may be a city; every further part must be a region or
  country consistent with it ("Aurora, IL" picks Aurora, Illinois). Any
  unrecognized part makes the whole location UNKNOWN ("Brooklyn, New York"
  is not New York City; "Lake Zurich, Illinois" is not Zurich).
- A city name that is ambiguous on its own ("Perth") is AMBIGUOUS without
  a country; one whose namesakes share a country ("Aurora", "Newark") is
  AMBIGUOUS without a region. A bare two-letter code ("DE":
  Germany or Delaware?) is AMBIGUOUS; alpha-3 codes are not.
- Several locations ("A; B") are normalized one by one with
  `normalize_location_list`.
"""

import re

from app.services.job_discovery.normalization.location import (
    ANYWHERE_PATTERN,
    CITIES,
    COUNTRIES,
    HYBRID_PATTERN,
    REMOTE_PATTERN,
    SEGMENT_PATTERN,
)
from app.services.job_discovery.normalization.text import clean_text, fold
from app.services.taxonomy.model import (
    Alias,
    AliasForm,
    Category,
    Entry,
    NormalizationResult,
    Vocabulary,
)
from app.services.taxonomy.versions import TAXONOMY_VERSION, VOCABULARY_VERSIONS

_KEY = re.compile(r"^(?:remote|country:[a-z]{2}|(?:region|city):[a-z]{2}:[a-z0-9_]+)$")
_TWO_LETTER = "two-letter code: needs a city for context (also e.g. a US state abbreviation)"
_POSTAL = "postal abbreviation: needs a city for context"

# ISO 3166-1 alpha-3 codes (unambiguous on their own, observed as "DEU", ...).
_ALPHA3 = {
    "DE": "DEU", "AT": "AUT", "CH": "CHE", "NL": "NLD", "BE": "BEL", "FR": "FRA", "IT": "ITA",
    "ES": "ESP", "PT": "PRT", "CZ": "CZE", "PL": "POL", "DK": "DNK", "SE": "SWE", "NO": "NOR",
    "FI": "FIN", "IE": "IRL", "GB": "GBR", "HU": "HUN", "RO": "ROU", "GR": "GRC", "US": "USA",
    "CA": "CAN", "PH": "PHL", "AU": "AUS", "NZ": "NZL", "MX": "MEX", "JP": "JPN", "TH": "THA",
}

# Countries V0.4 doesn't know that the real postings use, and extra names.
_EXTRA_COUNTRIES: dict[str, tuple[str, ...]] = {
    "PH": ("philippines",),
    "AU": ("australia",),
    "NZ": ("new zealand",),
    "MX": ("mexico",),
    "JP": ("japan",),
    "TH": ("thailand",),
}
_EXTRA_COUNTRY_NAMES: dict[str, tuple[str, ...]] = {"NL": ("niederlande",)}

_COUNTRY_LABELS = {
    "DE": "Germany", "AT": "Austria", "CH": "Switzerland", "NL": "Netherlands",
    "BE": "Belgium", "FR": "France", "IT": "Italy", "ES": "Spain", "PT": "Portugal",
    "CZ": "Czechia", "PL": "Poland", "DK": "Denmark", "SE": "Sweden", "NO": "Norway",
    "FI": "Finland", "IE": "Ireland", "GB": "United Kingdom", "HU": "Hungary",
    "RO": "Romania", "GR": "Greece", "US": "United States", "CA": "Canada",
    "PH": "Philippines", "AU": "Australia", "NZ": "New Zealand", "MX": "Mexico",
    "JP": "Japan", "TH": "Thailand",
}

# (slug, country, label, aliases, postal codes)
_REGIONS: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("berlin", "DE", "Berlin", (), ()),
    ("hamburg", "DE", "Hamburg", (), ()),
    ("lower_saxony", "DE", "Lower Saxony", ("niedersachsen",), ()),
    ("saxony_anhalt", "DE", "Saxony-Anhalt", ("sachsen anhalt",), ()),
    ("bavaria", "DE", "Bavaria", ("bayern",), ()),
    ("north_rhine_westphalia", "DE", "North Rhine-Westphalia", ("nordrhein westfalen", "nrw"), ()),
    ("hesse", "DE", "Hesse", ("hessen",), ()),
    ("baden_wurttemberg", "DE", "Baden-Württemberg", ("baden wuerttemberg",), ()),
    ("saxony", "DE", "Saxony", ("sachsen",), ()),
    ("rhineland_palatinate", "DE", "Rhineland-Palatinate", ("rheinland pfalz",), ()),
    ("ontario", "CA", "Ontario", (), ("on",)),
    ("alberta", "CA", "Alberta", (), ("ab",)),
    ("british_columbia", "CA", "British Columbia", (), ("bc",)),
    ("illinois", "US", "Illinois", (), ("il",)),
    ("colorado", "US", "Colorado", (), ("co",)),
    ("arizona", "US", "Arizona", (), ("az",)),
    ("new_jersey", "US", "New Jersey", (), ("nj",)),
    ("texas", "US", "Texas", (), ("tx",)),
    ("new_york", "US", "New York", (), ("ny",)),
    ("michigan", "US", "Michigan", (), ("mi",)),
    ("masovian", "PL", "Masovian Voivodeship", ("mazowieckie", "masovia"), ()),
    ("new_south_wales", "AU", "New South Wales", (), ("nsw",)),
    ("queensland", "AU", "Queensland", (), ("qld",)),
    ("western_australia", "AU", "Western Australia", (), ("wa",)),
    ("north_holland", "NL", "North Holland", ("noord holland",), ()),
    ("south_holland", "NL", "South Holland", ("zuid holland", "provinz südholland"), ()),
    ("essonne", "FR", "Essonne", (), ()),
    ("paris", "FR", "Paris", (), ()),
    ("county_dublin", "IE", "County Dublin", ("dublin",), ()),
    ("metro_manila", "PH", "Metro Manila", ("manila",), ()),
    ("auckland_region", "NZ", "Auckland Region", ("auckland",), ()),
    ("ostfold", "NO", "Østfold", (), ()),
    ("oslo", "NO", "Oslo", (), ()),
    ("skane", "SE", "Skåne", (), ()),
    ("east_flanders", "BE", "East Flanders", ("oost vlaanderen",), ()),
    ("saarland", "DE", "Saarland", (), ()),
    ("missouri", "US", "Missouri", (), ("mo",)),
    ("victoria", "AU", "Victoria", (), ("vic",)),
    ("capital_region_denmark", "DK", "Capital Region of Denmark", ("region hovedstaden",), ()),
)

# (slug, country, label, region slug, ambiguity of the bare name, extra aliases).
# An ambiguous name is resolved by a stated country -- or, for the cities in
# `_NEEDS_REGION` (namesakes in the same country), only by a stated region.
_EXTRA_CITIES: tuple[tuple[str, str, str, str | None, str | None, tuple[str, ...]], ...] = (
    ("mississauga", "CA", "Mississauga", "ontario", None, ()),
    ("calgary", "CA", "Calgary", "alberta", None, ()),
    ("edmonton", "CA", "Edmonton", "alberta", None, ()),
    ("abbotsford", "CA", "Abbotsford", "british_columbia",
     "also towns in England and Australia", ()),
    ("aurora_illinois", "US", "Aurora", "illinois",
     "several US cities are named Aurora (e.g. Illinois, Colorado)", ()),
    ("aurora_colorado", "US", "Aurora", "colorado",
     "several US cities are named Aurora (e.g. Illinois, Colorado)", ()),
    ("goodyear", "US", "Goodyear", "arizona", None, ()),
    ("newark", "US", "Newark", "new_jersey",
     "also Newark, Delaware and Newark-on-Trent (England)", ()),
    ("irving", "US", "Irving", "texas", None, ()),
    ("phoenix", "US", "Phoenix", "arizona", None, ()),
    ("joliet", "US", "Joliet", "illinois", None, ()),
    ("schiller_park", "US", "Schiller Park", "illinois", None, ()),
    ("burr_ridge", "US", "Burr Ridge", "illinois", None, ()),
    ("lake_zurich", "US", "Lake Zurich", "illinois", None, ()),
    ("boulder", "US", "Boulder", "colorado", None, ()),
    ("detroit", "US", "Detroit", "michigan", None, ()),
    ("chicago", "US", "Chicago", "illinois", None, ()),
    ("dallas", "US", "Dallas", "texas", None, ()),
    ("st_louis", "US", "St. Louis", "missouri", None, ("saint louis",)),
    ("fairfield", "US", "Fairfield", "new_jersey", "several US cities are named Fairfield", ()),
    ("verden", "DE", "Verden", "lower_saxony", None, ()),
    ("magdeburg", "DE", "Magdeburg", "saxony_anhalt", None, ()),
    ("barleben", "DE", "Barleben", "saxony_anhalt", None, ()),
    ("derby", "GB", "Derby", None, "also cities in the US and Australia", ()),
    ("manila", "PH", "Manila", "metro_manila", None, ()),
    ("sydney", "AU", "Sydney", "new_south_wales", None, ()),
    ("brisbane", "AU", "Brisbane", "queensland", None, ()),
    ("perth", "AU", "Perth", "western_australia", "also Perth, Scotland", ()),
    ("melbourne", "AU", "Melbourne", "victoria", "also Melbourne, Florida", ()),
    ("auckland", "NZ", "Auckland", "auckland_region", None, ()),
    ("bleiswijk", "NL", "Bleiswijk", "south_holland", None, ()),
    ("lisses", "FR", "Lisses", "essonne", None, ()),
    ("ghent", "BE", "Ghent", "east_flanders", None, ("gent",)),
    ("saarbrucken", "DE", "Saarbrücken", "saarland", None, ()),
    ("bjuv", "SE", "Bjuv", "skane", None, ()),
    ("mexico_city", "MX", "Mexico City", None, None, ("ciudad de mexico",)),
    ("tokyo", "JP", "Tokyo", None, None, ()),
    ("bangkok", "TH", "Bangkok", None, None, ()),
    ("moss", "NO", "Moss", "ostfold", "also an ordinary English word", ()),
)


_NEEDS_REGION = frozenset({"aurora_illinois", "aurora_colorado", "newark", "fairfield"})


def _slug(name: str) -> str:
    return fold(name).replace(" ", "_")


def _entries() -> tuple[Entry, ...]:
    entries: list[Entry] = [Entry("remote", "Remote", "remote")]
    for code in sorted({*COUNTRIES, *_EXTRA_COUNTRIES}):
        names = (*COUNTRIES.get(code, ()), *_EXTRA_COUNTRIES.get(code, ()))
        names = (*names, *_EXTRA_COUNTRY_NAMES.get(code, ()))
        entries.append(
            Entry(
                f"country:{code.lower()}",
                _COUNTRY_LABELS[code],
                "country",
                (*sorted(names), _ALPHA3[code], Alias(code, _TWO_LETTER)),
                (("country", code),),
            )
        )
    for slug, code, label, aliases, codes in _REGIONS:
        entries.append(
            Entry(
                f"region:{code.lower()}:{slug}",
                label,
                "region",
                (*aliases, *(Alias(c, _POSTAL) for c in codes)),
                (("country", code),),
            )
        )
    for name, (code, city_aliases) in CITIES.items():
        entries.append(
            Entry(
                f"city:{code.lower()}:{_slug(name)}",
                name,
                "city",
                tuple(sorted(city_aliases)),
                (("country", code),),
            )
        )
    for slug, code, label, region, ambiguity, extra in _EXTRA_CITIES:
        attributes = [("country", code)]
        if region:
            attributes.append(("region", f"region:{code.lower()}:{region}"))
        if slug in _NEEDS_REGION:
            attributes.append(("resolved_by", "region"))
        entries.append(
            Entry(
                f"city:{code.lower()}:{slug}",
                label,
                "city",
                (Alias(label, ambiguity), *extra),
                tuple(attributes),
            )
        )
    return tuple(entries)


LOCATIONS = Vocabulary(
    name="locations",
    version=VOCABULARY_VERSIONS["locations"],
    taxonomy_version=TAXONOMY_VERSION,
    categories=(
        Category("city", "City"),
        Category("region", "Region"),
        Category("country", "Country"),
        Category("remote", "Remote"),
    ),
    entries=_entries(),
    key_pattern=_KEY,
    allow_cross_category_aliases=True,
)

_FILLER = frozenset({"and", "or", "only", "work", "office"})
_PLACE = ("region", "country")


def normalize_location(text: str | None) -> NormalizationResult | None:
    """Normalize one location (see module docs). None for missing input."""
    original = clean_text(text)
    if original is None:
        return None
    if ";" in original:
        return LOCATIONS.unknown(original, "multiple_locations")

    lowered = original.casefold()
    flags: dict[str, str] = {}
    if REMOTE_PATTERN.search(lowered):
        flags["remote"] = "true"
    if HYBRID_PATTERN.search(lowered):
        flags["hybrid"] = "true"
    stripped = HYBRID_PATTERN.sub(" ", REMOTE_PATTERN.sub(" ", lowered))
    if "remote" in flags:
        stripped = ANYWHERE_PATTERN.sub(" ", stripped)
    segments = [fold(part) for part in SEGMENT_PATTERN.split(stripped)]
    segments = [s for s in segments if s and s not in _FILLER]

    if not segments:
        if "remote" in flags:
            return LOCATIONS.match(original, LOCATIONS.entry("remote"), "remote", flags)
        return LOCATIONS.unknown(original, "no_place", flags)

    first, rest = segments[0], segments[1:]
    rest_forms = [LOCATIONS.forms(segment, _PLACE) for segment in rest]
    if any(not forms for forms in rest_forms):
        return LOCATIONS.unknown(original, "unknown_part", flags)

    if cities := LOCATIONS.forms(first, ("city",)):
        return _resolve(original, first, cities, rest_forms, flags)
    places = [f for f in LOCATIONS.forms(first, _PLACE) if f.ambiguity is None]
    if places:
        return _resolve(original, first, places, rest_forms, flags)
    if bare := LOCATIONS.forms(first, _PLACE):
        if not rest:
            return LOCATIONS.ambiguous(original, [f.entry for f in bare], "bare_code", flags)
    return LOCATIONS.unknown(original, "not_in_vocabulary", flags)


def normalize_location_list(text: str | None) -> list[NormalizationResult]:
    """Normalize "A; B; C" location lists, one result per location."""
    if text is None:
        return []
    results = (normalize_location(part) for part in text.split(";"))
    return [result for result in results if result is not None]


def normalize_country(text: str | None) -> NormalizationResult | None:
    """Normalize a value known to be a country (e.g. a country field): there
    a two-letter code is unambiguous. None for missing input."""
    original = clean_text(text)
    if original is None:
        return None
    forms = LOCATIONS.forms(fold(original), ("country",))
    if len(forms) == 1:
        return LOCATIONS.match(original, forms[0].entry, fold(original))
    return LOCATIONS.unknown(original, "not_in_vocabulary")


def _resolve(
    original: str,
    alias: str,
    candidates: list[AliasForm],
    rest_forms: list[list[AliasForm]],
    flags: dict[str, str],
) -> NormalizationResult:
    """Keep the candidates every further part is consistent with."""
    consistent = [
        form
        for form in candidates
        if all(any(_fits(form.entry, part.entry) for part in forms) for forms in rest_forms)
    ]
    if not consistent:
        return LOCATIONS.unknown(original, "inconsistent_parts", flags)
    entries = {form.entry.key: form.entry for form in consistent}
    needs_context = any(
        form.ambiguity and not _resolved(form.entry, rest_forms) for form in consistent
    )
    if len(entries) > 1 or needs_context:
        notes = sorted({form.ambiguity for form in consistent if form.ambiguity})
        details = {**flags, **({"ambiguity": "; ".join(notes)} if notes else {})}
        reason = "alias_needs_context" if notes else "several_entries"
        return LOCATIONS.ambiguous(original, entries.values(), reason, details)
    entry = next(iter(entries.values()))
    details = {**flags, "country": entry.attribute("country") or ""}
    region = entry.attribute("region") or _stated_region(entry, rest_forms)
    if region:
        details["region"] = region
    return LOCATIONS.match(original, entry, alias, details)


def _fits(place: Entry, part: Entry) -> bool:
    """Is `part` (a region or country) consistent with `place`?"""
    if part.attribute("country") != place.attribute("country"):
        return False
    if part.category == "region" and place.category == "city":
        return place.attribute("region") in (None, part.key)
    if part.category == "region" and place.category == "region":
        return part.key == place.key
    if part.category == "country":
        return True
    return False


def _resolved(place: Entry, rest_forms: list[list[AliasForm]]) -> bool:
    """Does the context settle an ambiguous name? A stated country does,
    unless the name's namesakes share that country -- then only a region."""
    if place.attribute("resolved_by") == "region":
        return _stated_region(place, rest_forms) is not None
    return bool(rest_forms)


def _stated_region(place: Entry, rest_forms: list[list[AliasForm]]) -> str | None:
    if place.category != "city":
        return place.key if place.category == "region" else None
    for forms in rest_forms:
        for form in forms:
            if form.entry.category == "region" and _fits(place, form.entry):
                return form.entry.key
    return None
