# V0.6 — Job Matching & Explainability

V0.6 answers *"Why is this job a good or bad match for me?"* — with
evidence, not just a number. For one user and one job it produces:

```
Job Match:         87%     can I realistically do this job?
Preference Match:  72%     does it fit what I want?
Overall Match:     83%     70% Job + 30% Preference
Recommendation:    STRONG_MATCH
+ strong matches, potential gaps, unknowns, deal breakers (structured)
+ an optional, validated AI explanation in plain words
```

**The score is never produced by an AI.** A deterministic engine computes
every number; the AI layer only explains the finished result and is
checked against it.

## Contents

1. [Architecture](#1-architecture)
2. [Job Match](#2-job-match)
3. [Preference Match](#3-preference-match)
4. [Overall Match](#4-overall-match)
5. [Scoring weights](#5-scoring-weights)
6. [Component scoring](#6-component-scoring)
7. [UNKNOWN handling](#7-unknown-handling)
8. [Deal breakers](#8-deal-breakers)
9. [Caps and recommendation thresholds](#9-caps-and-recommendation-thresholds)
10. [Requirement extraction (required / preferred / unspecified)](#10-requirement-extraction)
11. [Evidence model](#11-evidence-model)
12. [AI explanation](#12-ai-explanation)
13. [Prompt versioning](#13-prompt-versioning)
14. [Match versioning](#14-match-versioning)
15. [Stale results](#15-stale-results)
16. [User-specific results](#16-user-specific-results)
17. [Taxonomy integration](#17-taxonomy-integration)
18. [API](#18-api)
19. [Frontend](#19-frontend)
20. [Database and migration](#20-database-and-migration)
21. [Limitations](#21-limitations)
22. [Examples](#22-examples)

## 1. Architecture

```
  V0.1 profile & preferences      confirmed Resume Profile (V0.2)
            \                        /
             candidate snapshot (immutable, content-hashed)     candidate_profile_versions
                         |
                         |        canonical Job (V0.4)
                         |              |
                         |        deterministic extraction    job_intelligence + job_requirements
                         |              |
                         v              v
                       Match Engine (matching_v1, pure function)
                  /              |                 \
           Job Match     Preference Match       Evidence
                  \              |                 /
                       Overall Match + caps
                                 |
                          Recommendation                         job_matches
                                 |
                   AI Explanation Layer (optional)               job_match_explanations
```

Code: `backend/app/services/matching/`

| Module | Role |
| --- | --- |
| `candidate.py` | Builds the candidate snapshot (V0.1 tables + confirmed Resume Profile) |
| `extraction.py` | Canonical job → `JobFacts` + requirements (deterministic) |
| `terms.py` | Finds V0.5.1 taxonomy terms inside posting prose |
| `model.py` | The `matching_v1` model: every weight, threshold and matrix; hashed |
| `components.py` | One deterministic evaluator per component, with evidence |
| `engine.py` | Components → sub-scores → Overall → caps → recommendation |
| `explanation.py` | Deterministic summary; optional, validated AI explanation |
| `status.py` | Staleness and API read models |
| `service.py` | Database orchestration |

The engine (`engine.evaluate`) is a pure function: no database, clock,
randomness or AI. Same inputs, same result — tested.

V0.6 builds on V0.5.0's tables rather than adding new result tables: it is
the first writer of `candidate_profile_versions`, `job_intelligence`,
`job_requirements` and `job_matches`.

## 2. Job Match

*"Can this candidate realistically perform this job?"* — skills,
experience, role/title, seniority, industry experience, languages,
education. Preferences never influence it: a part-time job a full-time
seeker doesn't want has the same Job Match as a full-time one.

## 3. Preference Match

*"Does this job fit what the candidate wants?"* — location (and
relocation), work model, salary, employment type, desired titles,
preferred industries, travel. Deal breakers are evaluated separately (§8).

## 4. Overall Match

```
Overall = round_half_up(0.7 × Job Match + 0.3 × Preference Match)
```

computed from the two **displayed integer** sub-scores, so anyone can
check the arithmetic (87 and 72 → 60.9 + 21.6 = 82.5 → 83).

- No Preference Match (the user stated no preferences at all) → Overall =
  Job Match.
- No Job Match (nothing about the candidate's fit is known) → no Overall
  Match, recommendation `INSUFFICIENT_DATA`.
- Caps (§9) can lower it; the uncapped value is always returned too
  (`overall_match_uncapped`), and the sub-scores are never hidden.

All scores are **integers 0–100**. The model is not precise enough to
justify decimals.

## 5. Scoring weights

The V0.6 prompt suggested one combined list (skills 30, experience 20,
title 10, seniority 10, industry 10, location 5, salary 5, language 5,
education 5) *and* a separate Job/Preference split with 70/30. Both can't
hold at once — in the combined list preferences would weigh only 10%. The
implemented model keeps the suggested **relative** job weights exactly and
gives preferences their own 100-point scale:

| Job Match component | Weight | | Preference Match component | Weight |
| --- | ---: | --- | --- | ---: |
| skills | 30 | | location (incl. relocation) | 25 |
| experience | 20 | | work model | 20 |
| title / role | 10 | | salary | 20 |
| seniority | 10 | | employment type | 15 |
| industry experience | 10 | | desired title | 10 |
| languages | 5 | | preferred industry | 5 |
| education | 5 | | travel | 5 |

Each sub-score is the weighted mean of the components whose status is
known:

```
sub-score = round_half_up(100 × Σ(weight × score) / Σ(weight))   over known components
coverage  = Σ(weight of known components) / Σ(all weights)
```

Inside a component, requirements count by how explicitly the posting asked:
**required 1.0, unspecified 0.7, preferred 0.35**; contextual mentions 0.

All numbers live in `model.py`. The model is explicitly **uncalibrated**:
the weights are product judgement, not fitted to outcome data.

## 6. Component scoring

Each component returns a status — `match` (score ≥ 0.8), `partial`
(0.3 ≤ score < 0.8), `mismatch` (< 0.3) or `unknown` (no score) — plus a
0–1 score and its evidence.

| Component | Rule |
| --- | --- |
| **skills** | Per requirement, by skill taxonomy key: candidate has it → 1.0 (0.6 if both state years and the candidate has fewer); doesn't → 0 (MISSING). "A or B" groups count once, satisfied by either. Ambiguous terms ("GCP") are UNKNOWN items, never scored. Contextual mentions are shown, never scored. |
| **experience** | Candidate years vs. the posting's minimum: ≥ → 1.0; within 1 year → 0.75; ≥ half → 0.4; below half → 0 (gap). The strongest non-preferred statement counts. |
| **title** | Current title vs. job title by taxonomy key (or identical role wording outside the vocabulary): `STRONG_MATCH` 1.0. A previous role from the confirmed resume: `RELATED_ROLE` 0.6. Two *known, different* roles: `WEAK_MATCH` 0. Anything else: UNKNOWN (never literal string equality, never "vaguely related"). |
| **seniority** | Matrix below. |
| **industry** | Folded name equality: 1.0; different: 0.3 (no industry taxonomy yet, so a non-match never scores 0). |
| **languages** | By ISO code: missing → 0; level compared only when both sides state an explicit level (CEFR or native) — native satisfies any CEFR level; below → 0.4. A level that isn't stated on either side is not compared and not penalized. |
| **education** | Highest confirmed degree level vs. the required level: meets → 1.0; below with "or equivalent experience" → 0.5; below → 0. Order: secondary < vocational < associate < bachelor < master < doctorate. |
| **location** | Taxonomy keys only (§17): job in a preferred city / region / country → 1.0; where the candidate lives → 0.9; remote → 1.0 (0.5 if remote is restricted to another country). Another city in the same country: relocation yes 0.7 / maybe 0.5 / unstated 0.4 / no 0. Another country: 0.5 / 0.3 / 0.2 / 0. |
| **work model** | Selected → 1.0; FLEXIBLE on either side → 1.0; hybrid job for a remote-only seeker 0.5, for on-site 0.7; remote job for on-site 0.3, for hybrid 0.6; on-site job for hybrid 0.3, for remote 0. |
| **salary** | Same currency only, annual (monthly ×12; hourly/daily/weekly never converted). Job's top figure ≥ desired → 1.0; ≥ minimum → 0.8; below minimum → 0. |
| **employment type** | Selected → 1.0, otherwise 0. |
| **desired title** | Job title equals a desired title (taxonomy key / identical role): primary 1.0, secondary 0.9; all desired titles known and different → 0; otherwise UNKNOWN. |
| **preferred industry** | As industry. |
| **travel** | Willingness × job travel (none / occasional / frequent): e.g. "no travel" vs. frequent → 0, vs. occasional → 0.4. |

### Seniority compatibility matrix

Rows: the candidate. Columns: what the job asks for.

| | intern | junior | mid | senior | lead | manager | director |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **intern** | 1.0 | 0.6 | 0.2 | 0 | 0 | 0 | 0 |
| **junior** | 0.8 | 1.0 | 0.6 | 0.2 | 0 | 0 | 0 |
| **mid** | 0.5 | 0.8 | 1.0 | 0.6 | 0.3 | 0.2 | 0 |
| **senior** | 0.3 | 0.5 | 0.9 | 1.0 | 0.7 | 0.5 | 0.2 |
| **lead** | 0.2 | 0.4 | 0.7 | 0.9 | 1.0 | 0.7 | 0.4 |
| **manager** | 0.2 | 0.3 | 0.6 | 0.8 | 0.8 | 1.0 | 0.6 |
| **director** | 0.1 | 0.2 | 0.4 | 0.6 | 0.7 | 0.9 | 1.0 |

MID/MID strong; SENIOR/MID 0.9 (fine); JUNIOR/SENIOR 0.2 (significant gap);
far above the job → partial ("overqualified"). Profession-neutral: it
compares levels, never roles.

## 7. UNKNOWN handling

Missing information is **never** a mismatch:

- The job doesn't state salary, industry, experience, work model, … → that
  component is UNKNOWN and excluded from the sub-score (numerator *and*
  denominator).
- The candidate hasn't told JobPilot (no skills, no seniority, no
  preferences) → UNKNOWN as well.
- `coverage` reports how much of each sub-score's weight was evaluable, so
  a 100% based on two components is visibly thin.
- If less than **30%** of the Job Match weight is evaluable, the
  recommendation is `INSUFFICIENT_DATA` rather than a guess.
- Unknowns are listed for the user ("Salary not stated", "Your seniority
  level isn't set"). Preference unknowns are only listed when the user has
  stated that preference (otherwise they don't care).

## 8. Deal breakers

Only **explicitly configured** user constraints can block a job:

| Deal breaker (V0.1 UI label) | Blocks when |
| --- | --- |
| `minimum_salary` "Salary below minimum" | the job's comparable top figure is below the user's minimum |
| `remote_requirement` "Fully on-site" | the job's work model is on-site |
| `employment_type` "Specific employment types" | the job's type is in the deal breaker's value (e.g. `working_student`), or — without a value — not among the user's selected types |
| `location` "Outside preferred locations" | a non-remote job whose known location is outside every preferred location |
| `relocation` "Required relocation" | an on-site/hybrid job in a place the user neither prefers nor lives in |
| `travel` "Frequent travel" | the posting states frequent travel |
| Job Preferences **relocation: no** | same as `relocation` (an explicit answer; reported as `relocation_preference`) |

A deal breaker whose data is missing is **UNKNOWN** (listed, never
blocking). A blocking deal breaker: `HARD_BLOCK` → verdict `BLOCKED`
(`NOT_RECOMMENDED`), Overall capped at 40 (§9). Strong preference
mismatches without a configured deal breaker never block (tested).

## 9. Caps and recommendation thresholds

| Cap | Max Overall | When |
| --- | ---: | --- |
| `hard_block.<type>` | 40 | any deal breaker blocks (also sets `NOT_RECOMMENDED`) |
| `required_missing.<component>` | 69 | a qualification the posting **explicitly marks required** is missing (skill, language, education without "or equivalent", or experience below half) |

Recommendation (first match wins):

| Condition | Recommendation (`job_matches.verdict`) | Label |
| --- | --- | --- |
| any hard block | `NOT_RECOMMENDED` (BLOCKED) | Not recommended |
| no Job Match or job coverage < 30% | `INSUFFICIENT_DATA` | Not enough data |
| Overall ≥ 80 | `STRONG_MATCH` (STRONG) | Strong match |
| Overall ≥ 70 | `GOOD_MATCH` (GOOD) | Good match |
| Overall ≥ 60 | `POTENTIAL_MATCH` (PARTIAL) | Potential match |
| otherwise | `WEAK_MATCH` (WEAK) | Weak match |

Display bands (`SCORE_BANDS`, returned by the API, never re-derived in the
UI): 90–100 Excellent, 80–89 Strong, 70–79 Good, 60–69 Potential, < 60 Weak.

## 10. Requirement extraction

`extraction.py` reads only canonical job fields — never a source's raw
payload — so it doesn't depend on Greenhouse or Lever. Version:
`JOB_EXTRACTOR_VERSION = "job_extractor_v1"`.

**Required vs. preferred is never guessed.** The canonical V0.4
`requirements` list merges "nice to have" sections into requirements, so
the extractor recovers headings from the canonical `description` (headings
are short lines, list items "• " lines):

| Evidence | Kind |
| --- | --- |
| heading "Must have", "Minimum/Basic qualifications", "Required", "Voraussetzungen" | REQUIRED |
| heading "Nice to have", "Bonus", "Preferred qualifications", "Wünschenswert" | PREFERRED |
| any other requirements heading ("Qualifications", "Your profile", "What you bring") | **UNSPECIFIED** |
| responsibilities, other text | CONTEXTUAL (never scored) |
| item markers: "(preferred)", "is a plus", "ideally" / "required", "must", "at least", "mindestens" | override the heading |

`UNSPECIFIED` is a new `job_requirement_kind` value: "asked for, importance
not stated". Mixed items ("Python required, Docker is a plus") are split
into clauses.

Other rules: experience needs a number *and* "experience"/"Erfahrung";
languages need a language cue; a level only from explicit CEFR/"native"
wording; education needs an education cue ("Scrum Master" is not a
degree); travel only from explicit phrases ("up to 40% travel" ≥ 30% =
frequent). Text outside requirement sections counts only when it describes
what the candidate *has* ("You have 5+ years of experience").

Storage conventions on `job_requirements`: SKILL rows in an alternatives
group carry `value_key = "any_of:<n>"`; ambiguous terms are SKILL rows with
`skill_key` NULL; EDUCATION rows carry the level key in `value_key` and
`level = "or_equivalent"` when equivalent experience is accepted; LANGUAGE
rows carry the ISO code in `value_key` and the CEFR level / "native" in
`level` (NULL = not stated); EXPERIENCE rows carry `min_years`. Every row
keeps `evidence_text` (verbatim, ≤ 600 chars) and, for the description, its
`evidence_offset`.

`job_intelligence` is global per job and reused by every user; it is
re-extracted only when the extractor's input (`input_hash`) or the
extractor/taxonomy version changes.

## 11. Evidence model

`job_matches.evidence` (`MatchEvidence`, `match_evidence_v1`) stores every
component:

```json
{
  "key": "skills", "group": "job", "status": "partial", "score": 0.8, "weight": 30,
  "reason_code": "skills_compared",
  "items": [{
    "key": "python", "label": "Python", "importance": "required", "status": "match",
    "score": 1.0, "reason_code": "skill_match",
    "candidate": {"value": "Python", "origin": "user", "ref": "skills/0"},
    "job": {"value": "Python", "source_field": "description", "section": "required",
            "text": "3+ years Python development", "requirement_id": "…"}
  }]
}
```

and every deal breaker (`type`, `source`, `status`: hard_block / clear /
unknown, `reason_code`). Candidate evidence points into the snapshot
(`ref`) and, for resume-derived values, to the Resume Profile item
(`evidence_ref`). Job evidence is the posting's own text — never invented;
facts from structured fields carry their field instead of a quote.
`criteria` and `caps` (V0.5.0 documents) are filled too, with requirement
ids and snapshot refs per component.

From the evidence, `explanation.summarize` derives four lists — **strong
matches, gaps, unknowns, deal breakers** — each item with a stable `id`
(`skills:python`, `salary`, `deal_breaker:relocation`), `kind`,
`component`, `status`, `importance` and display `text`. The frontend reads
state from the structured fields only.

## 12. AI explanation

Optional, on request (`POST /jobs/{id}/match/explanation`), through the
existing `AIProvider` abstraction (Anthropic in production, a fake in
tests; never called by batch matching or on read).

The AI receives only the deterministic result: job title and company, the
three scores, the recommendation code and the four fact lists (ids and
texts). No profile, resume or contact data. It returns
`AIMatchExplanation` — `summary`, `strengths[]`, `concerns[]` (each
referencing a `fact_id`). That schema has no score, percentage or
recommendation field.

Before anything is stored, `validate_ai_explanation` rejects the answer if
it

- references a fact id that wasn't given, or uses a gap as a strength;
- contains any number not in the facts or scores (a different %, invented
  years or salaries);
- names a skill, language or degree that isn't in the facts (the same
  taxonomy term finder the extractor uses);
- names something the candidate lacks in a strength, or in a summary
  sentence without negation ("You bring strong Kubernetes skills");
- claims a different recommendation ("strong match" for a weak one).

A rejected or failed answer is never stored or shown (HTTP 502
`AI_EXPLANATION_REJECTED`, 503 `AI_NOT_CONFIGURED`, …); the deterministic
explanation stands on its own. Accepted answers are stored once per match
and prompt version and reused. Logs contain only error codes and exception
types.

## 13. Prompt versioning

`MATCH_EXPLANATION_PROMPT_VERSION = "matching_explanation_v1"` names the
prompt file `backend/app/ai/prompts/matching_explanation_v1.txt` (the V0.2
convention: never edited in place; a change gets a new file and version).
Every stored explanation records `prompt_version`, `ai_provider` and
`ai_model`; `UNIQUE (job_match_id, prompt_version)`.

## 14. Match versioning

Every `job_matches` row pins:

| What | Where |
| --- | --- |
| candidate inputs | `candidate_profile_version_id` (immutable snapshot, content-hashed) |
| job inputs | `job_intelligence_id` (`extractor_version`, `taxonomy_version`, `input_hash`) |
| algorithm | `model_id = "matching_v1"`, `model_hash` = SHA-256 of `model.model_definition()` |
| taxonomy | `taxonomy_version = "taxonomy_v1"` |

`tests/test_matching_scores.py` pins `MODEL_HASH`: changing any weight or
threshold without bumping `MATCHING_ALGORITHM_VERSION` fails the test.

## 15. Stale results

A stored result is **stale** when, compared with today:

| Reason | Check |
| --- | --- |
| `profile_changed` | the snapshot built from the current profile, preferences and confirmed resume has a different content hash |
| `job_changed` | the job's extractor input hash or the extractor version differs |
| `algorithm_changed` | `model_id` / `model_hash` differ from the current model |
| `taxonomy_changed` | `taxonomy_version` differs |

Stale results are returned with `status: "stale"` and the reasons — never
presented as current — and are recalculated only on an explicit
"Recalculate" (no background worker). Recalculating stores a new row and
moves `is_current`; the old one stays as history. Identical inputs are
never stored twice.

## 16. User-specific results

`job_matches` is keyed by `(user_id, job_id)` — the natural key of
`user_jobs`. Results are only computed and read through the user's own
`UserJob` (`get_visible_job`): another user's job or match is a 404,
exactly like a non-existent one, and batch requests report foreign ids as
`not_found`. The canonical `Job` has no score; the same job can be 91% for
one user and 54% for another (tested). No database foreign key to
`user_jobs` was added, to leave the V0.5.0 constraints (and their tests)
unchanged.

The candidate snapshot (`candidate_profile_versions`) is V0.5.0's read
model, now built by `candidate.py`: V0.1 data (origin "user") plus the
**most recently confirmed** Resume Profile's accepted/edited items (origin
"resume", with `evidence_ref`). The user's value wins on conflict; the
resume is never re-parsed. V0.6 adds three optional document fields —
`role.previous_titles` (resume work history), `role.industries` (worked
in) and `preferences.industries` (wanted) — additively within
`candidate_profile_v1` (no snapshot rows existed before V0.6, the
precedent V0.5.1 set). `JobFacts` gains `title` and `industry` likewise.

## 17. Taxonomy integration

The matcher compares **V0.5.1 taxonomy keys**, never raw strings, and adds
no alias system of its own:

| Aspect | Taxonomy use |
| --- | --- |
| skills | `normalize_skill` for candidate skills; `TermFinder(SKILLS)` finds aliases in posting prose ("React.js" → `react`, "Postgres" → `postgresql`; Java ≠ JavaScript) |
| languages | `normalize_language` / `normalize_language_level`; no CEFR from "fluent" |
| education | `normalize_education_level`; levels ordered by `EDUCATION_LEVELS` |
| titles | `normalize_title` (seniority words, gender markers, qualifiers peeled off) |
| locations | `normalize_location` → `city:de:cologne`, `country:de`, regions; no geocoding |

Ambiguous aliases and terms (Alias `ambiguity`, `AmbiguousTerm` such as
"SAP", "GCP") are never resolved: they appear as UNKNOWN items. Nothing in
the code names a profession, vendor or product.

## 18. API

| Method | Path | |
| --- | --- | --- |
| `GET` | `/jobs/{job_id}/match` | `{status: not_calculated \| calculated \| stale, result}`; never calculates |
| `POST` | `/jobs/{job_id}/match` | calculate / recalculate (deterministic) |
| `POST` | `/jobs/match` | batch: `{job_ids?: ≤25, limit?: 1-25, force?}`; deterministic only |
| `POST` | `/jobs/{job_id}/match/explanation` | AI explanation of an up-to-date result (409 if none/stale) |
| `GET` | `/jobs`, `/jobs/{id}` | each job now carries `match` (summary or `null`); `sort=match` = best Overall first |

All require authentication and work on the user's own jobs only.

## 19. Frontend

- **Jobs list** (`JobCard` → `MatchBadge`): "87% Overall match", the
  recommendation, and "Job match 94% · Preference match 71%" — or "Match not
  calculated"; "Outdated" for stale results. "Calculate matches" runs the
  batch for the page; "Sort: Best match".
- **Job details** (`MatchPanel`): states *not calculated*, *calculating*,
  *calculated*, *stale* (with reasons and "Recalculate") and *error* (with
  retry; no endless spinners). Overall / Job / Preference, recommendation
  and band, deal breakers, "Why this matches", "Potential gaps", "Unknown"
  (6 items, expandable), an expandable "detailed evidence" section with the
  posting quotes and versions, and the optional "Explain in plain words
  (AI)" button.

## 20. Database and migration

Migration `c7d2e9f4b1a6` (parent: V0.5.0 `a56b296b53a5`) — needed for the
two sub-scores, evidence and AI explanations:

- `job_requirement_kind` + `UNSPECIFIED`;
- `job_matches` + `job_score`, `preference_score` (0–100), `job_coverage`,
  `preference_coverage` (0–1), `taxonomy_version`, `evidence` (JSONB,
  default `{}`), with CHECK constraints;
- new table `job_match_explanations` (`job_match_id` → `job_matches` ON
  DELETE CASCADE, `prompt_version`, `ai_provider`, `ai_model`, `content`,
  `created_at`; `UNIQUE (job_match_id, prompt_version)`; write-once).

Additive; existing rows are unchanged. `downgrade()` drops the table and
columns and recreates the enum without `UNSPECIFIED`, first deleting the
derived fact sheets that use it (and their matches) — derived data that
V0.6 recomputes. `tests/test_migrations.py` covers V0.5 → V0.6 → V0.5 →
V0.6 with data, and that the migrated schema equals the models.

## 21. Limitations

- **Uncalibrated.** Weights and thresholds are judgement, not learned from
  outcomes.
- **Small vocabularies.** Skills, titles and locations outside the V0.5.1
  taxonomy can't be compared: they become UNKNOWN (often lowering coverage
  rather than the score). Title relatedness only comes from the resume
  history; "Software Developer" vs. "Software Engineer" is UNKNOWN.
- **No industry taxonomy**: industries match by folded name only.
- **No geography.** No geocoding or distances: a neighbouring city is a
  different city (add it to preferred locations). Hybrid jobs in another
  city count as needing relocation.
- **Required vs. preferred** depends on the posting's headings/markers in
  the canonical description. Lever "nice to have" *lists* lose their
  heading in V0.4's canonical lists, so their items are UNSPECIFIED.
- **Prose extraction is conservative**: verbs ("you excel at"), ordinary
  words and lower-case ambiguous acronyms are skipped, so some real
  requirements are missed (missed = unknown, never a false gap).
- Experience is total years, not years in the specific field.
- Salary needs the same currency and a yearly/monthly period; no external
  salary data.
- Work authorization is not evaluated (no user field exists yet).
- No background recalculation: stale results wait for "Recalculate".
- The AI validator is lexical; it rejects conservatively (a valid but
  unusual phrasing may be discarded — the deterministic explanation always
  remains).

## 22. Examples

Strong fit (`tests/test_matching_scores.py`): senior software engineer, 6
years, Python + SQL, English C1, wants Cologne hybrid full-time ≥ €70k; job
"Senior Software Engineer", Köln, hybrid, €75–85k, requires 3+ years,
Python and SQL, Kubernetes nice to have → Job ≈ 92, Preference ≈ 96,
Overall ≈ 93, `STRONG_MATCH`; gap: "Kubernetes (nice to have)"; unknowns:
industry, education.

Same candidate, relocation "no", job on-site in München → Job ≈ 92,
Preference ≈ 68, Overall 85 → capped **40**, `NOT_RECOMMENDED`, deal
breaker "Requires relocation, and you don't want to relocate".

Domain-neutral scenarios in the tests: accountant (IFRS, bachelor's),
food safety manager (HACCP, SQF required → capped), maintenance technician
(PLC, forklift), supply chain planner.
