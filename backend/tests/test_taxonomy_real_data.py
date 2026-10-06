"""V0.5.1 taxonomy regressions on the 503 real Greenhouse/Lever postings
captured in V0.4.1 (tests/fixtures/jobs/live/).

Every term below is first asserted to occur verbatim in the captured
postings (`observed`), so these tests pin behaviour on real wording, not on
invented examples. Negative cases are deliberate: a conservative UNKNOWN is
preferred to a wrong normalization.
"""

import json
import re
from collections import Counter
from functools import cache
from pathlib import Path

import pytest

from app.services.job_discovery.normalization.location import CITIES
from app.services.job_discovery.normalization.text import fold
from app.services.job_discovery.normalizer import JobNormalizer, NormalizedJob
from app.services.job_discovery.sources.ats import Board
from app.services.taxonomy import (
    NormalizationStatus,
    normalize_education_level,
    normalize_language,
    normalize_language_level,
    normalize_location,
    normalize_location_list,
    normalize_skill,
    normalize_title,
)
from app.services.taxonomy.education import EDUCATION_LEVEL_VOCABULARY
from app.services.taxonomy.languages import LANGUAGES
from app.services.taxonomy.locations import LOCATIONS
from app.services.taxonomy.skills import SKILLS
from app.services.taxonomy.titles import JOB_TITLES
from tests.job_helpers import FakeJobBoards, greenhouse_source, lever_source

LIVE = Path(__file__).parent / "fixtures" / "jobs" / "live"
pytestmark = pytest.mark.skipif(not LIVE.is_dir(), reason="no captured real responses")

MATCH = NormalizationStatus.MATCH
UNKNOWN = NormalizationStatus.UNKNOWN
AMBIGUOUS = NormalizationStatus.AMBIGUOUS


@cache
def real_jobs() -> tuple[NormalizedJob, ...]:
    jobs: list[NormalizedJob] = []
    greenhouse = greenhouse_source(FakeJobBoards())
    for path in sorted((LIVE / "greenhouse").glob("*.jobs.json")):
        token = path.name.removesuffix(".jobs.json")
        for item in json.loads(path.read_text(encoding="utf-8"))["jobs"]:
            raw = greenhouse.to_raw_job(item, board=Board(token), company_name=None)
            jobs.append(JobNormalizer().normalize(raw))
    lever = lever_source(FakeJobBoards())
    for path in sorted((LIVE / "lever").glob("*.json")):
        for item in json.loads(path.read_text(encoding="utf-8")):
            jobs.append(JobNormalizer().normalize(lever.to_raw_job(item, board=Board(path.stem))))
    return tuple(jobs)


@cache
def corpus() -> str:
    return "\n".join(
        "\n".join([j.title, j.description or "", *j.requirements, *j.responsibilities])
        for j in real_jobs()
    )


@cache
def folded_corpus() -> str:
    return f" {fold(corpus())} "


@cache
def real_locations() -> Counter[str]:
    return Counter(loc for j in real_jobs() for loc in (j.locations or [j.location]) if loc)


def observed(term: str) -> str:
    assert term in corpus(), f"{term!r} does not occur in the captured postings"
    return term


def test_the_capture_is_the_v041_validation_set() -> None:
    assert len(real_jobs()) == 503


# --- Seed selection is data-driven -------------------------------------------------


def _mentioned(aliases: tuple[str, ...]) -> bool:
    return any(f" {alias} " in folded_corpus() for alias in aliases)


@pytest.mark.parametrize("key", SKILLS.keys)
def test_every_seed_skill_occurs_in_the_real_postings(key: str) -> None:
    assert _mentioned(SKILLS.aliases(key))


@pytest.mark.parametrize("key", LANGUAGES.keys)
def test_every_seed_language_occurs_in_the_real_postings(key: str) -> None:
    # Verbatim and capitalized: "polish the product" must not justify Polish.
    assert re.search(rf"\b{LANGUAGES.entry(key).label}\b", corpus())


@pytest.mark.parametrize("key", EDUCATION_LEVEL_VOCABULARY.keys)
def test_every_education_level_occurs_in_the_real_postings(key: str) -> None:
    assert _mentioned(EDUCATION_LEVEL_VOCABULARY.aliases(key))


def test_every_seed_title_is_a_real_posting_title() -> None:
    matched = {r.key for j in real_jobs() if (r := normalize_title(j.title)) and r.is_match}
    assert matched == set(JOB_TITLES.keys)


