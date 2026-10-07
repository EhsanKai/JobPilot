# V0.5.1 — Generic Taxonomy & Deterministic Normalization

V0.5.1 adds code-owned, versioned vocabularies and deterministic
normalizers for skills, languages and language levels, education levels,
job titles and locations (`backend/app/services/taxonomy/`).

It is a **foundation only**. Nothing calls these normalizers yet: there is
no requirement extraction, no snapshot builder, no matching or scoring, no
LLM, no embeddings, no semantic similarity and no geocoding. No database
migration and no API or frontend change come with it.

## Contents

1. [Principles](#1-principles)
2. [Architecture](#2-architecture)
3. [Normalization results](#3-normalization-results)
4. [Versioning](#4-versioning)
5. [Skills](#5-skills)
6. [Languages and levels](#6-languages-and-levels)
7. [Education levels](#7-education-levels)
8. [Job titles](#8-job-titles)
9. [Locations](#9-locations)
10. [How the seed was chosen](#10-how-the-seed-was-chosen)
11. [Integration with V0.5.0](#11-integration-with-v050)
12. [Tests](#12-tests)
13. [Known limitations](#13-known-limitations)

## 1. Principles

- **Domain-neutral.** The mechanism doesn't know any profession, industry
  or product. "HACCP", "Python", "IFRS" and "Forklift operation" are all
  ordinary entries; no code path names any of them. The same applies to
  SAP: "SAP" is one of three data-defined ambiguous vendor terms, handled
  exactly like "Oracle".
- **Normalization, not matching.** Only explicit aliases of the *same*
  thing are equated ("JS" = "JavaScript", "Postgres" = "PostgreSQL"). Related
  things stay distinct: Java ≠ JavaScript, Lean Six Sigma ≠ Six Sigma,
  Google Sheets ≠ Google Workspace, "Developer" ≠ "Engineer", AWS ≠ an AWS
  service.
- **Conservative.** When in doubt the result is UNKNOWN or AMBIGUOUS, never
  a guess.
- **Deterministic and reproducible.** Exact alias lookup on folded text, no
  randomness, no network, versioned data.

## 2. Architecture

```
app/services/taxonomy/
  model.py       Vocabulary, Entry, Alias, AmbiguousTerm, NormalizationResult
  versions.py    TAXONOMY_VERSION + per-vocabulary versions (the one place to bump)
  skills.py      SKILLS + normalize_skill
  languages.py   LANGUAGES, LANGUAGE_LEVEL_VOCABULARY + normalize_language(_level)
  education.py   EDUCATION_LEVEL_VOCABULARY + normalize_education_level
  titles.py      JOB_TITLES + normalize_title
  locations.py   LOCATIONS + normalize_location(_list), normalize_country
```

A `Vocabulary` is data plus one lookup:

| Element | Meaning |
| --- | --- |
| `Category(key, label)` | A grouping. Adding one is a data change; categories may have no entries yet (e.g. `healthcare`). |
| `Entry(key, label, category, aliases, attributes)` | A canonical concept with a stable snake_case key. The label is always an alias. `attributes` holds vocabulary-specific facts (a city's country). |
| `Alias(text, ambiguity=None)` | A way of writing an entry. With `ambiguity`, the alias also means something else and never matches on its own. |
| `AmbiguousTerm(text, reason, candidates)` | A term that names several things ("SAP", "Oracle", "CAD", "diploma"). |

**Folding** reuses V0.4's comparison folding (`normalization.text.fold`).
It is case-insensitive, removes diacritics, treats "&" as "and", turns
punctuation into spaces and collapses whitespace, but keeps "+" and "#"
inside words ("C++", "C#"). So "CI/CD", "ci-cd" and " CI / CD " are the
same, and "Bachelor’s" (curly apostrophe) equals "Bachelor's".

Building a vocabulary validates it at import time. These are errors:
invalid or duplicate keys, an unregistered category, empty aliases, an
alias shared by two entries without being declared ambiguous, an ambiguous
term that is also an alias, and unknown candidate keys.

There is no fuzzy matching, no stemming, no plural guessing and no partial
matching. "Lake Zurich" is not "Zurich", and "SAP ABAP" doesn't match a
seeded "SAP" because no such entry exists.

## 3. Normalization results

Every normalizer returns a `NormalizationResult`, or `None` when the input
is missing (None or blank). "Nothing to normalize" is never confused with
"not recognized".

| Status | Fields |
| --- | --- |
| `MATCH` | `key`, `label`, `category`, `matched_alias`, `vocabulary`, `vocabulary_version`, `taxonomy_version`, `input` (verbatim) |
| `UNKNOWN` | `input` (verbatim) and `reason`: `not_in_vocabulary`, `no_comparable_text`, `unknown_part`, `inconsistent_parts`, `no_place`, `multiple_locations` |
| `AMBIGUOUS` | `input`, `reason` (`ambiguous_term`, `alias_needs_context`, `several_entries`, `bare_code`), `candidates` (only those known to be in the vocabulary, possibly none); no key is chosen |

`details` carries extras: the human explanation of an ambiguity
(`ambiguity`), a title's `role` / `modifiers` / `grade` / `qualifier`, and a
location's `country` / `region` / `remote` / `hybrid`.

## 4. Versioning

- Each vocabulary has a version (`skills_v1`, `languages_v1`,
  `language_levels_v1`, `education_levels_v1`, `job_titles_v1`,
  `locations_v1`).
- **`TAXONOMY_VERSION = "taxonomy_v1"`** names that set
  (`TAXONOMY_MANIFESTS`). It is the value V0.5.0 already stores as
  `taxonomy_version`, both on `job_intelligence` rows and in candidate
  snapshot metadata. No second, incompatible mechanism exists.
- `tests/test_taxonomy.py` pins a SHA-256 fingerprint of each vocabulary's
  meaning (keys, labels, categories, folded aliases, ambiguity, attributes,
  but not the version string). Changing any of these without bumping the
  version fails the test.
- To change a vocabulary: edit its data, give it a new version, add a new
  `TAXONOMY_MANIFESTS` entry and point `TAXONOMY_VERSION` at it, then pin
  the new fingerprint. Never edit an existing manifest or pin. Historical
  records keep naming the version that defined their keys, so nothing
  stored is ever reinterpreted.

## 5. Skills

84 entries in 27 categories. Each seeded category has at least one entry
observed in the real postings. The counts below are postings, out of 503,
that mention the entry through an unambiguous alias:

| Category | Entries (postings) |
| --- | --- |
| programming_language | python 46, sql 63, java 9, javascript 5, kotlin 9, typescript 4, scala 2, go 6, swift †, r † |
| framework / library / runtime | react 14, react_native 8, vue_js 2, spring_boot 2, pandas 7, scikit_learn 4, node_js 4 |
| database / data_engineering | postgresql 7, mysql 3, snowflake 16, bigquery 3, apache_kafka 19, apache_airflow 12, apache_spark 12, dbt 6 |
| cloud / devops / mobile | aws 29, microsoft_azure 10, google_cloud 1 (+ "GCP" †), docker 9, kubernetes 14, ci_cd 22, terraform 2, git 4, ios 7, android 6 |
| data_science | machine_learning 18, large_language_models 27 |
| business_intelligence | tableau 38, looker 14, power_bi 9 |
| business_software | microsoft_excel 135, microsoft_office 60, google_sheets 60, google_workspace 33, microsoft_powerpoint 14, jira 14, workday 13, salesforce 12, crm 39, confluence 2, hubspot 2, zendesk 2 |
| enterprise_software | warehouse_management_systems 35, erp 13 |
| design / marketing | figma 10, seo 8, google_ads 5, google_analytics 3 |
| finance | financial_modeling 13, accounting 8, budgeting 8, ifrs 4, fp_and_a 4 |
| certification | acca 3, apics_cpim 4, certified_public_accountant † |
| quality_management / food_safety / occupational_safety | gmp 45, haccp 28, sqf 24, osha 11 |
| engineering | plc 12 |
| methodology / project_management | agile 38, a_b_testing 37, six_sigma 16, lean_six_sigma 16, lean_manufacturing 8, scrum 4, project_management 54, stakeholder_management 50 |
| supply_chain / logistics | supply_chain_management 161, procurement 81, inventory_management 20, logistics 67, forklift_operation 17 |

† Observed only through a context-dependent alias, with the meaning
confirmed by its context in the postings: "iOS (Swift)", "python and R",
"CPA, ACCA" / "CIA, ACCA, Chartered Accountant, or CPA".

Registered without seed entries: `healthcare` and `other`. The real data
holds no healthcare skill. "Nursery support scheme" and "Mental health
first aiders" are employee benefits, and a test pins them as UNKNOWN.

**Aliases** come from one of three sources:

- variants observed in the real data: "React.js", "PowerBI", "MS Excel",
  "GSuite", "Golang", "financial modelling", "A/B tests",
  "Warehouse-Management-Systemen", "Good Manufacturing Practices";
- variants already in the V0.4 technology vocabulary: "postgres", "k8s",
  "sklearn", "reactjs", "vuejs", "nodejs";
- plainly equivalent spellings or expansions: "JS", "Java Script",
  "Amazon Web Services", "Search engine optimisation".

**Ambiguity** comes from the real data:

| Term | Result | Why |
| --- | --- | --- |
| SAP, Oracle | AMBIGUOUS (term) | Vendor names covering many products ("Oracle, SAP, or other ERP systems"; "SAP IBP"). No SAP product is seeded: none recurs in the data, and "ABAP" never occurs. |
| CAD | AMBIGUOUS (term) | Every real occurrence is a Canadian-dollar pay range ("$72,000—$82,000 CAD"), not computer-aided design. |
| CPA | AMBIGUOUS → certified_public_accountant | Finance postings mean the qualification; marketing postings mean cost per acquisition ("CPA/CAC"). |
| GCP | AMBIGUOUS → google_cloud | Also Good Clinical Practice, a healthcare standard. |
| MS (education) | AMBIGUOUS → master | 75 occurrences, mostly "MS Excel" / "MS Office". |
| R, Go, Swift, Lean, Node, ML, PLC, SCM | AMBIGUOUS → the entry | A single letter ("R&D"), ordinary words ("lean into", "go"), or a second meaning (SWIFT interbank network, millilitre, public limited company, source-control management). Unambiguous forms match: "Golang", "R programming", "Lean Six Sigma", "PLC programming". |

## 6. Languages and levels

**Languages:** 12 entries, keyed by ISO 639-1 code (the `language_key`
format V0.5.0 already validates). Each was found by its English name in the
real postings: English 185, German 55, Dutch 14, French 13, Swedish 12,
Norwegian 9, Spanish 8, Danish 8, Italian, Portuguese, Japanese and Greek 1
each.

Aliases are the language's own name, its German name (German is the second
posting language), observed compounds ("Deutschkenntnisse" 29,
"Englischkenntnisse" 65), the Dutch "Engels", and the ISO code. The codes
"it", "no" and "ja" are also ordinary words, so on their own they are
AMBIGUOUS. Polish isn't seeded: "polish" occurs only as an English word
("the polish, the motion").

**Levels:** `A1`–`C2`, `native`, `unknown`. These are the values of
V0.5.0's `LanguageLevel`, which now imports them from here.

- Explicit level wording maps to a level: "C1", "B2-Niveau", "Niveau C1",
  "B2 level", "CEFR A2" (all observed forms); "native", "native speaker",
  "mother tongue", "Muttersprache", "muttersprachliche" (observed).
- Descriptive wording ("fluent", "fließend", "business fluent",
  "verhandlungssicher") and qualified levels ("C1+") are **UNKNOWN**. The
  repository has no authoritative mapping to CEFR; later phases decide it.
  V0.1's proficiency values are not mapped either.
- `unknown` is a value: the language is stated but its level isn't. Missing
  data is the absence of a language, or `None` from the normalizer.
- `CEFR_ORDER` records CEFR's own order. Where `native` stands relative to
  C2 is a matching decision and is left open.

## 7. Education levels

Keys: `secondary_school`, `vocational_training`, `associate_degree`,
`bachelor`, `master`, `doctorate`. The four required levels are
supplemented with two that the real data needs. US hourly roles ask for a
"High school diploma" or "GED" (56 postings) or an "Associates degree" (15).
German roles ask for an "Ausbildung" (19).

- Aliases cover degree types only: "Bachelor’s degree", "B.Sc.", "BS",
  "BA", "Bachelorstudium", "Master's", "MSc", "MBA", "PhD", "Ph.D.",
  "abgeschlossene Ausbildung", "GED".
- AMBIGUOUS: "diploma" (high school, postgraduate, Diplom), "Diplom"
  (candidates bachelor/master), "MS" (Microsoft).
- UNKNOWN, never inferred: "degree", "university degree", "Studium"
  (completed studies, level unstated), a subject such as "Computer Science",
  "Meister" (a craft qualification, not a master's degree), "MD".

## 8. Job titles

30 entries, each the normalized role of at least one real posting title,
spanning engineering, data, product, design, finance, marketing, sales,
recruiting, customer care, maintenance, production, quality, warehouse and
supply chain. One entry is the German "Fachkraft für Arbeitssicherheit"
(6 postings). Matches on the real data: 98 of 503 titles. All others stay
UNKNOWN.

`normalize_title` peels off structure deterministically and reports it in
`details`:

- gender markers, including the "(H/F/N)" and "(v/m/x)" forms V0.4.1 found;
- a leading tag such as "[US DC]";
- a qualifier after ",", " - ", "(" or "|";
- leading seniority words (senior/junior/lead/staff/principal, "Sr."/"Jr.");
- a trailing grade (I–IV).

The remaining role must then equal an alias exactly.

Never equated: "Software Developer" and "Software Engineer", "Recipe
Developer" and any software title, "Lifecycle Marketing Manager" and
"Marketing Manager", "General Ledger Accountant" and "Accountant",
"Account Executive DACH" and "Account Executive" ("DACH" stays in the
role). There are no translation equivalences: "Occupational Safety
Specialist" doesn't match the German entry.

## 9. Locations

Keys: `country:de`, `region:us:illinois`, `city:de:cologne`,
`city:us:aurora_illinois`, `remote`. `details` adds `country`, `region`,
`remote` and `hybrid`.

The vocabulary **extends V0.4's location vocabulary** with the forms
V0.4.1 found unrecognized. It reuses V0.4's city and country tables,
folding and remote/hybrid rules. It adds:

- 39 regions with postal abbreviations: Ontario/ON, Illinois/IL,
  Niedersachsen, Masovian Voivodeship, Provinz Südholland, …;
- 6 countries;
- ISO alpha-3 codes ("DEU" 39×, "CHE", "NLD", "USA", …);
- 39 cities that recur in the real postings (Mississauga, Aurora IL and
  Aurora CO, Verden, Derby, Manila, …).

V0.4's tables are not edited, so V0.4's deduplication keys are unchanged.
A test pins this.

Parsing is positional. An optional first part is a city, and every further
part must be a consistent region or country. On the real data, **748 of 760
location occurrences resolve**, where V0.4 recognized about 25% of
Greenhouse locations.

| Input | Result |
| --- | --- |
| Toronto, Ontario, Canada | city:ca:toronto |
| Aurora, IL, United States | city:us:aurora_illinois |
| Aurora | AMBIGUOUS (Illinois, Colorado) |
| Perth | AMBIGUOUS (also Perth, Scotland); "Perth, Western Australia" matches |
| Newark, United States | AMBIGUOUS (New Jersey or Delaware?): namesakes in one country need a region; "Newark, NJ" matches |
| Lake Zurich, Illinois | city:us:lake_zurich, never Zurich |
| Brooklyn, New York / Etobicoke, Ontario / Berlin HQ | UNKNOWN (a borough, a district, an unknown word) |
| DE | AMBIGUOUS (`bare_code`: Germany or Delaware?); `normalize_country("DE")` matches |
| DEU | country:de |
| Remote - Germany | country:de, `remote=true`; "Remote" alone is `remote` |
| A; B | `normalize_location_list` (one result each) |

There is no geocoding, no coordinates, no distance calculation and no maps
API.

## 10. How the seed was chosen

1. All 503 captured postings were run through the production normalizer
   (`JobNormalizer`). The text examined was title, description, requirements
   and responsibilities, plus every location string.
2. Candidate terms across many domains were counted. Every recurring or
   risky term was then read in context before being seeded, made
   ambiguous, or rejected:
   - "CAD" is always currency;
   - "nursing" and "first aid" appear only in benefits text;
   - "polish" and "lean" also occur as ordinary words;
   - "native" mostly appears in "cloud-native";
   - "MS" mostly means Microsoft;
   - "Diplom…" is always "diploma"/"diplomacy".
3. Only entries with real evidence were seeded. Tests enforce this:
   - every skill and education entry occurs in the postings;
   - every language name occurs verbatim;
   - every title entry matches a real title;
   - every city added in V0.5.1 matches a real location.
4. The real postings reach well beyond software: supply chain (161),
   procurement (81), food safety (86), production, marketing, finance,
   logistics. The seed reflects that.

## 11. Integration with V0.5.0

No table, column or migration changes. The V0.5.0 candidate snapshot
document now uses the taxonomy:

- `LanguageEntry.level` uses the taxonomy's `LanguageLevel` (same values as
  before).
- `EducationEntry.degree_level` uses the taxonomy's `EducationLevel`. The
  provisional V0.5.0 values `secondary` / `vocational` / `other` are
  replaced by `secondary_school` / `vocational_training` /
  `associate_degree` (`bachelor` / `master` / `doctorate` are unchanged).
  No snapshot rows exist yet, so nothing stored changes.
- `SkillEntry.skill_key` must be a snake_case taxonomy key, and any
  `skill_key` requires `metadata.taxonomy_version` (e.g. `taxonomy_v1`).

`job_intelligence.taxonomy_version` and `job_requirements.skill_key` are
unchanged. Future extraction stores `TAXONOMY_VERSION` and taxonomy keys
there.

## 12. Tests

- `tests/test_taxonomy.py` covers:
  - the mechanism: results, folding, validation errors, categories as data,
    determinism;
  - version pins;
  - every vocabulary's matches, distinct-but-related terms, ambiguity and
    unknowns;
  - unchanged V0.4 location keys;
  - the V0.5.0 document integration.
- `tests/test_taxonomy_real_data.py` runs regressions on the 503 real
  postings. Each term is first asserted to occur verbatim in the captured
  data. It also checks that the seed is data-driven, and checks location
  coverage (≥ 95%) and consistency.

## 13. Known limitations

- The vocabularies are small by design. Most real titles, and many skills,
  are UNKNOWN until the vocabularies grow with evidence.
- Normalizers take **terms**, not prose. Finding terms inside a posting
  ("You excel at…", "lean into", "cloud-native") is the extractor's job
  (later V0.5 phases), with context rules of its own.
- Cities from V0.4's table carry no region, so "Cologne, Bavaria" is not
  rejected. Only the country is checked for them.
- Titles prefixed with a city ("Cologne IT Consultant") stay UNKNOWN.
- Descriptive language levels have no CEFR mapping (by decision).
- Only the current version of each vocabulary is in the code. Re-running an
  old version's normalization means using the git history at that version;
  the stored keys themselves never change meaning.
