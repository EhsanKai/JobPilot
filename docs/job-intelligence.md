# Job Intelligence (V0.4) — Canonical Jobs & Cross-Source Deduplication

V0.4 makes JobPilot understand that several postings from different sources
can be **one real-world job**, without ever throwing a source posting away.

```
Greenhouse                              Lever
  Senior SAP ABAP Developer               Senior SAP ABAP-Developer (m/w/d)
  Example GmbH                            Example GmbH
  Cologne, Germany                        Köln
  job-boards.greenhouse.io/example/...    jobs.lever.co/example/...
                  \                         /
                   ▼                       ▼
                 ONE canonical Job  (stable id)
                   ├── Greenhouse posting  (original URL kept)
                   └── Lever posting       (original URL kept)
```

Everything here is **deterministic**: no AI, no embeddings, no external
calls. The same input always produces the same normalization, the same
hash and the same decision. AI matching is deliberately V0.5 (§17).

Contents

1. [Canonical Job](#1-canonical-job)
2. [Source Posting](#2-source-posting)
3. [URL normalization](#3-url-normalization)
4. [Text normalization](#4-text-normalization)
5. [Company normalization](#5-company-normalization)
6. [Location normalization](#6-location-normalization)
7. [Content hashing](#7-content-hashing)
8. [Cross-source deduplication](#8-cross-source-deduplication)
9. [Confidence rules](#9-confidence-rules)
10. [False-positive protection](#10-false-positive-protection)
11. [Canonical Job selection](#11-canonical-job-selection-preferred-posting)
12. [Source preservation](#12-source-preservation)
13. [Freshness](#13-freshness)
14. [Concurrency](#14-concurrency)
15. [Migration strategy](#15-migration-strategy)
16. [Compatibility with Application Tracking](#16-compatibility-with-application-tracking)
17. [Why AI matching is deferred to V0.5](#17-why-ai-matching-is-deferred-to-v05)
18. [Other normalization: employment type, work model, salary](#18-other-normalization)
19. [Discovery statistics](#19-discovery-statistics)
20. [API and UI](#20-api-and-ui)
21. [Code map, tests, limitations](#21-code-map-tests-limitations)
22. [V0.4.1 — Live source validation](#22-v041--live-source-validation)

---

## Architecture

```
JobSource (Greenhouse, Lever, ...)            V0.3, unchanged contract
    │  RawJob (source-shaped)
    ▼
JobNormalizer                                 V0.3 + V0.4 normalization modules
    │  NormalizedJob (one posting, normalized)
    ▼
Source Posting  (job_source_postings)         V0.4: identity, original URL,
    │                                                raw + normalized data, freshness
    ▼
Cross-source identity resolution              V0.4: EXACT / HIGH_CONFIDENCE /
    │                                                POSSIBLE_MATCH / NO_MATCH
    ▼
Canonical Job  (jobs)                         stable id, canonical content,
    │                                                comparison keys
    ▼
UserJob  (user_jobs)                          per-user: discovered, saved
    │
    ▼
Application (applications) → ApplicationStatusHistory     future
```

Four separate concepts, four tables — never collapsed:

| Concept        | Table                 | Scope    | Answers                                 |
|----------------|-----------------------|----------|-----------------------------------------|
| Source Posting | `job_source_postings` | global   | *Where* was the job found, and as what? |
| Canonical Job  | `jobs`                | global   | *What* is the vacancy?                  |
| UserJob        | `user_jobs`           | per user | What did *this user* do with it?        |
| Application    | `applications`        | per user | (future) Applying to it                 |

## 1. Canonical Job

`jobs` holds one row per real-world vacancy. Its `id` is stable for life:
once established it never changes, however many sources later turn out to
list the same vacancy. `user_jobs.job_id` and `applications.job_id` point at
it.

The Job's content columns (title, company, location(s), description,
requirements, responsibilities, technologies, salary, employment type, work
model, seniority, industry, posted date, `canonical_url`, `apply_url`) are a
**projection of its preferred source posting** (§11). Source identity, raw
payloads and content hashes moved to the postings in V0.4.

New queryable, deterministic comparison columns (V0.5 can use them without
knowing any source's schema):

| Column                    | Example                       |
|---------------------------|-------------------------------|
| `company_name`            | `Example GmbH` (display)      |
| `normalized_company_name` | `example`                     |
| `title` / `normalized_title` | `Senior SAP ABAP Developer` / `senior sap abap developer` |
| `location` / `normalized_location` | `Cologne, Germany` / `de:cologne` |
| `city`                    | `Cologne`                     |
| `salary_text`             | `€60k - €70k + bonus` (as written) |

## 2. Source Posting

`job_source_postings` — one row per posting at one source:

| Column | Meaning |
|---|---|
| `id`, `job_id` | the posting, and the canonical Job it belongs to |
| `source`, `source_job_id` | e.g. `lever` / `5ac2…`; **unique together** (partial index, legacy rows excluded) |
| `dedup_key` | **unique**; `source:id:<id>` or `source:url:<sha256>` (V0.3 rules) |
| `source_company_key` | board/site within the source (Greenhouse board token, Lever site) |
| `source_url` | the URL exactly as the source returned it |
| `canonical_source_url` | the same URL normalized but still clickable (§3) |
| `apply_url` | the source's application URL, if it has one |
| `title`, `company_name`, `location` | what *this source* said, as written |
| `posted_at`, `source_updated_at` | the source's timestamps |
| `url_key`, `normalized_title`, `normalized_company_name`, `normalized_location`, `identity_version` | comparison keys (§3–§6) |
| `normalized_data` | this posting's own normalized content (JSON) |
| `raw_data` | the source's payload — **never returned by the API** |
| `content_hash`, `normalizer_version` | change detection (§7) |
| `first_seen_at`, `last_seen_at`, `is_active` | freshness (§13) |
| `created_at`, `updated_at` | row timestamps |

Each posting keeps its own normalized snapshot so that (a) the canonical
Job can be rebuilt from whichever posting is preferred and (b) a new posting
is compared against **every** posting of a job, not only the canonical text.

## 3. URL normalization

`normalization/url.py` produces two different things on purpose:

| Function | Used for | Changes |
|---|---|---|
| `canonicalize_url` | stored, clickable `canonical_source_url` / `Job.canonical_url` | lowercase scheme+host, drop default port, drop `#fragment`, drop known tracking parameters |
| `url_identity_key` | deduplication only (never opened) | all of the above **plus** `http` ≡ `https` and trailing `/` ignored |

```
https://example.com/job/123
https://example.com/job/123/
http://example.com/job/123#apply
https://EXAMPLE.com:443/job/123?utm_source=x&utm_campaign=y
        → identity key  https://example.com/job/123
```

Removed query parameters — **only** known tracking parameters: `utm_*`
(`utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, …),
`gclid`, `fbclid`, `msclkid`, `dclid`, `mc_cid`, `mc_eid`, `_hsenc`,
`_hsmi`, `igshid`. Every other parameter is kept byte-for-byte and in order
(`?gh_jid=4012346` selects the posting on a company careers page).
Non-default ports stay significant. The stored link keeps its scheme and
trailing slash: an http-only site must stay reachable, so the semantic
destination of a link is never changed.

Deduplication never fetches a URL. It only compares URLs that validated
JobSource connectors already returned.

## 4. Text normalization

`normalization/text.py`:

- **Display cleaning** (`clean_text`, `clean_multiline`, `clean_list`):
  whitespace, non-breaking and zero-width characters, blank-line runs.
  Wording, case and accents are kept.
- **Comparison folding** (`fold`): HTML entities/tags removed, Unicode NFKC
  (full-width letters, ligatures), case-folded, diacritics removed
  (`Köln`→`koln`, `ß`→`ss`, `ø`→`o`), `&`→`and`, hyphens/dashes/slashes and
  punctuation → spaces, whitespace collapsed. `+` and `#` survive inside
  words so `C++`/`C#` ≠ `C`.
- **Title key** (`title_key`): `fold` plus removal of German gender markers
  (`(m/w/d)`, `(w/m/d)`, `(all genders)`, …) and expansion of `Sr.`/`Jr.`.

```
"Senior SAP ABAP Developer"
"Senior SAP ABAP-Developer"            →  "senior sap abap developer"
"senior sap abap developer (m/w/d)"
"Sr. SAP ABAP Developer"

"Senior SAP Developer"  ≠  "SAP Developer"        (no meaningful word is removed)
```

## 5. Company normalization

`normalization/company.py` — `company_key` is a comparison aid; the original
name is always kept for display.

- Folded as in §4, then **trailing** legal-form suffixes are removed,
  repeatedly, only while something remains: GmbH, gGmbH, mbH, AG, SE, KG,
  KGaA, UG (haftungsbeschränkt), OHG, e.V., GmbH & Co. KG, & Co., Ltd,
  Limited, Inc, Incorporated, LLC, LLP, PLC, Corp, Corporation, Co, Pty
  (Ltd), B.V., N.V., S.A., SAS, SARL, S.r.l., S.p.A., Oy, Oyj, AB, ASA, ApS,
  sp. z o.o.
- Words like *Group*, *Holding*, *Systems*, *Digital* are kept — they often
  distinguish separate legal entities.
- Keys are compared for **exact equality only**; similar-looking names are
  never treated as the same company.

```
"Example GmbH" / "EXAMPLE GMBH" / "Example GmbH."   →  "example"
"Example Systems GmbH"                              →  "example systems"   (different)
```

Limitation: Lever's API has no company-name field, so the configured
display name (`LEVER_SITES=site:Display Name`) is used. Configure the same
display name on both sources for cross-source matching to work.

## 6. Location normalization

`normalization/location.py` — an explicit vocabulary, no geocoding:

```
"Köln" / "Koeln" / "Cologne" / "Cologne, Germany" / "Köln, DE"
"Cologne, North Rhine-Westphalia, Germany"           →  de:cologne  (city Cologne, DE)
"Bonn"                                               →  de:bonn     (never Cologne)
"München"                                            →  de:munich
"Remote - Germany" / "Germany (Remote)"              →  remote:de
"Remote" / "100% Remote"                             →  remote
"Springfield, IL"                                    →  text:springfield il   (not in vocabulary)
```

Rules:

- A city is recognized only through `CITIES`, whose aliases (local and
  English spellings, ae/oe/ue transliterations) are spelled out. Cities are
  never merged by proximity.
- Any part the vocabulary doesn't know, more than one city, or a country
  contradicting the city → the location falls back to its folded text
  (`text:…`) — only identical text matches then.
- Country names (English + local) are recognized. Two-letter codes only
  *confirm* a recognized city's country (`Köln, DE`): on their own they are
  ambiguous with US states (`Wilmington, DE` is Delaware).
- `Hybrid` is a work model, not a place: it doesn't change the key.
  `Remote` does — a remote role and an office role in the same city are
  kept apart.
- The original text is always kept (`Job.location`, `JobSourcePosting.location`).

Deduplication compares the **primary** location of each posting.

## 7. Content hashing

`normalization/content.py` — `content_hash` is a SHA-256 over the posting's
*meaningful* content, in folded form:

| Included | Excluded |
|---|---|
| title key, company key, location + location keys, description, requirements, responsibilities, department, salary (min/max/currency/period + original text), employment type, work model, URL identity key, the raw source URL (shown as the original link), apply URL | `discovered_at`, `first/last_seen_at`, the source's posted/updated timestamps, raw payloads |

Formatting-only changes (spacing, case, `-` vs `–`) don't change the hash;
a changed description does. When a refresh sees a different hash (or a newer
`normalizer_version`, now `job_normalizer_v2`), the posting is re-stored and
counted as `updated`, and its canonical Job is re-derived (§11). The
source's timestamps are refreshed on every sighting without counting as a
change.

## 8. Cross-source deduplication

For each **new** posting (unknown `dedup_key`), within one transaction:

```
1. lock the identity block (company key + title key)        §14
2. candidates = postings with the same company key AND title key
3. compare(new posting, each candidate posting)            §9
4. a job's decision = best decision over its postings
5. exactly one job at EXACT/HIGH_CONFIDENCE → attach (merged)
   otherwise                                → new canonical Job (created)
```

A **known** posting (same `dedup_key`) is simply refreshed — V0.3's
source-level identity. A posting never moves to another Job once attached.

**Description similarity** (`deduplication/similarity.py`): Jaccard
similarity of word 3-gram ("shingle") sets of the two normalized bodies
(description + responsibilities + requirements, folded):

```
J(A, B) = |shingles(A) ∩ shingles(B)| / |shingles(A) ∪ shingles(B)|
```

3-grams keep a little word order — two different vacancies at one company
share vocabulary (boilerplate, tech stack) but much less word *sequence*.
Sets make it robust to repeated text (Greenhouse puts requirements inside
the description; Lever lists them separately). Linear time, no
dependencies, deterministic.

## 9. Confidence rules

`deduplication/resolver.py` — first rule that applies wins:

| # | Condition | Decision |
|---|---|---|
| 1 | same source (different ids) | `NO_MATCH` — the source's own ids say: two vacancies |
| 2 | a company key or title key is empty | `NO_MATCH` |
| 3 | company keys differ, or title keys differ | `NO_MATCH` |
| 4 | same URL identity key | **`EXACT`** |
| 5 | primary location missing on either side | `POSSIBLE_MATCH` |
| 6 | location keys differ | `NO_MATCH` |
| 7 | either body has < **30** words | `POSSIBLE_MATCH` (too little text to confirm) |
| 8 | similarity ≥ **0.80** and no conflicting facts | **`HIGH_CONFIDENCE`** |
| 9 | similarity ≥ 0.80 but conflicting facts | `POSSIBLE_MATCH` |
| 10 | similarity ≥ **0.50** | `POSSIBLE_MATCH` |
| 11 | otherwise | `NO_MATCH` |

*Conflicting facts*: both postings state an employment type and they
differ; both state a work model and they differ; both state a salary and
the currencies or periods differ or the ranges don't overlap.

Lifting to jobs (`resolve`):

- a job that already has a posting from the incoming source can receive at
  most `POSSIBLE_MATCH` (that source already lists the job under another id);
- exactly one job at `EXACT` → merge there (EXACT outranks HIGH);
  otherwise exactly one job at `HIGH_CONFIDENCE` → merge there;
- two or more jobs at the top level → `POSSIBLE_MATCH` (ambiguous: never guess);
- **only `EXACT` and `HIGH_CONFIDENCE` merge automatically.**

Thresholds are constants (`HIGH_CONFIDENCE_SIMILARITY = 0.80`,
`POSSIBLE_MATCH_SIMILARITY = 0.50`, `MIN_BODY_WORDS = 30`) and covered by
tests. Example: the Greenhouse and Lever postings of the SAP job in the
tests share company/title/location keys and reach a similarity well above
0.80 → `HIGH_CONFIDENCE`.

## 10. False-positive protection

Wrongly merging two vacancies is far worse than showing a duplicate: the
user's saved state and, later, applications attach to the canonical Job, so
a bad merge could mean the wrong application URL, salary, location or
status. Therefore:

- **Never sufficient alone**: same title; same company + title; same
  company + location. Company *and* title *and* location must match, plus
  very similar text (or an identical posting URL with identical company and
  title).
- `Senior SAP ABAP Developer — Cologne / Munich / Remote` at one company are
  three jobs (different location keys).
- Same company/title/location with a substantially different description →
  separate jobs.
- Conflicting structured facts (full-time vs working student) → not merged.
- Ambiguity (two candidate jobs) → not merged.
- `POSSIBLE_MATCH` is counted (`possible_matches`) but never merged.
- A shared generic URL (e.g. every posting linking to `/careers`) can't
  merge different titles — EXACT also requires equal company and title keys.
- No automatic un-merge/re-merge: once attached, a posting stays with its Job.

## 11. Canonical Job selection (preferred posting)

`deduplication/merger.py` — when a Job has several postings, one is
**preferred**: it supplies the canonical content and is the posting future
application automation (V0.10+) should use (`jobs.preferred_source_posting_id`).

Policy, first difference wins:

1. **active** postings before inactive ones (§13);
2. **source reliability**: the employer's own ATS board (Greenhouse, Lever)
   before job-search APIs before web pages; unknown sources last
   (`SOURCE_RELIABILITY`);
3. an **application URL is available**: an explicit apply URL, or a source
   whose posting page hosts the application form (Greenhouse, Lever);
4. **source priority** (`SOURCE_PRIORITY = greenhouse, lever`);
5. **stable fallback**: earliest `first_seen_at`, then posting id.

Canonical content = the preferred posting's content. Fields it leaves empty
are filled from the next posting in preference order that states them;
salary is one group (min/max/currency/period/text never mix across
postings). `discovered_at` only ever moves earlier; `last_seen_at` is the
latest sighting of any posting. The Job's `id` never changes.

## 12. Source preservation

Nothing about a source posting is discarded by a merge: every posting keeps
its source, source id, board, original URL, apply URL, title/company/location
as written, timestamps, raw payload and normalized snapshot. `GET
/jobs/{id}` and `GET /jobs/{id}/sources` list all of them, preferred first:

```
Found on:
• Greenhouse (example)   Preferred · Active     View original posting →
• Lever (example)        Active                 View original posting →
```

## 13. Freshness

Per posting:

- `first_seen_at` / `last_seen_at` — first/latest discovery run that
  received the posting from its source. V0.4 counts **every valid posting
  the source listed**, including ones filtered out by that run's criteria.
- `is_active` — set to false only when a **complete** board listing no
  longer contains the posting. A board counts as complete when it was read
  without error, had no unreadable/invalid entries and wasn't cut off by
  `JOB_SOURCE_MAX_JOBS_PER_BOARD`. Failed boards, partial reads and
  criteria filters never deactivate anything. Seen again → active again.
- `status` (API, computed): `active`; `stale` — not received for more than
  `JOB_POSTING_STALE_DAYS` (default 14); `inactive` — no longer listed.
- `seen_in_latest_run` (API, per user) — whether the user's latest finished
  run received the posting (null if that run didn't query its source).

A Job is `is_active` while any of its postings is. **Nothing is deleted** —
not jobs, not postings — because a source stopped returning them.

## 14. Concurrency

Guarantees are enforced by Postgres, not by Python checks:

- `job_source_postings.dedup_key` **UNIQUE** and (`source`,
  `source_job_id`) **UNIQUE** (partial). Inserts use `INSERT … ON CONFLICT
  DO NOTHING`: two runs can never store one posting twice.
- A new canonical Job is created **in a savepoint together with its first
  posting**. If the posting insert hits the unique constraint (a concurrent
  run won), the savepoint rolls back — no posting-less duplicate Job — and
  the existing posting is refreshed instead.
- **Identity locks.** Every automatic merge requires equal company key and
  title key, so (company key, title key) — the *block key* — partitions
  postings into groups that can never merge across. Before storing a
  source's postings, a run takes a transaction-scoped advisory lock
  (`pg_advisory_xact_lock`) for each block key it touches (new keys and the
  postings' stored keys), **in ascending lock-id order** (no lock-order
  deadlocks), held until commit. Two runs that could create or merge the
  same vacancy are serialized; unrelated jobs proceed in parallel. The
  second run then sees the first run's committed Job and merges into it.
- Freshness updates use `SELECT … FOR UPDATE SKIP LOCKED`, so they never
  wait on (or deadlock with) a run that is busy with the same rows.
- A transaction Postgres aborts as a deadlock victim or serialization
  failure is retried (up to 3 attempts, from scratch).

Tested with simultaneous runs (both sources each, and Greenhouse vs Lever
in parallel) and with a deterministic test that holds the identity lock and
proves the second run waits and then merges.

## 15. Migration strategy

Migration `d4a7e3b9c2f1_v0_4_job_intelligence` (revises V0.3's `c3f9d2e4a8b1`):

**Upgrade**

1. Create `job_source_postings` with its constraints and indexes.
2. For every existing job, insert **one posting** carrying its V0.3 identity
   (source, source id, `dedup_key` — including `legacy:<id>` keys — board,
   source URL, canonical URL, apply URL, title/company/location, posted and
   source-updated timestamps, raw payload, content hash, normalizer version;
   `first_seen_at = discovered_at`, `last_seen_at = last_seen_at`).
3. Add `preferred_source_posting_id` (FK, `ON DELETE SET NULL`),
   `salary_text`, `normalized_title`, `normalized_company_name`,
   `normalized_location`, `city` to `jobs`; point each job at its posting.
4. Drop the identity/provenance columns from `jobs` (now on the postings).

Job ids, `user_jobs` (incl. `saved_at`), `applications.job_id`, timestamps
and canonical URLs are untouched. No enum type is created, altered or
dropped. Nothing is invented: comparison keys and the posting snapshot stay
NULL in SQL; application code computes keys before the next resolution
(`persistence.backfill_identities`, run at the start of every discovery —
also how a future `IDENTITY_VERSION` bump re-keys everything) and
re-stores snapshots when sources return the postings (they count as
`updated` once, because `normalizer_version` moved to v2).

**Downgrade** — V0.3 can represent one source per job: a Job with several
postings keeps its id and its preferred posting's identity; each other
posting is split back out into its own V0.3 job row (content from its own
snapshot where available), users who had the merged Job get a (not saved)
`user_jobs` row for it, and applications stay on the original Job. No source
posting is lost.

Verified by `tests/test_migrations.py` (scratch database): V0 data → head;
V0.3 data → upgrade → downgrade → upgrade with identical data and enum
values; merged V0.4 data → downgrade; migrated schema == ORM models.

## 16. Compatibility with Application Tracking

```
Canonical Job ──┬── Greenhouse posting
                ├── Lever posting
                └── future source posting
        │
        ▼
     UserJob  (saved_at, discovered)
        │
        ▼
   Application  (applications.job_id → jobs.id)       exists since V0
        │
        ▼
ApplicationStatusHistory                              future
```

- Applications reference the canonical Job, whose id never changes;
  refreshing or merging postings never touches `user_jobs` or
  `applications` (tested).
- The preferred posting (and its `apply_url` / hosted application page) is
  the target for browser automation (V0.10+); every other original URL
  remains available.
- Full application tracking is intentionally not part of V0.4.

## 17. Why AI matching is deferred to V0.5

V0.5 will compute Job Match %, Preference Match % and Overall Match % from
the user's profiles and the canonical Job. That only makes sense once:

- a vacancy is **one** record (no double-scored duplicates),
- its fields are normalized into JobPilot's vocabularies, and
- its identity is stable (scores and applications attach to it).

V0.4 provides exactly that, deterministically and cheaply, and keeps
source-specific schemas out of V0.5's way (`jobs` has everything V0.5
needs). Using an LLM or embeddings for *identity* would make deduplication
non-deterministic, expensive and hard to test — and wrong merges are costly
(§10). V0.4 therefore contains no match scores, no preference scoring, no
AI recommendations and no LLM calls.

## 18. Other normalization

**Employment type** (`normalization/employment.py`, patterns in
`vocabulary.py`): `full-time`, `Full Time`, `FULL_TIME`, `Vollzeit` →
`FULL_TIME`; likewise `PART_TIME`/`Teilzeit`, `WORKING_STUDENT`/`Werkstudent`,
`CONTRACT`, `INTERNSHIP`/`Praktikum`, `TEMPORARY`/`befristet`, `FREELANCE`.
Unknown values stay unknown; no new types are invented.

**Work model** (`normalization/work_model.py`): `Remote`, `100% Remote`,
`Home Office` → `REMOTE`; `Hybrid`, `Hybrid work` → `HYBRID`; `On-site`,
`Vor Ort` → `ONSITE`. Only the explicit value, title and location text are
read — never the description ("flexible hours" doesn't make a job remote).

**Salary** (`normalization/salary.py`): min, max, currency, period.
Structured source amounts win; text is parsed only when unambiguous:

```
"€70,000 - €90,000 per year"   → 70000 / 90000 EUR, year
"up to €90,000"                → max 90000 EUR
"CHF 9'500 per month"          → 9500 CHF, month
"€60k - €70k + €10k bonus"     → not structured (3 amounts), text kept
"Competitive salary"           → NULL, text kept
```

The source's wording is kept in `salary_text` and shown when no structured
salary exists. Values are never invented.

## 19. Discovery statistics

Per source and in total (`DiscoveryCounts`):

| Counter | Meaning |
|---|---|
| `fetched` | postings received from the source |
| `invalid` | rejected by validation |
| `duplicates` | same posting (same source + id) twice in one run |
| `filtered_out` | valid, but not matching the criteria |
| `beyond_limit` | matching, beyond the requested page/limit |
| `skipped` | `invalid + duplicates + filtered_out + beyond_limit` |
| `created` | new postings that became a **new canonical job** |
| `merged` | new postings attached to an existing job found by another source (EXACT/HIGH) |
| `updated` | known postings whose content changed |
| `unchanged` | known postings seen again unchanged |
| `possible_matches` | new postings that resembled an existing job but were kept separate (subset of `created`) |
| `cross_source_matches` | stored postings whose job has postings from ≥ 2 sources, at the time the source was processed |
| `error_count` | problems reported by the source |
| `discovered` | **distinct** canonical jobs stored for the user |

Per source: `fetched = skipped + created + merged + updated + unchanged`.
The run total of `discovered` is **not** the sum of the sources'
`discovered` when sources share jobs (the example: Greenhouse 1 created,
Lever 1 merged → total `discovered` = 1).

## 20. API and UI

API (all authenticated; users only see jobs they have a `UserJob` for):

- `GET /api/v1/jobs` — one item per canonical job; new fields `sources`
  (preferred first), `source_count`, `is_active`; `source` (preferred
  posting's source) and `canonical_url` kept. The `source` filter matches a
  job when **any** posting is from that source.
- `GET /api/v1/jobs/{id}` — adds `source_postings`,
  `preferred_source_posting_id`, `city`, `salary_text`; V0.3 fields kept
  (`source_job_id`, `source_url`, … now from the preferred posting).
- `GET /api/v1/jobs/{id}/sources` — **new**: the source postings alone.
- Exposed per posting: id, source, source id, board, `original_url`,
  `canonical_url`, `apply_url`, title/company/location as written, posted
  date, first/last seen, `is_active`, `status`, `is_preferred`,
  `seen_in_latest_run`. **Never** exposed: raw payloads, normalized
  snapshots, comparison keys, hashes, match diagnostics, other users' state.

UI:

- `/jobs` — one card per canonical job: `via Greenhouse`,
  `Sources: Greenhouse + Lever`, or `Sources: 3`; "No longer listed" badge.
  The discovery summary mentions postings recognized across sources and
  possible matches kept apart.
- `/jobs/[id]` — **JobPilot data** (normalized, "Preferred source",
  "Found on"), **Sources** (each posting with status, as-written title and
  location, first/last seen, its own "View original posting →" link), and
  **Original job posting** (the preferred posting's text).

## 21. Code map, tests, limitations

```
app/models/job.py                    canonical Job
app/models/job_source_posting.py     Source Posting
app/services/job_discovery/
    normalizer.py                    RawJob -> NormalizedJob (composes the modules below)
    normalization/                   url, text, company, location, salary,
                                     employment, work_model, content
    deduplication/                   identity, similarity, resolver, merger, snapshot
    persistence.py                   upsert posting + resolution, locks, freshness, backfill
    discovery_service.py             orchestration, statistics, retries
    job_service.py                   list/detail/sources API logic
alembic/versions/d4a7e3b9c2f1_*      migration
```

Tests:

| Area | File |
|---|---|
| URL/text/company/location/vocabulary/salary/hash | `tests/test_job_intelligence_normalization.py` |
| Decision model, thresholds, similarity | `tests/test_job_deduplication.py` |
| End to end: merge, false positives, saved state, applications, freshness, legacy backfill, concurrency, API | `tests/test_cross_source_discovery.py` |
| Migrations (V0 → head, V0.3 roundtrip, downgrade split, schema drift) | `tests/test_migrations.py` |
| UI | `frontend/__tests__/MultiSourceJobs.test.tsx` |
| Real browser | `frontend/e2e/multi-source.e2e.ts` (+ `backend/e2e/serve.py`) |

Real-browser E2E: `cd frontend && npm run build && E2E_PYTHON=<python with
backend deps> npm run e2e`. Playwright starts the real backend on a freshly
created and migrated `<db>_e2e` database and the production frontend build.
The Greenhouse/Lever adapters read **deterministic mock payloads** through
an in-process HTTP transport: the live job boards are not reachable from
every environment, and E2E must be repeatable. Nothing in the E2E run
claims anything about the live APIs.

Limitations (honest):

- Matching needs equal company keys: a company configured with different
  display names on two sources won't be matched.
- The location vocabulary is a curated list (mainly DACH + major European
  cities); unknown places match only by identical text.
- Only the primary location is compared; multi-location postings whose
  primary location differs stay separate.
- Same-source duplicates under two ids are never merged (by design).
- No manual merge/split UI; `POSSIBLE_MATCH` is reported, not reviewable yet.
- Inactivation only works for sources that report complete board listings
  (both current ATS sources do).

## 22. V0.4.1 — Live source validation

V0.4.1 adds no product feature. It validates V0.4 against the real
Greenhouse and Lever APIs and hardens the adapters.

**Result: PASS** — after the environment's network policy was changed to
allow the provider hosts, the real APIs were contacted from the same
container the tests run in and 503 real postings went through the
production pipeline. (The first attempt the same day was BLOCKED by the
egress proxy, `403` on `CONNECT`, "no rule or allowlist entry allows host";
both reports are kept in `docs/live-validation/`.)

### Providers and boards (2026-10-06)

| Provider | Host | Result |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io` | reachable; provider responses (CloudFront, `x-request-id`) |
| Lever | `api.lever.co` | reachable; provider responses |
| Lever EU | `api.eu.lever.co` | reachable; neither configured site lives there (provider `404 {"ok":false,"error":"Document not found"}`) |

| Board | Result | Postings |
|---|---|---|
| Greenhouse `getyourguide` | 200 | 54 |
| Greenhouse `hellofresh` | 200 | 411 |
| Greenhouse `grover` | 200 | 3 |
| Lever `netlight` (global) | 200 | 35 |
| Lever `ppro` (global) | 200 `[]` — the site exists but has no published postings (a non-existent site returns 404) | 0 |

Pipeline results: 503 postings fetched, 0 invalid, 0 retries, 503 canonical
jobs, 503 source postings, 0 duplicate postings, 0 orphan jobs. A repeat run
created/merged nothing (all `unchanged`), `first_seen_at` was stable and
`last_seen_at` advanced for every posting. Every board was a complete
listing; nothing was wrongly marked inactive. Inactivation on a real listing
is covered by replaying the captured response with one stored posting
removed (exactly that posting becomes inactive).

### Real responses vs. V0.4 assumptions

- **Greenhouse list responses include `company_name` and
  `first_published`** (the adapter's docstring assumed single-job
  responses only). Both were already used when present: company identity
  is the posting's own `company_name`, posted dates are 100% populated.
- Greenhouse adds fields the adapter ignores (`data_compliance`,
  `application_deadline`, `ai_disclaimer`, `language`, `requisition_id`).
  Metadata values are strings, lists, booleans or null; only strings are
  used (as designed).
- **No salary anywhere**: no `pay_input_ranges` on Greenhouse list
  responses, no `salaryRange` on Lever. Salary stays NULL (never invented).
- Greenhouse boards expose no employment-type metadata on these boards
  (fields like "Pay Rate Type [WD]", "Management Level"); employment type is
  known for 2.5% (from unambiguous titles). No remote/hybrid postings exist
  on these boards, so work model is unknown there — correctly.
- HelloFresh posting URLs live on `careers.hellofresh.com/...?gh_jid=<id>`;
  the required `gh_jid` parameter is kept (as designed). No tracking
  parameters occurred.
- Content is entity-encoded HTML (as assumed). Three HelloFresh postings
  are double-encoded *by the employer* (`&amp;amp;`), so `&amp;` remains
  in their text — exactly what the employer's own page shows. Comparison
  keys decode entities, so matching is unaffected. Not changed.
- **Lever `workplaceType` is `onsite`** (docs say `on-site`); the existing
  pattern matches both — work model 100%.
- Lever commitments: `Full time`, `Working Student (Part Time)`,
  `Internship (Full time)`, `Event`. `Event` is not an employment type and
  stays unknown (as designed).
- Lever adds `opening`, `descriptionBody` (+ plain variants); the adapter
  uses `description`, `lists` and `additional`, which carry the text.
- Lever has no company-name field (as documented); identity comes from the
  configured display name, never invented.

### Coverage and gaps (no code changed)

- Location vocabulary recognized 100% of Lever locations but only ~25% of
  Greenhouse ones (canonical `city` set for 157 of 503 jobs). Unrecognized:
  North American and Polish "City, Region, Country" forms
  ("Toronto, Ontario, Canada", "Aurora, IL, United States",
  "Warszawa, Masovian Voivodeship, Poland") and cities outside the
  vocabulary ("Verden, Niedersachsen, Germany", "Manila…"). They fall back
  to text keys — conservative: no wrong merges, but a "Toronto" posting on
  one source and "Toronto, Ontario, Canada" on another would not match.
- Title keys strip German gender markers only; real titles also carry
  `(H/F/N)` (French) and `(v/m/x)` (Dutch), which stay in the key. Same
  effect: possible missed matches, never wrong merges.
- Requirements/responsibilities extraction depends on heading words; Lever
  headings such as "The Role" or "A Day in the Life…" are not classified
  (responsibilities found for 26% of Lever postings).

These are recall limitations of the conservative design, not bugs; no real
cross-source duplicate was missed because of them. Extending the
vocabularies is a candidate for a later version, driven by real data.

### Deduplication on real data

- **No naturally occurring cross-source duplicate** exists among these
  boards (no company publishes on both providers here). None was
  manufactured; cross-source merging remains covered by the deterministic
  tests.
- Same title, different location: 14 real groups in the harness run (235
  stored postings; e.g. one Netlight title in
  Toronto and Helsinki; a HelloFresh title in Berlin, Toronto and Warsaw)
  stay separate jobs.
- **Real same-board twins**: HelloFresh lists distinct vacancies with the
  same title and location and near-identical text (similarity 0.91–0.99).
  They stay separate (same source, different ids). If another source listed
  one of them, the resolver would see two equally good candidates and **not
  merge** (ambiguity rule) — pinned by `tests/test_live_fixture_regressions.py`.
- Genuinely different vacancies stayed separate; total jobs = total postings.

### Captured fixtures and tests

Raw responses are in `backend/tests/fixtures/jobs/live/` (see its README;
one named contact person was redacted). They drive
`tests/test_live_sources.py` (full pipeline replay per board: repeat
discovery, no duplicates, inactivation) and
`tests/test_live_fixture_regressions.py` (normalization of every real
posting, same-board twins, same-title/different-location, Lever
vocabulary, the empty-but-existing Lever site).

### The harness

```bash
cd backend
python -m e2e.live_sources --capture tests/fixtures/jobs/live --report live-report.json
JOBPILOT_LIVE_SOURCES=1 pytest tests/test_live_sources.py   # opt-in live test
```

It probes the adapters' real endpoints, then runs `JobDiscoveryService`
twice through the production pipeline into a migrated scratch database and
reports reachability, response shape, field coverage, duplicates,
cross-source matches, same-title separation and repeat stability. Status:
`PASS` / `PARTIAL` / `BLOCKED` / `FAIL`; a provider counts as validated only
after real HTTP 200 JSON. It stores at most 200 postings per source per run
(the API's run limit); the 268 remaining Greenhouse postings were persisted
with pages 2–3 through the same pipeline for this validation.

### Hardening (V0.4.1, before live access)

- Malformed records, unexpected normalizer errors and database-rejected
  postings are isolated instead of failing whole runs; bounded, observable
  retries for transient failures (job-discovery.md §13a). The live run
  needed neither (0 retries, 0 invalid records).

**No application code changes were required by the real provider data.**
