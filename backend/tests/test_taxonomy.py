"""V0.5.1 taxonomy: the generic mechanism, versioning, every vocabulary, and
its integration with the V0.5.0 candidate snapshot document.

Real-data regressions are in test_taxonomy_real_data.py.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.candidate_profile import CandidateProfileDocument
from app.services.job_discovery.normalization.location import location_key
from app.services.taxonomy import (
    TAXONOMY_VERSION,
    VOCABULARIES,
    VOCABULARY_VERSIONS,
    NormalizationStatus,
    normalize_country,
    normalize_education_level,
    normalize_language,
    normalize_language_level,
    normalize_location,
    normalize_location_list,
    normalize_skill,
    normalize_title,
)
from app.services.taxonomy.education import EDUCATION_LEVEL_VOCABULARY, EDUCATION_LEVELS
from app.services.taxonomy.languages import (
    CEFR_ORDER,
    LANGUAGE_LEVEL_VOCABULARY,
    LANGUAGE_LEVELS,
)
from app.services.taxonomy.model import (
    Alias,
    AmbiguousTerm,
    Category,
    Entry,
    Vocabulary,
    VocabularyError,
)
from app.services.taxonomy.skills import SKILLS
from app.services.taxonomy.versions import TAXONOMY_MANIFESTS

MATCH = NormalizationStatus.MATCH
UNKNOWN = NormalizationStatus.UNKNOWN
AMBIGUOUS = NormalizationStatus.AMBIGUOUS


def toy(**overrides: Any) -> Vocabulary:
    values: dict[str, Any] = {
        "name": "toy",
        "version": "toy_v1",
        "taxonomy_version": "taxonomy_test",
        "categories": (Category("thing", "Thing"),),
        "entries": (Entry("alpha", "Alpha", "thing", ("a1",)),),
    }
    values.update(overrides)
    return Vocabulary(**values)


# --- Generic mechanism -------------------------------------------------------------


def test_match_result_carries_key_label_versions_and_alias() -> None:
    result = normalize_skill("  postgres ")
    assert result is not None
    assert result.status is MATCH and result.is_match
    assert (result.key, result.label, result.category) == ("postgresql", "PostgreSQL", "database")
    assert result.matched_alias == "postgres"
    assert result.input == "  postgres "  # preserved as given
    assert (result.vocabulary, result.vocabulary_version) == ("skills", "skills_v1")
    assert result.taxonomy_version == TAXONOMY_VERSION
    assert result.candidates == () and result.reason is None


def test_unknown_preserves_input_and_invents_nothing() -> None:
    result = normalize_skill("Underwater Basket Weaving")
    assert result is not None and result.status is UNKNOWN
    assert result.input == "Underwater Basket Weaving"
    assert result.key is None and result.label is None and result.candidates == ()
    assert result.reason == "not_in_vocabulary"


def test_ambiguous_exposes_candidates_and_chooses_none() -> None:
    result = normalize_skill("CPA")
    assert result is not None and result.status is AMBIGUOUS
    assert result.key is None
    assert [c.key for c in result.candidates] == ["certified_public_accountant"]
    assert result.reason == "alias_needs_context"
    assert "cost per acquisition" in (result.detail("ambiguity") or "")


def test_missing_input_is_not_a_result() -> None:
    for normalize in (normalize_skill, normalize_language, normalize_language_level,
                      normalize_education_level, normalize_title, normalize_location,
                      normalize_country):
        assert normalize(None) is None
        assert normalize("   ") is None


def test_punctuation_only_input_is_unknown() -> None:
    result = normalize_skill("!!!")
    assert result is not None and result.status is UNKNOWN
    assert result.reason == "no_comparable_text"


@pytest.mark.parametrize(
    "variant", ["CI/CD", "ci/cd", "CI-CD", "ci cd", "  CI / CD ", "Ci/Cd"]
)
def test_case_whitespace_and_punctuation_tolerance(variant: str) -> None:
    result = normalize_skill(variant)
    assert result is not None and result.key == "ci_cd"


def test_normalization_is_deterministic() -> None:
    assert normalize_skill("JS") == normalize_skill("JS")
    assert normalize_location("Aurora") == normalize_location("Aurora")
    assert all(v.fingerprint() == v.fingerprint() for v in VOCABULARIES)


def test_categories_are_data_not_code() -> None:
    # A domain the seed doesn't cover is added purely as data.
    vocabulary = toy(
        categories=(Category("nursing", "Nursing"),),
        entries=(Entry("wound_care", "Wound care", "nursing", ("wound management",)),),
    )
    result = vocabulary.lookup("Wound Management")
    assert result.is_match and result.category == "nursing"
    # The real skill taxonomy already registers categories with no seed yet.
    assert "healthcare" in {c.key for c in SKILLS.categories}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"entries": (Entry("Bad Key", "X", "thing"),)}, "invalid key"),
        ({"entries": (Entry("a", "A", "thing"), Entry("a", "B", "thing"))}, "duplicate key"),
        ({"entries": (Entry("a", "A", "elsewhere"),)}, "unregistered category"),
        (
            {"entries": (Entry("a", "A", "thing", ("x",)), Entry("b", "B", "thing", ("x",)))},
            "not declared ambiguous",
        ),
        ({"ambiguous_terms": (AmbiguousTerm("a1", "?"),)}, "also an alias"),
        ({"ambiguous_terms": (AmbiguousTerm("zz", "?", ("nope",)),)}, "unknown candidate"),
        ({"entries": (Entry("a", "A", "thing", ("!!",)),)}, "no comparable text"),
        ({"categories": (Category("thing", "T"), Category("thing", "T"))}, "duplicate category"),
        ({"version": ""}, "needs a name, a version"),
    ],
)
def test_inconsistent_vocabulary_data_is_rejected(overrides: dict, message: str) -> None:
    with pytest.raises(VocabularyError, match=message):
        toy(**overrides)


def test_shared_alias_must_be_declared_and_then_is_ambiguous() -> None:
    vocabulary = toy(
        entries=(
            Entry("a", "A", "thing", (Alias("x", "means a or b"),)),
            Entry("b", "B", "thing", (Alias("x", "means a or b"),)),
        )
    )
    result = vocabulary.lookup("x")
    assert result.status is AMBIGUOUS
    assert [c.key for c in result.candidates] == ["a", "b"]


def test_cross_category_aliases_need_opt_in_and_category_filters_resolve() -> None:
    entries = (Entry("city", "Berlin", "city"), Entry("state", "Berlin", "region"))
    categories = (Category("city", "City"), Category("region", "Region"))
    with pytest.raises(VocabularyError):
        toy(categories=categories, entries=entries)
    vocabulary = toy(categories=categories, entries=entries, allow_cross_category_aliases=True)
    assert vocabulary.lookup("berlin").status is AMBIGUOUS
    assert vocabulary.lookup("berlin", categories=["region"]).key == "state"


# --- Versioning ------------------------------------------------------------------------

# Pinned meaning of each vocabulary version. If this fails you changed a
# vocabulary: bump its version in app/services/taxonomy/versions.py (and
# TAXONOMY_VERSION, adding a new manifest), then record the new fingerprint
# here. Never edit an existing pin.
PINNED_FINGERPRINTS = {
    ("skills", "skills_v1"): (
        "b9ce4b9738d24dc9492df69be41781f1"
        "78e0be19551de2dbda6718406f3da476"
    ),
    ("languages", "languages_v1"): (
        "6e65418bb6021b8717c629fd920accc4"
        "3429014f68385906798eb220251efcaf"
    ),
    ("language_levels", "language_levels_v1"): (
        "2ae21c17a0abedcdcd26c18e30162701"
        "274163359ebc18021d71e1668346beb9"
    ),
    ("education_levels", "education_levels_v1"): (
        "031537649cc2986b903b7242d28e5460"
        "c4d61d400ddc3939ab99394b947d4d9f"
    ),
    ("job_titles", "job_titles_v1"): (
        "04de9a2b0bf3d93efb72e74c1931b684"
        "ec10f1a00b6bcdacbf313f45b0d8cf00"
    ),
    ("locations", "locations_v1"): (
        "d9cb22ef9d52e6453453275826eb5e6f"
        "1615a652a85ec0e1ccdad11fd52c262c"
    ),
}


@pytest.mark.parametrize("vocabulary", VOCABULARIES, ids=lambda v: v.name)
def test_vocabulary_meaning_is_pinned_to_its_version(vocabulary: Vocabulary) -> None:
    pinned = PINNED_FINGERPRINTS.get((vocabulary.name, vocabulary.version))
    assert pinned is not None, f"no pinned fingerprint for {vocabulary.version}"
    assert vocabulary.fingerprint() == pinned, (
        f"{vocabulary.name} changed without a version bump (fingerprint "
        f"{vocabulary.fingerprint()})"
    )


def test_taxonomy_version_names_the_vocabulary_versions() -> None:
    assert VOCABULARY_VERSIONS == TAXONOMY_MANIFESTS[TAXONOMY_VERSION]
    assert {v.name: v.version for v in VOCABULARIES} == VOCABULARY_VERSIONS
    assert {v.taxonomy_version for v in VOCABULARIES} == {TAXONOMY_VERSION}


def test_fingerprint_ignores_the_version_but_not_the_meaning() -> None:
    base = toy()
    assert toy(version="toy_v2").fingerprint() == base.fingerprint()
    assert toy(entries=(Entry("alpha", "Alpha", "thing", ("a2",)),)).fingerprint() != (
        base.fingerprint()
    )


# --- Skills ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "key"),
    [
        ("Python", "python"),
        ("JavaScript", "javascript"),
        ("JS", "javascript"),
        ("Java Script", "javascript"),
        ("Java", "java"),
        ("PostgreSQL", "postgresql"),
        ("Postgres", "postgresql"),
        ("React", "react"),
        ("React.js", "react"),
        ("ReactJS", "react"),
        ("React Native", "react_native"),
        ("Golang", "go"),
        ("K8s", "kubernetes"),
        ("Amazon Web Services", "aws"),
        ("AWS", "aws"),
        ("Google Cloud Platform", "google_cloud"),
        ("Azure", "microsoft_azure"),
        ("MS Excel", "microsoft_excel"),
        ("Excel", "microsoft_excel"),
        ("G Suite", "google_workspace"),
        ("Power BI", "power_bi"),
        ("PowerBI", "power_bi"),
        ("FP&A", "fp_and_a"),
        ("Financial Modelling", "financial_modeling"),
        ("A/B Testing", "a_b_testing"),
        ("HACCP", "haccp"),
        ("Good Manufacturing Practices", "gmp"),
        ("SQF", "sqf"),
        ("IFRS", "ifrs"),
        ("PLC programming", "plc"),
        ("Lean Manufacturing", "lean_manufacturing"),
        ("Six Sigma", "six_sigma"),
        ("Lean Six Sigma", "lean_six_sigma"),
        ("Forklift", "forklift_operation"),
        ("Supply Chain Management", "supply_chain_management"),
        ("Search Engine Optimisation", "seo"),
        ("LLMs", "large_language_models"),
    ],
)
def test_skill_aliases(term: str, key: str) -> None:
    result = normalize_skill(term)
    assert result is not None and result.status is MATCH, result
    assert result.key == key


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Java", "JavaScript"),
        ("React", "React Native"),
        ("Six Sigma", "Lean Six Sigma"),
        ("Google Sheets", "Google Workspace"),
        ("Microsoft Excel", "Microsoft Office"),
        ("SQL", "MySQL"),
        ("Accounting", "Certified Public Accountant"),
    ],
)
def test_related_skills_stay_distinct(a: str, b: str) -> None:
    ra, rb = normalize_skill(a), normalize_skill(b)
    assert ra is not None and rb is not None
    assert ra.is_match and rb.is_match and ra.key != rb.key


@pytest.mark.parametrize(
    ("term", "reason", "candidates"),
    [
        ("SAP", "ambiguous_term", []),
        ("Oracle", "ambiguous_term", []),
        ("CAD", "ambiguous_term", []),
        ("CPA", "alias_needs_context", ["certified_public_accountant"]),
        ("GCP", "alias_needs_context", ["google_cloud"]),
        ("Go", "alias_needs_context", ["go"]),
        ("R", "alias_needs_context", ["r"]),
        ("Swift", "alias_needs_context", ["swift"]),
        ("Lean", "alias_needs_context", ["lean_manufacturing"]),
        ("PLC", "alias_needs_context", ["plc"]),
        ("ML", "alias_needs_context", ["machine_learning"]),
        ("SCM", "alias_needs_context", ["supply_chain_management"]),
        ("Node", "alias_needs_context", ["node_js"]),
    ],
)
def test_ambiguous_skills_are_never_guessed(term: str, reason: str, candidates: list) -> None:
    result = normalize_skill(term)
    assert result is not None and result.status is AMBIGUOUS, result
    assert result.key is None and result.reason == reason
    assert [c.key for c in result.candidates] == candidates


@pytest.mark.parametrize(
    "term",
    [
        "SAP ABAP",  # not seeded: no real posting asks for it
        "SAP IBP",
        "AWS S3",  # a service is not the platform
        "Amazon S3",
        "Microsoft 365",
        "C++",
        "Nursery",
        "Developer",
        "Reactive",
        "JavaScript frameworks",
    ],
)
def test_unknown_skills_stay_unknown(term: str) -> None:
    result = normalize_skill(term)
    assert result is not None and result.status is UNKNOWN, result


def test_no_vendor_gets_special_treatment() -> None:
    sap, oracle = normalize_skill("SAP"), normalize_skill("Oracle")
    assert sap is not None and oracle is not None
    assert (sap.status, sap.reason) == (oracle.status, oracle.reason)
    # Every seed entry resolves through the same generic lookup: its label
    # matches it -- or, where the label alone is ambiguous ("Go", "R"),
    # names it as the only candidate.
    for entry in SKILLS.entries:
        result = normalize_skill(entry.label)
        assert result is not None, entry.key
        if result.is_match:
            assert result.key == entry.key
        else:
            assert result.reason == "alias_needs_context"
            assert [c.key for c in result.candidates] == [entry.key]


def test_skill_seed_spans_many_domains() -> None:
    used = {entry.category for entry in SKILLS.entries}
    assert {"programming_language", "business_software", "finance", "food_safety",
            "supply_chain", "engineering", "marketing", "logistics",
            "quality_management", "occupational_safety", "certification"} <= used


# --- Languages ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "key"),
    [
        ("German", "de"),
        ("Deutsch", "de"),
        ("deutsch", "de"),
        ("Deutschkenntnisse", "de"),
        ("English", "en"),
        ("Englisch", "en"),
        ("Engels", "en"),
        ("French", "fr"),
        ("Français", "fr"),
        ("Französisch", "fr"),
        ("Dutch", "nl"),
        ("Nederlands", "nl"),
        ("Spanish", "es"),
        ("Español", "es"),
        ("Swedish", "sv"),
        ("Norwegian", "no"),
        ("Danish", "da"),
        ("Japanese", "ja"),
        ("Greek", "el"),
        ("de", "de"),
        ("EN", "en"),
    ],
)
def test_language_aliases(term: str, key: str) -> None:
    result = normalize_language(term)
    assert result is not None and result.is_match and result.key == key


@pytest.mark.parametrize("term", ["IT", "no", "ja"])
def test_language_codes_that_are_words_are_ambiguous(term: str) -> None:
    result = normalize_language(term)
    assert result is not None and result.status is AMBIGUOUS


@pytest.mark.parametrize("term", ["Klingon", "Javanese", "Swiss German", "Englischer"])
def test_unknown_languages(term: str) -> None:
    result = normalize_language(term)
    assert result is not None and result.status is UNKNOWN


@pytest.mark.parametrize(
    ("term", "level"),
    [
        ("A1", "A1"),
        ("b2", "B2"),
        ("C1-Niveau", "C1"),
        ("B2 Niveau", "B2"),
        ("Niveau C1", "C1"),
        ("C2 level", "C2"),
        ("Level B1", "B1"),
        ("CEFR A2", "A2"),
        ("native", "native"),
        ("Native speaker", "native"),
        ("mother tongue", "native"),
        ("Muttersprache", "native"),
        ("muttersprachliche", "native"),
        ("unknown", "unknown"),
    ],
)
def test_language_levels(term: str, level: str) -> None:
    result = normalize_language_level(term)
    assert result is not None and result.is_match and result.key == level


@pytest.mark.parametrize(
    "term", ["fluent", "fließend", "business fluent", "verhandlungssicher", "C1+", "good", "D1"]
)
def test_level_wording_without_authoritative_mapping_is_unknown(term: str) -> None:
    result = normalize_language_level(term)
    assert result is not None and result.status is UNKNOWN


def test_language_level_representation() -> None:
    assert set(LANGUAGE_LEVEL_VOCABULARY.keys) == set(LANGUAGE_LEVELS)
    assert CEFR_ORDER == ("A1", "A2", "B1", "B2", "C1", "C2")
    # "unknown" is a value; missing input is None -- they never coincide.
    assert normalize_language_level(None) is None
    stated = normalize_language_level("unknown")
    assert stated is not None and stated.key == "unknown"


# --- Education ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "key"),
    [
        ("Bachelor", "bachelor"),
        ("Bachelor's", "bachelor"),
        ("Bachelor’s degree", "bachelor"),
        ("Bachelors degree", "bachelor"),
        ("BSc", "bachelor"),
        ("B.Sc.", "bachelor"),
        ("B.A.", "bachelor"),
        ("Bachelor of Science", "bachelor"),
        ("Master", "master"),
        ("Master's degree", "master"),
        ("MSc", "master"),
        ("M.Sc.", "master"),
        ("MBA", "master"),
        ("PhD", "doctorate"),
        ("Ph.D.", "doctorate"),
        ("Doctorate", "doctorate"),
        ("Doctoral degree", "doctorate"),
        ("Ausbildung", "vocational_training"),
        ("abgeschlossene Ausbildung", "vocational_training"),
        ("Apprenticeship", "vocational_training"),
        ("High school diploma", "secondary_school"),
        ("GED", "secondary_school"),
        ("Associate’s degree", "associate_degree"),
    ],
)
def test_education_aliases(term: str, key: str) -> None:
    result = normalize_education_level(term)
    assert result is not None and result.is_match and result.key == key


@pytest.mark.parametrize(
    ("term", "candidates"),
    [("diploma", []), ("Diplom", ["bachelor", "master"]), ("MS", ["master"])],
)
def test_ambiguous_education(term: str, candidates: list) -> None:
    result = normalize_education_level(term)
    assert result is not None and result.status is AMBIGUOUS
    assert [c.key for c in result.candidates] == candidates


@pytest.mark.parametrize(
    "term", ["degree", "university degree", "Studium", "Computer Science", "Meister", "MD"]
)
def test_education_level_is_never_inferred(term: str) -> None:
    result = normalize_education_level(term)
    assert result is not None and result.status is UNKNOWN


def test_minimum_education_levels_exist() -> None:
    assert {"vocational_training", "bachelor", "master", "doctorate"} <= set(EDUCATION_LEVELS)
    assert set(EDUCATION_LEVEL_VOCABULARY.keys) == set(EDUCATION_LEVELS)


# --- Titles -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "key", "details"),
    [
        ("Software Engineer", "software_engineer", {}),
        ("Senior Software Engineer (Backend focused) - Supply", "software_engineer",
         {"modifiers": "senior", "qualifier": "Backend focused) - Supply"}),
        ("Sr. Data Scientist", "data_scientist", {"modifiers": "senior"}),
        ("Data Engineer (m/w/d)", "data_engineer", {}),
        ("Accountant (H/F/N)", "accountant", {}),
        ("Product Manager (v/m/x)", "product_manager", {}),
        ("Maintenance Technician III", "maintenance_technician", {"grade": "iii"}),
        ("[US DC] Machine Operator", "machine_operator", {}),
        ("Marketing Manager", "marketing_manager", {}),
        ("Sales Representatives", "sales_representative", {}),
        ("Fachkraft für Arbeitssicherheit (m/w/d)", "occupational_safety_specialist", {}),
    ],
)
def test_title_aliases(title: str, key: str, details: dict) -> None:
    result = normalize_title(title)
    assert result is not None and result.is_match and result.key == key, result
    for name, value in details.items():
        assert result.detail(name) == value


@pytest.mark.parametrize(
    "title",
    [
        "Software Developer",  # developer is not engineer
        "Developer",
        "Engineer",
        "Junior Recipe Developer (all genders)",
        "Lifecycle Marketing Manager",  # more specific than marketing manager
        "General Ledger Accountant",
        "Software Engineering Consultant",
        "Production Lead",
        "Sales Manager",  # not observed in the real data, so not seeded
        "Occupational Safety Specialist",  # no translation equivalences
        "Senior",
    ],
)
def test_titles_are_never_matched_by_similarity(title: str) -> None:
    result = normalize_title(title)
    assert result is not None and result.status is UNKNOWN, result


# --- Locations ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "key", "details"),
    [
        ("Köln", "city:de:cologne", {"country": "DE"}),
        ("Cologne, Germany", "city:de:cologne", {"country": "DE"}),
        ("Köln, DE", "city:de:cologne", {"country": "DE"}),
        ("Bonn", "city:de:bonn", {}),
        ("Toronto, Ontario, Canada", "city:ca:toronto", {"region": "region:ca:ontario"}),
        ("Aurora, IL, United States", "city:us:aurora_illinois", {}),
        ("Aurora, Colorado", "city:us:aurora_colorado", {}),
        ("Perth, Australia", "city:au:perth", {}),
        ("Newark, NJ", "city:us:newark", {"region": "region:us:new_jersey"}),
        ("Derby, United Kingdom", "city:gb:derby", {}),
        ("Berlin, Berlin, Germany", "city:de:berlin", {"region": "region:de:berlin"}),
        ("Bavaria, Germany", "region:de:bavaria", {}),
        ("Ontario", "region:ca:ontario", {}),
        ("Germany", "country:de", {}),
        ("Deutschland", "country:de", {}),
        ("DEU", "country:de", {}),
        ("Remote", "remote", {"remote": "true"}),
        ("Fully remote", "remote", {"remote": "true"}),
        ("100% Remote", "remote", {"remote": "true"}),
        ("Home Office", "remote", {"remote": "true"}),
        ("Remote - Germany", "country:de", {"remote": "true"}),
        ("Hybrid - Munich", "city:de:munich", {"hybrid": "true"}),
    ],
)
def test_locations(text: str, key: str, details: dict) -> None:
    result = normalize_location(text)
    assert result is not None and result.is_match and result.key == key, result
    for name, value in details.items():
        assert result.detail(name) == value


@pytest.mark.parametrize(
    ("text", "status", "reason"),
    [
        ("Aurora", AMBIGUOUS, "alias_needs_context"),
        ("Perth", AMBIGUOUS, "alias_needs_context"),
        ("DE", AMBIGUOUS, "bare_code"),
        ("Cologne, US", UNKNOWN, "inconsistent_parts"),
        ("Berlin HQ", UNKNOWN, "not_in_vocabulary"),
        ("Brooklyn, New York", UNKNOWN, "not_in_vocabulary"),
        ("Newark, United States", AMBIGUOUS, "alias_needs_context"),  # NJ or Delaware?
        ("Aurora, United States", AMBIGUOUS, "alias_needs_context"),
        ("Lake Zurich, Switzerland", UNKNOWN, "inconsistent_parts"),
        ("Lake Zurich, Ohio", UNKNOWN, "unknown_part"),
        ("Springfield, IL", UNKNOWN, "not_in_vocabulary"),
        ("Hybrid", UNKNOWN, "no_place"),
        ("Berlin; Munich", UNKNOWN, "multiple_locations"),
        ("Atlantis", UNKNOWN, "not_in_vocabulary"),
    ],
)
def test_location_ambiguity_and_unknowns(text: str, status: Any, reason: str) -> None:
    result = normalize_location(text)
    assert result is not None and result.status is status and result.reason == reason, result
    assert result.key is None


def test_ambiguous_city_lists_its_candidates() -> None:
    result = normalize_location("Aurora")
    assert result is not None
    assert [c.key for c in result.candidates] == ["city:us:aurora_colorado",
                                                  "city:us:aurora_illinois"]


def test_location_lists_and_country_fields() -> None:
    results = normalize_location_list("Irving, TX, United States; Newark, NJ, United States")
    assert [r.key for r in results] == ["city:us:irving", "city:us:newark"]
    for code in ("de", "DE", "Germany", "DEU"):
        result = normalize_country(code)
        assert result is not None and result.key == "country:de"
    unknown = normalize_country("Bavaria")
    assert unknown is not None and unknown.status is UNKNOWN


def test_v04_deduplication_keys_are_unchanged() -> None:
    # The taxonomy extends V0.4's vocabulary without editing it, so V0.4's
    # identity keys (and therefore deduplication) behave exactly as before.
    assert location_key("Toronto, Ontario, Canada") == "text:toronto ontario canada"
    assert location_key("Köln, DE") == "de:cologne"
    assert location_key("DEU") == "text:deu"


# --- Integration with the V0.5.0 candidate snapshot -----------------------------------


def test_snapshot_levels_are_the_taxonomy_levels() -> None:
    for level in LANGUAGE_LEVELS:
        CandidateProfileDocument.model_validate(
            {"languages": [{"language_key": "de", "level": level, "origin": "user"}]}
        )
    for level in EDUCATION_LEVELS:
        CandidateProfileDocument.model_validate(
            {"credentials": {"education": [{"degree_level": level, "origin": "user"}]}}
        )
    for retired in ("secondary", "vocational", "other"):
        with pytest.raises(ValidationError):
            CandidateProfileDocument.model_validate(
                {"credentials": {"education": [{"degree_level": retired, "origin": "user"}]}}
            )


def test_snapshot_skill_keys_need_a_taxonomy_version() -> None:
    result = normalize_skill("Postgres")
    assert result is not None and result.key is not None
    skill = {"name": "Postgres", "skill_key": result.key, "origin": "user"}
    with pytest.raises(ValidationError, match="taxonomy_version"):
        CandidateProfileDocument.model_validate({"skills": [skill]})
    document = CandidateProfileDocument.model_validate(
        {"skills": [skill], "metadata": {"taxonomy_version": result.taxonomy_version}}
    )
    assert document.skills[0].skill_key == "postgresql"
    assert document.metadata.taxonomy_version == TAXONOMY_VERSION
    with pytest.raises(ValidationError):
        CandidateProfileDocument.model_validate(
            {"skills": [{**skill, "skill_key": "Not A Key"}],
             "metadata": {"taxonomy_version": TAXONOMY_VERSION}}
        )


def test_language_keys_fit_the_snapshot_format() -> None:
    from app.services.taxonomy.languages import LANGUAGES

    for key in LANGUAGES.keys:
        CandidateProfileDocument.model_validate(
            {"languages": [{"language_key": key, "origin": "user"}]}
        )
