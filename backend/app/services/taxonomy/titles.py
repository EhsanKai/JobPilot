"""Job-title vocabulary (V0.5.1): explicit title aliases, nothing more.

Profession-neutral and deliberately small: a handful of titles that recur
in the 503 real postings, from software to sanitation. It normalizes how a
title is *written*, not what it is similar to:

- "Developer" is not "Engineer"; "Lifecycle Marketing Manager" is not
  "Marketing Manager"; "Recipe Developer" is not a software title.
- Only the role part of a posting title is looked up. Structure is peeled
  off deterministically and reported in `details`, never discarded
  silently:
    * gender markers -- "(m/w/d)", "(all genders)", "(H/F/N)", "(v/m/x)";
    * a leading tag -- "[US DC] Machine Operator";
    * a qualifier after ",", " - ", "(" or "|" -- "Data Engineer, Growth";
    * leading seniority words (senior/junior/lead/staff/principal, "Sr."/
      "Jr.") and a trailing grade (I-IV) -- "Maintenance Technician II".
- The remaining role must equal an alias exactly; otherwise UNKNOWN.
"""

import re

from app.services.job_discovery.normalization.text import (
    clean_text,
    strip_gender_marker,
    title_key,
)
from app.services.taxonomy.model import Category, Entry, NormalizationResult, Vocabulary
from app.services.taxonomy.versions import TAXONOMY_VERSION, VOCABULARY_VERSIONS

_TITLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("software_engineer", "Software Engineer", ()),
    ("data_engineer", "Data Engineer", ()),
    ("data_scientist", "Data Scientist", ()),
    ("data_analyst", "Data Analyst", ()),
    ("product_analyst", "Product Analyst", ()),
    ("financial_analyst", "Financial Analyst", ()),
    ("product_manager", "Product Manager", ()),
    ("project_manager", "Project Manager", ()),
    ("product_designer", "Product Designer", ()),
    ("ux_designer", "UX Designer", ()),
    ("accountant", "Accountant", ()),
    ("marketing_manager", "Marketing Manager", ()),
    ("account_executive", "Account Executive", ()),
    ("account_manager", "Account Manager", ()),
    ("sales_representative", "Sales Representative", ("sales representatives",)),
    ("technical_recruiter", "Technical Recruiter", ()),
    ("customer_care_specialist", "Customer Care Specialist", ()),
    ("maintenance_technician", "Maintenance Technician", ()),
    ("automation_technician", "Automation Technician", ()),
    ("refrigeration_technician", "Refrigeration Technician", ()),
    ("machine_operator", "Machine Operator", ()),
    ("production_associate", "Production Associate", ()),
    ("production_supervisor", "Production Supervisor", ()),
    ("production_manager", "Production Manager", ()),
    ("sanitation_associate", "Sanitation Associate", ()),
    ("quality_control_associate", "Quality Control Associate", ()),
    ("warehouse_associate", "Warehouse Associate", ()),
    ("warehouse_supervisor", "Warehouse Supervisor", ()),
    ("supply_chain_planner", "Supply Chain Planner", ()),
    # A legally defined German role; only its German name is an alias.
    ("occupational_safety_specialist", "Fachkraft für Arbeitssicherheit", ()),
)

JOB_TITLES = Vocabulary(
    name="job_titles",
    version=VOCABULARY_VERSIONS["job_titles"],
    taxonomy_version=TAXONOMY_VERSION,
    categories=(Category("job_title", "Job title"),),
    entries=tuple(Entry(key, label, "job_title", aliases) for key, label, aliases in _TITLES),
)

# Gender markers V0.4's title key keeps but real postings use: "(H/F/N)"
# (French), "(v/m/x)" (Dutch), "(M/F/N)".
_EXTRA_GENDER_MARKER = re.compile(r"[(\[]\s*[a-z]\s*(?:/\s*[a-z]\s*){1,3}[)\]]", re.IGNORECASE)
_LEADING_TAG = re.compile(r"^\s*\[[^\]]{1,60}\]\s*")
# Where the role ends and a qualifier (team, location, contract) begins.
_QUALIFIER_SPLIT = re.compile(r"\s*,\s*|\s+[-–—|]\s+|\s*\(")
_SENIORITY_WORDS = frozenset({"senior", "junior", "lead", "staff", "principal"})
_GRADES = frozenset({"i", "ii", "iii", "iv"})


def normalize_title(text: str | None) -> NormalizationResult | None:
    """Normalize a job title (see module docs). None for missing input."""
    cleaned = clean_text(text)
    if cleaned is None:
        return None
    title = _EXTRA_GENDER_MARKER.sub(" ", strip_gender_marker(cleaned))
    title = _LEADING_TAG.sub("", title).strip()
    head, _, qualifier = _partition(title)

    words = title_key(head).split()
    modifiers: list[str] = []
    while len(words) > 1 and words[0] in _SENIORITY_WORDS:
        modifiers.append(words.pop(0))
    grade = words.pop() if len(words) > 1 and words[-1] in _GRADES else None

    details = {"role": " ".join(words)}
    if modifiers:
        details["modifiers"] = " ".join(modifiers)
    if grade:
        details["grade"] = grade
    if qualifier:
        details["qualifier"] = qualifier
    if not words:
        return JOB_TITLES.unknown(cleaned, "no_comparable_text", details)
    result = JOB_TITLES.lookup(" ".join(words))
    if result.is_match and result.key is not None:
        return JOB_TITLES.match(
            cleaned, JOB_TITLES.entry(result.key), result.matched_alias, details
        )
    if result.reason == "not_in_vocabulary":
        return JOB_TITLES.unknown(cleaned, "not_in_vocabulary", details)
    return JOB_TITLES.ambiguous(
        cleaned,
        [JOB_TITLES.entry(c.key) for c in result.candidates],
        result.reason or "several_entries",
        details,
    )


def _partition(title: str) -> tuple[str, str, str]:
    match = _QUALIFIER_SPLIT.search(title)
    if match is None:
        return title, "", ""
    qualifier = title[match.end() :].strip(" )-–—|,")
    return title[: match.start()], match.group(0), qualifier