def test_every_location_added_in_v051_is_a_real_location() -> None:
    v04_cities = {f"city:{c.lower()}:{fold(n).replace(' ', '_')}" for n, (c, _) in CITIES.items()}
    added = {e.key for e in LOCATIONS.entries if e.category == "city"} - v04_cities
    matched = {
        r.key for loc in real_locations() for r in normalize_location_list(loc) if r.is_match
    }
    assert added <= matched, sorted(added - matched)


# --- Skills ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "key"),
    [
        ("Python", "python"),
        ("SQL", "sql"),
        ("PostgreSQL", "postgresql"),
        ("React.js", "react"),
        ("React Native", "react_native"),
        ("Node.js", "node_js"),
        ("Golang", "go"),
        ("Kubernetes", "kubernetes"),
        ("CI/CD", "ci_cd"),
        ("Snowflake", "snowflake"),
        ("Tableau", "tableau"),
        ("PowerBI", "power_bi"),
        ("Power BI", "power_bi"),
        ("MS Excel", "microsoft_excel"),
        ("Microsoft Excel", "microsoft_excel"),
        ("Google Sheets", "google_sheets"),
        ("Google Workspace", "google_workspace"),
        ("GSuite", "google_workspace"),
        ("Workday", "workday"),
        ("Salesforce", "salesforce"),
        ("Warehouse Management Systems", "warehouse_management_systems"),
        ("Warehouse-Management-Systemen", "warehouse_management_systems"),
        ("WMS", "warehouse_management_systems"),
        ("ERP", "erp"),
        ("A/B testing", "a_b_testing"),
        ("A/B tests", "a_b_testing"),
        ("SEO", "seo"),
        ("Search Engine Optimization", "seo"),
        ("FP&A", "fp_and_a"),
        ("financial modelling", "financial_modeling"),
        ("IFRS", "ifrs"),
        ("ACCA", "acca"),
        ("CPIM", "apics_cpim"),
        ("HACCP", "haccp"),
        ("SQF", "sqf"),
        ("Good Manufacturing Practices", "gmp"),
        ("OSHA", "osha"),
        ("PLC logic", "plc"),
        ("Lean Six Sigma", "lean_six_sigma"),
        ("Six Sigma", "six_sigma"),
        ("Lean Manufacturing", "lean_manufacturing"),
        ("Lean methodologies", "lean_manufacturing"),
        ("stakeholder management", "stakeholder_management"),
        ("Supply Chain Management", "supply_chain_management"),
        ("forklifts", "forklift_operation"),
        ("LLMs", "large_language_models"),
        ("large language models", "large_language_models"),
    ],
)
def test_real_skill_terms_normalize(term: str, key: str) -> None:
    result = normalize_skill(observed(term))
    assert result is not None and result.status is MATCH and result.key == key, result


def test_cad_in_real_postings_is_a_currency_so_it_is_ambiguous() -> None:
    assert re.search(r"\$[\d,]+—\$[\d,]+ CAD", corpus())  # "Pay Range $72,000—$82,000 CAD"
    result = normalize_skill(observed("CAD"))
    assert result is not None and result.status is AMBIGUOUS and result.candidates == ()


def test_cpa_means_two_things_in_real_postings() -> None:
    observed("CPA, ACCA")  # Certified Public Accountant (finance)
    observed("CPA/CAC")  # cost per acquisition (marketing)
    result = normalize_skill("CPA")
    assert result is not None and result.status is AMBIGUOUS


@pytest.mark.parametrize(
    ("term", "evidence"),
    [
        ("SAP", "Oracle, SAP, or other enterprise resource planning"),  # a vendor family
        ("Oracle", "Proficiency in Oracle, SAP"),
        ("R", "R&D"),  # mostly research & development, once the language
        ("Go", "Go, Python"),
        ("Swift", "iOS (Swift)"),
        ("Lean", "lean into"),  # a verb as well as a methodology
        ("ML", "AI/ML"),
        ("GCP", "GCP"),
        ("SCM", "SCM stakeholders"),
    ],
)
def test_real_ambiguous_terms_are_not_guessed(term: str, evidence: str) -> None:
    observed(evidence)
    result = normalize_skill(term)
    assert result is not None and result.status is AMBIGUOUS and result.key is None, result


