# V0.5.0 — Candidate & Job Intelligence Data Model

V0.5.0 lays the **data foundation** for V0.5 matching: four new tables, the
validated JSONB documents they hold, and one create-only migration.

What V0.5.0 contains:

- `candidate_profile_versions`, `job_intelligence`, `job_requirements`,
  `job_matches` (SQLAlchemy models in `backend/app/models/`)
- Pydantic documents for their JSONB columns (`backend/app/schemas/`:
  `candidate_profile.py`, `job_facts.py`, `job_match.py`, shared types in
  `document_types.py`)
- model-level validation and write-once semantics
- Alembic migration `a56b296b53a5` (parent: V0.4 `d4a7e3b9c2f1`)

What V0.5.0 does **not** contain: nothing creates rows in these tables yet.
There is no snapshot builder, skill taxonomy or normalization, requirement
extraction, scoring, explanation rendering, matching API, frontend change,
background job or AI. Those are later V0.5 phases ([§10](#10-future-v05-components-not-implemented)).
No existing table, API, discovery or source behaviour changes.

## Contents

1. [Overview](#1-overview)
2. [Reproducibility principle](#2-reproducibility-principle)
3. [candidate_profile_versions](#3-candidate_profile_versions)
4. [job_intelligence](#4-job_intelligence)
5. [job_requirements](#5-job_requirements)
6. [job_matches](#6-job_matches)
7. [Immutability and versioning](#7-immutability-and-versioning)
8. [Foreign keys and deletion](#8-foreign-keys-and-deletion)
9. [Migration](#9-migration)
10. [Future V0.5 components (not implemented)](#10-future-v05-components-not-implemented)

## 1. Overview

```
users ──< candidate_profile_versions ──┐                (immutable snapshots)
  │                                    │
  └──────────────< job_matches >───────┤                (one result per input set)
                       │               │
jobs ──< job_intelligence ─────────────┘                (versioned fact sheets)
  │            └──< job_requirements                    (immutable rows)
  └──< job_source_postings  (V0.4)  ◄── job_intelligence.source_posting_id
```

| Entity | Purpose | Owner |
| --- | --- | --- |
| `candidate_profile_versions` | Immutable snapshot of what matching may know about a candidate | user |
| `job_intelligence` | Versioned, derived fact sheet of a canonical job | job (global) |
| `job_requirements` | The requirements one `job_intelligence` version found | job_intelligence |
| `job_matches` | One reproducible match result per user, job and input set | user + job |

## 2. Reproducibility principle

A stored match result must be explainable and re-computable long after its
inputs changed. So every input is **pinned**, not referenced "live":

- the candidate side by an immutable snapshot (`candidate_profile_version_id`),
- the job side by a fact-sheet version (`job_intelligence_id`),
- the scoring logic by `model_id` + `model_hash`.

Editing a profile, re-extracting a job or changing the scoring model creates
*new* rows; it never rewrites the inputs of an existing result.

## 3. candidate_profile_versions

### Why snapshots, separate from the V0.1 profile tables

The V0.1 tables (`professional_profiles`, `job_preferences`, `user_skills`,
`user_languages`, `deal_breakers`, ...) stay the **editable source of
truth**. Matching needs something else: a frozen, self-contained view that a
result can point at. A snapshot is a **read model** built from:

- the V0.1 tables — origin `"user"`;
- the user's **confirmed** Resume Profile (accepted/edited items of
  `resume_profiles.reviewed_data`) — origin `"resume"`.

Rules:

- Resume data is **never written back** into the V0.1 tables (no import
  workflow exists).
- When both inputs state the same fact, the **user-declared value wins**.
- Every origin-bearing value records `origin`; resume-derived values also
  carry an `evidence_ref` (`resume_profile:<uuid>[/<item>]`), user values
  never do.

### Columns

| Column | Notes |
| --- | --- |
| `id`, `user_id` | `user_id` → `users` ON DELETE CASCADE |
| `version_number` | 1, 2, 3, ... per user (`CHECK >= 1`) |
| `content_hash` | SHA-256 hex of the canonical document |
| `document` | JSONB, `CandidateProfileDocument` |
| `schema_version` | e.g. `candidate_profile_v1`; mirrors `document.metadata` |
| `taxonomy_version` | skill taxonomy version; NULL while no taxonomy exists |
| `sources` | JSONB, `SnapshotSources`: exact input records (ids, timestamps) for audit; **not** part of the content hash |
| `reason` | `candidate_profile_version_reason`: INITIAL, PROFILE_UPDATED, PREFERENCES_UPDATED, RESUME_CONFIRMED, SCHEMA_UPGRADE, TAXONOMY_UPGRADE |
| `created_at` | no `updated_at`: rows never change |

Constraints: `UNIQUE (user_id, version_number)`,
`UNIQUE (user_id, content_hash)` (identical content is one version; two users
may share a hash), and `UNIQUE (id, user_id)` as the target of
`job_matches`' composite foreign key.

### Document (`CandidateProfileDocument`)

| Section | Fields |
| --- | --- |
| `role` | `current_title`, `seniority_level`, `years_of_experience` (each `{value, origin, evidence_ref}`), `target_titles` (user only) |
| `skills[]` | `name`, `skill_key` (NULL until taxonomy), `category`, `proficiency` (`unknown` unless stated), `years`, `last_used` (`YYYY`/`YYYY-MM`), `origin`, `evidence_ref` |
| `languages[]` | `language_key` (ISO 639-1), `level` (CEFR A1–C2, `native`, `unknown`), `origin`, `evidence_ref` |
| `location_and_mobility` | `current_location` (sourced), `preferred_locations` (user only), `relocation`, `travel` |
| `preferences` | `work_models`, `employment_types`, `minimum_salary`, `desired_salary`, `currency` (user only) |
| `deal_breakers[]` | the V0.1 representation unchanged: `type`, `value`, `enabled` |
| `credentials` | `education[]` (`degree`, `degree_level`, `field_of_study`, `institution`), `certifications[]` (`name`, `issuer`, `date`) |
| `work_authorization` | `authorized_countries` |
| `metadata` | `sections_provided`, `schema_version`, `taxonomy_version`, `sources` (origins) |

Validation (Pydantic, the Resume Profile convention):

- **Unknown keys are rejected everywhere.** Name, email, phone, LinkedIn or
  other URLs, resume text, age/birth date, gender, nationality, marital
  status, photo and health information cannot be represented. Education
  dates are left out too (graduation years are an age proxy).
- Vocabularies reuse V0.1 enums (`SeniorityLevel`, `RemotePreference`,
  `EmploymentTypeValue`, `RelocationPreference`, `TravelWillingness`,
  `DealBreakerType`, `JobTitlePriority`) and Resume Intelligence literals
  (`SkillCategory`, `SkillProficiencyLevel`).
- One entry per skill, language and deal-breaker type.
- `metadata.sections_provided` and `metadata.sources` are **derived from the
  content** (filled in when omitted, rejected when they disagree), and money
  is quantized to two decimals, so equal content always serializes — and
  hashes — identically. A section missing from `sections_provided` is
  unknown, not negative.

**Work authorization**: ISO 3166-1 alpha-2 codes only (validated against the
official list, normalized to upper case, sorted, de-duplicated). User-declared
only — never inferred from nationality, location, language, name or the
resume. An empty list means **UNKNOWN**, not "not authorized". Nationality is
never collected.

Location values mirror V0.4's deterministic normalization (`text`, `city`,
`country`, `is_remote`): no geocoding, no distances, no maps API.

## 4. job_intelligence

### Why separate from canonical jobs

`jobs` (V0.4) is *what a vacancy is*, maintained by discovery. Matching
needs *derived* facts (requirements, normalized signals) whose extraction
rules will change. Keeping them in their own versioned table means:

- `jobs` is never altered by V0.5 — no new columns, no rewrites;
- derived data can be discarded and recomputed at any time;
- old match results keep pointing at the exact fact sheet they used.

### Columns

| Column | Notes |
| --- | --- |
| `job_id` | → `jobs` ON DELETE CASCADE |
| `extractor_version`, `taxonomy_version` | what produced the result (both required) |
| `input_hash` | SHA-256 of the job content the extractor read |
| `result_hash` | SHA-256 of the facts and requirements produced |
| `facts` | JSONB, `JobFacts` |
| `is_current` | pointer to the version matching uses |
| `verified_at` | set by the future extraction review checkpoint |
| `source_posting_id` | the posting whose content was the input; → `job_source_postings` ON DELETE SET NULL |
| `created_at` | |

Constraints: partial `UNIQUE (job_id) WHERE is_current`;
`UNIQUE (job_id, result_hash, extractor_version, taxonomy_version)` (an
identical result is stored once per extractor/taxonomy version);
`UNIQUE (id, job_id)` for `job_matches`.

`JobFacts` has an optional, provenance-carrying slot (`value`,
`source_field`, `extraction_method`, `confidence`, `evidence_text`,
`evidence_offset`) for `seniority`, `employment_type`, `work_model`,
`location`, `salary`, `language` (named working languages), `travel`,
`authorization` (required countries, sponsorship) and `document_language`
(the posting's language — a signal, never a requirement by itself). A
missing fact is unknown.

## 5. job_requirements

One row per requirement of one `job_intelligence` version
(→ `job_intelligence` ON DELETE CASCADE).

- `kind`: `job_requirement_kind` — REQUIRED, PREFERRED, CONTEXTUAL.
- `category`: `job_requirement_category` — SKILL, EXPERIENCE, LANGUAGE,
  EDUCATION, CERTIFICATION, SENIORITY, WORK_MODEL, EMPLOYMENT_TYPE, SALARY,
  AUTHORIZATION, TRAVEL.
- Value columns, all nullable: `skill_key` (SKILL only), `value_key` (a
  vocabulary value: language code, enum value, country code, ...),
  `value_text`, `level`, `min_years`, `salary_min` / `salary_max` /
  `salary_currency` / `salary_period` (SALARY only; reuses the existing
  `salary_period` type).
- Provenance: `source_field`, `section_kind`, `extraction_method`,
  `confidence` (`extraction_confidence`: HIGH/MEDIUM/LOW, the Resume
  Intelligence scale), `evidence_text` (≤ 600 chars, verbatim) and
  `evidence_offset`. The source posting is the parent's
  `source_posting_id` (all requirements of a version share one input).

CHECK constraints keep category-specific columns consistent (skill key only
for SKILL, salary fields only for SALARY, `salary_min <= salary_max`,
`min_years >= 0`, an offset only with evidence text).

## 6. job_matches

| Column | Notes |
| --- | --- |
| `user_id`, `job_id` | → `users` / `jobs` ON DELETE CASCADE |
| `candidate_profile_version_id` | composite FK `(…, user_id)` → `candidate_profile_versions (id, user_id)` |
| `job_intelligence_id` | composite FK `(…, job_id)` → `job_intelligence (id, job_id)` |
| `model_id`, `model_hash` | the scoring model and a SHA-256 of its exact definition |
| `score` | 0–100, nullable |
| `coverage` | 0–1: share of the model's weight evaluable with known data |
| `verdict` | `job_match_verdict`: STRONG, GOOD, PARTIAL, WEAK, BLOCKED, INSUFFICIENT_DATA |
| `is_blocked` | `CHECK is_blocked = (verdict = 'BLOCKED')` |
| `criteria`, `caps` | JSONB, `MatchCriteria` / `MatchCaps`: per-criterion status, weight, score and evidence references; applied score ceilings |
| `is_current` | pointer to the user's current result for the job |

Constraints and indexes:

- `UNIQUE (user_id, job_id, candidate_profile_version_id, job_intelligence_id, model_id)`
  — the same inputs give one result (idempotent writes);
- partial `UNIQUE (user_id, job_id) WHERE is_current`;
- `ix_job_matches_user_current_score (user_id, score DESC NULLS LAST) WHERE is_current`
  — "my current matches, best first";
- the composite foreign keys make it impossible to pair a user with another
  user's snapshot, or a job with another job's fact sheet.

**Why store model and version hashes.** `model_id` names the model;
`model_hash` pins its exact definition (weights, thresholds, caps). If a
definition changes without a new id, the hash still tells old results apart.
Together with the two pinned version ids, a row records everything needed
to reproduce its score.

## 7. Immutability and versioning

All four entities are write-once (`ImmutableMixin` in
`backend/app/models/mixins.py`): an ORM flush that would change any column
raises `ImmutableRecordError`, except the pointer/state columns each model
lists in `__mutable_columns__`:

| Model | Mutable after insert |
| --- | --- |
| `CandidateProfileVersion` | — |
| `JobIntelligence` | `is_current`, `verified_at` |
| `JobRequirement` | — |
| `JobMatch` | `is_current` |

Deletes are allowed (they cascade from users and jobs). The guard covers
application code, not raw SQL.

Model-level validation: JSONB columns are validated (and normalized) by their
Pydantic document on assignment (`@validates`), hash columns must be
lowercase SHA-256 hex, `schema_version` / `taxonomy_version` on a snapshot
must equal the document metadata (they are filled in from it when omitted),
and the database enforces the CHECK constraints above. `read_document()`,
`read_facts()`, `read_criteria()` and `read_caps()` validate on read.

Versioning:

- candidate snapshots: a new `version_number` per distinct content;
- job intelligence: a new row per distinct result and extractor/taxonomy
  version; moving `is_current` switches versions;
- matches: a new row per distinct input set; moving `is_current` switches
  the user's current result. Old rows remain as history.

**Retention (current decision).** Current snapshots, current job
intelligence and every version a match references stay available.
No cleanup job exists; unreferenced historical versions may remain
indefinitely for now.

## 8. Foreign keys and deletion

| Reference | On delete |
| --- | --- |
| `candidate_profile_versions.user_id` → `users` | CASCADE |
| `job_intelligence.job_id` → `jobs` | CASCADE |
| `job_intelligence.source_posting_id` → `job_source_postings` | SET NULL |
| `job_requirements.job_intelligence_id` → `job_intelligence` | CASCADE |
| `job_matches.user_id` → `users`, `job_matches.job_id` → `jobs` | CASCADE |
| `job_matches (candidate_profile_version_id, user_id)` → `candidate_profile_versions` | NO ACTION |
| `job_matches (job_intelligence_id, job_id)` → `job_intelligence` | NO ACTION |

Deleting a user removes their snapshots and matches; deleting a job removes
its intelligence, requirements and matches (both sides cascade within one
statement, so NO ACTION never blocks that). A snapshot or intelligence
version that a match references cannot be deleted **on its own**: historical
match references always stay valid.

## 9. Migration

`backend/alembic/versions/a56b296b53a5_v0_5_candidate_and_job_intelligence.py`

- create-only: four tables, their indexes and five enum types
  (`candidate_profile_version_reason`, `job_requirement_kind`,
  `job_requirement_category`, `extraction_confidence`, `job_match_verdict`);
- no ALTER of any V0.4 table, no data backfill;
- `downgrade()` drops exactly these objects (the reused `salary_period` type
  stays).

`tests/test_migrations.py::test_v041_data_survives_v05_upgrade_downgrade_upgrade`
runs V0.4.1 data → V0.5.0 → downgrade → upgrade and checks that every
pre-existing table's data fingerprint, columns, constraints, indexes and
enum values are unchanged, and that the new tables, indexes, foreign keys
and enums are exactly as specified.

## 10. Future V0.5 components (not implemented)

Everything below is **future work** — none of it exists in V0.5.0:

- **Snapshot builder**: assembles `CandidateProfileDocument` from the V0.1
  tables and the confirmed Resume Profile (user wins on conflict), computes
  `content_hash`, and stores a version only when the content changed.
- **Skill taxonomy and normalization**: fills `skill_key` /
  `taxonomy_version`. A small deterministic title-synonym vocabulary may
  follow later; no title synonym matching exists.
- **Location vocabulary expansion** (V0.5.1): deterministic city / country /
  remote normalization only — no geocoding or distances.
- **Requirement extraction**: deterministic extraction into `job_facts` and
  `job_requirements`.
- **Extraction review checkpoint** (V0.5.3): a quality gate on extraction
  output before matching relies on it (`verified_at`).
- **Scoring with `match_v1`**: an initial, deterministic and explicitly
  **uncalibrated** model — its weights are not empirically validated. It
  will write `job_matches`; explanation rendering, matching APIs and UI come
  with or after it.
