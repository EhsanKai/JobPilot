"""Taxonomy versions -- the one place to bump them.

Each vocabulary has its own version. `TAXONOMY_VERSION` names the whole
set; it is the value V0.5.0 records as `taxonomy_version` (on
`job_intelligence` rows and in candidate snapshot metadata), so a stored
key can always be traced to the vocabulary that defined it.

Rules:

- Changing a vocabulary's meaning (a key, label, category, alias or
  ambiguity) requires a new version of that vocabulary and a new
  `TAXONOMY_VERSION`. The pinned fingerprints in
  `tests/test_taxonomy.py` fail until both are bumped.
- Never reuse or edit an existing version's entry in `TAXONOMY_MANIFESTS`:
  historical records refer to it.
"""

TAXONOMY_MANIFESTS: dict[str, dict[str, str]] = {
    "taxonomy_v1": {
        "skills": "skills_v1",
        "languages": "languages_v1",
        "language_levels": "language_levels_v1",
        "education_levels": "education_levels_v1",
        "job_titles": "job_titles_v1",
        "locations": "locations_v1",
    },
}

TAXONOMY_VERSION = "taxonomy_v1"
VOCABULARY_VERSIONS: dict[str, str] = TAXONOMY_MANIFESTS[TAXONOMY_VERSION]