@pytest.mark.parametrize(
    "term",
    [
        "Nursery",  # "Nursery support scheme" -- a benefit, not nursing
        "first aiders",  # "Mental health first aiders" -- a benefit
        "SAP IBP",  # a specific product the seed doesn't cover
        "IBP",
        "reactivation",  # not React
        "reactive",
        "cloud-native",
        "Databricks",
        "Statsig",
    ],
)
def test_real_non_skills_and_uncovered_skills_stay_unknown(term: str) -> None:
    result = normalize_skill(observed(term))
    assert result is not None and result.status is UNKNOWN, result


# --- Languages --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "key"),
    [
        ("English", "en"),
        ("German", "de"),
        ("Deutsch", "de"),
        ("Englisch", "en"),
        ("Deutschkenntnisse", "de"),
        ("Englischkenntnisse", "en"),
        ("French", "fr"),
        ("Dutch", "nl"),
        ("Nederlands", "nl"),
        ("Engels", "en"),
        ("Spanish", "es"),
        ("Swedish", "sv"),
        ("Norwegian", "no"),
        ("Danish", "da"),
        ("Italian", "it"),
        ("Portuguese", "pt"),
        ("Japanese", "ja"),
        ("Greek", "el"),
    ],
)
def test_real_language_names_normalize(term: str, key: str) -> None:
    result = normalize_language(observed(term))
    assert result is not None and result.is_match and result.key == key


@pytest.mark.parametrize(
    ("term", "level"),
    [
        ("B2", "B2"),
        ("B2 Niveau", "B2"),
        ("C1-Niveau", "C1"),
        ("B2-Niveau", "B2"),
        ("B2 level", "B2"),
        ("C1", "C1"),
        ("C2", "C2"),
        ("B1", "B1"),
        ("A2", "A2"),
        ("Muttersprachliche", "native"),
        ("native level", "native"),
    ],
)
def test_real_language_levels_normalize(term: str, level: str) -> None:
    result = normalize_language_level(observed(term))
    assert result is not None and result.is_match and result.key == level


@pytest.mark.parametrize("term", ["Fluent", "fluent", "C1+", "Business"])
def test_real_level_wording_without_a_mapping_stays_unknown(term: str) -> None:
    result = normalize_language_level(observed(term))
    assert result is not None and result.status is UNKNOWN


# --- Education ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "key"),
    [
        ("Bachelor’s degree", "bachelor"),
        ("Bachelor's degree", "bachelor"),
        ("Bachelor’s", "bachelor"),
        ("Bachelors degree", "bachelor"),
        ("Bachelor degree", "bachelor"),
        ("Bachelorstudium", "bachelor"),
        ("BS", "bachelor"),
        ("BA", "bachelor"),
        ("Master’s degree", "master"),
        ("Master's", "master"),
        ("MSc", "master"),
        ("MBA", "master"),
        ("PhD", "doctorate"),
        ("Ausbildung", "vocational_training"),
        ("High school diploma", "secondary_school"),
        ("High School Graduate", "secondary_school"),
        ("GED", "secondary_school"),
        ("Associates degree", "associate_degree"),
        ("Associate’s degree", "associate_degree"),
    ],
)
def test_real_education_terms_normalize(term: str, key: str) -> None:
    result = normalize_education_level(observed(term))
    assert result is not None and result.is_match and result.key == key


def test_real_education_ambiguity_and_unknowns() -> None:
    observed("MS Excel")  # why "MS" alone can't be a degree
    for term in ("MS", "diploma"):
        result = normalize_education_level(observed(term))
        assert result is not None and result.status is AMBIGUOUS
    for term in ("Studium", "degree", "diplomacy"):
        result = normalize_education_level(observed(term))
        assert result is not None and result.status is UNKNOWN


# --- Titles -------------------------------------------------------------------------------


def _title(raw: str) -> str:
    assert raw in {j.title for j in real_jobs()}, f"{raw!r} is not a real posting title"
    return raw


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("Senior Software Engineer (Backend focused) - Supply", "software_engineer"),
        ("Data Engineer, Operations Technology (all genders)", "data_engineer"),
        ("Senior Data Scientist, Growth", "data_scientist"),
        ("Sr. Product Analyst (m/w/x)", "product_analyst"),
        ("Senior Product Manager II, Logistics (all genders)", "product_manager"),
        ("Project Manager, Merchandising", "project_manager"),
        ("Senior Accountant", "accountant"),
        ("Marketing Manager", "marketing_manager"),
        ("Account Executive - UK", "account_executive"),
        ("Sales Representatives", "sales_representative"),
        ("Maintenance Technician II", "maintenance_technician"),
        ("[US DC] Machine Operator", "machine_operator"),
        ("Quality Control Associate (12 Months Contract)", "quality_control_associate"),
        ("Supply Chain Planner (f/m/x)", "supply_chain_planner"),
        ("Technical Recruiter (12 Month Maternity Cover)", "technical_recruiter"),
    ],
)
def test_real_titles_normalize(title: str, key: str) -> None:
    result = normalize_title(_title(title))
    assert result is not None and result.is_match and result.key == key, result


@pytest.mark.parametrize(
    "title",
    [
        "Junior Recipe Developer (all genders)",  # a developer, but not of software
        "Lifecycle Marketing Manager (all genders)",
        "General Ledger Accountant",
        "Software Engineering Consultant",
        "Cologne IT Consultant",
        "Production Lead",
        "Senior UX Product Designer",
        "Senior Frontend Developer (React Native)",
        "Working Student, Security Engineer",
        "Account Executive DACH (C1+ German) (m/w/x)",  # "DACH" stays in the role
    ],
)
def test_real_titles_without_an_exact_alias_stay_unknown(title: str) -> None:
    result = normalize_title(_title(title))
    assert result is not None and result.status is UNKNOWN, result


def test_no_real_developer_title_becomes_an_engineer() -> None:
    for job in real_jobs():
        result = normalize_title(job.title)
        if result is not None and result.is_match:
            assert "developer" not in (result.detail("role") or "")
            assert result.detail("role") in JOB_TITLES.aliases(result.key or "")


# --- Locations ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("location", "key"),
    [
        ("Berlin, Berlin, Germany", "city:de:berlin"),
        ("Berlin", "city:de:berlin"),
        ("Toronto, Ontario, Canada", "city:ca:toronto"),
        ("New York, NY, United States", "city:us:new_york"),
        ("Aurora, IL, United States", "city:us:aurora_illinois"),
        ("Aurora, Colorado, United States", "city:us:aurora_colorado"),
        ("Warszawa, Masovian Voivodeship, Poland", "city:pl:warsaw"),
        ("Verden, Niedersachsen, Germany", "city:de:verden"),
        ("Derby, England, United Kingdom", "city:gb:derby"),
        ("Manila, Manila, Philippines", "city:ph:manila"),
        ("München, Bavaria, Germany", "city:de:munich"),
        ("Hannover, Lower Saxony, Germany", "city:de:hanover"),
        ("Lake Zurich, Illinois, United States", "city:us:lake_zurich"),
        ("Zurich", "city:ch:zurich"),
        ("Bleiswijk, Provinz Südholland, Niederlande", "city:nl:bleiswijk"),
        ("Perth, Western Australia, Australia", "city:au:perth"),
        ("DEU", "country:de"),
        ("CHE", "country:ch"),
        ("USA", "country:us"),
        ("Philippines", "country:ph"),
        ("Germany", "country:de"),
    ],
)
def test_real_locations_normalize(location: str, key: str) -> None:
    assert location in real_locations(), f"{location!r} is not a real location"
    result = normalize_location(location)
    assert result is not None and result.is_match and result.key == key, result


@pytest.mark.parametrize(
    "location",
    [
        "Berlin HQ",
        "Brooklyn, New York, United States",  # a borough, not New York City
        "Etobicoke, Ontario, Canada",  # part of Toronto, not Toronto
        "København, Capital RegionDenmark, Denmark",  # malformed region
        "Palawan, Philippines",
    ],
)
def test_real_locations_outside_the_vocabulary_stay_unknown(location: str) -> None:
    assert location in real_locations()
    result = normalize_location(location)
    assert result is not None and result.status is UNKNOWN and result.key is None


def test_real_multi_location_strings_are_split() -> None:
    raw = "Irving, TX, United States; Newark, NJ, United States; Phoenix, AZ, United States"
    assert raw in real_locations()
    assert [r.key for r in normalize_location_list(raw)] == [
        "city:us:irving", "city:us:newark", "city:us:phoenix"
    ]


def test_real_location_coverage_and_consistency() -> None:
    results = [
        (count, result)
        for location, count in real_locations().items()
        for result in normalize_location_list(location)
    ]
    total = sum(count for count, _ in results)
    matched = sum(count for count, r in results if r.is_match)
    assert matched / total >= 0.95  # V0.4 recognized ~25% of Greenhouse locations
    assert not any(r.status is AMBIGUOUS for _, r in results)
    for _, result in results:
        if result.is_match and result.key != "remote":
            entry = LOCATIONS.entry(result.key or "")
            assert result.detail("country") == entry.attribute("country")
