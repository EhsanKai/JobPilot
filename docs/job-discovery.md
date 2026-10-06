# Job Discovery (V0.3)

V0.3 lets JobPilot find **real job postings**, normalize them into one
canonical `Job` model, store them without duplicates, and show them in the
frontend. Every job links to its original posting.

What V0.3 does **not** do (later versions): AI matching or match scores,
preference matching or gap analysis, recommendations, tailored CVs or
Anschreiben, application questions, browser auto-fill or auto-submit,
application status tracking, follow-ups, LinkedIn (or any login-protected
site) automation, CAPTCHA or anti-bot circumvention.

---

## 1. Architecture

```
                 POST /jobs/discover  (202, returns a DiscoveryRun)
                          |
                          v
   run_service.start_run()           fast: auth, one-active-run guard, validate sources
                          |
                          v  FastAPI background task (own DB session; no Redis/Celery)
   run_service.execute_run()
                          |
                          v
   JobDiscoveryService.discover(criteria)
     +-- registry.select(criteria.sources)      enabled sources only
     +-- JobSource.search(criteria)             concurrently, each source isolated
     |      GreenhouseJobSource  --+
     |      LeverJobSource       --+--> RawJob[]          (source-specific parsing ends here)
     |      <future sources>     --+
     +-- JobNormalizer.normalize(RawJob)       -> NormalizedJob, or InvalidJobError
     +-- in-run dedup (dedup_key)
     +-- criteria filters JobPilot applies     (filters.py)
     +-- newest first, page/limit
     +-- persistence.upsert_job() + link_user_job()
                          |
                          v
   DiscoveryRun.summary   (counts per source, errors, who applied each filter)

   Frontend polls GET /jobs/discover/runs/{id} until is_active == false.
```

The discovery service contains **no provider-specific logic**; it only talks
to the `JobSource` interface. Swapping or adding a source never touches the
service, persistence, or API.

### Code map

```
backend/app/services/job_discovery/
  sources/base.py         JobSource ABC, AcquisitionMethod, SourceSearchResult, SourceError family
  sources/http.py         JsonFetcher: host allowlist, no redirects, size cap, user-safe errors
  sources/ats.py          BoardJobSource: shared multi-board plumbing (concurrency, isolation)
  sources/greenhouse.py   Greenhouse Job Board API adapter
  sources/lever.py        Lever Postings API adapter
  sources/html.py         HTML -> plain text, section/heading detection (no AI)
  raw_job.py              RawJob, RawSalary
  normalizer.py           JobNormalizer, URL/date/salary/... normalization, dedup key
  vocabulary.py           deterministic keyword tables (employment type, work model, ...)
  filters.py              JobPilot-side evaluation of search criteria
  registry.py             JobSourceRegistry, build_default_registry(settings)
  discovery_service.py    orchestration
  persistence.py          upsert + UserJob linking
  run_service.py          DiscoveryRun lifecycle, criteria from preferences
  job_service.py          list / detail / save for the API
backend/app/schemas/jobs.py      JobSearchCriteria, summaries, job read models
backend/app/api/v1/jobs.py       routes
frontend/app/jobs/, frontend/components/jobs/, frontend/lib/{jobs,jobSearch}.ts, frontend/lib/api/jobs.ts
```

## 2. The `JobSource` abstraction

```python
class JobSource(ABC):
    name: str                         # stable; persisted as jobs.source
    display_name: str
    acquisition_method: AcquisitionMethod   # ATS | API | WEB
    native_filters: frozenset[SearchFilter] # filters the source itself applies

    async def search(self, criteria: JobSearchCriteria) -> SourceSearchResult: ...
```

- `SourceSearchResult` carries `jobs: list[RawJob]` plus `issues` (e.g. one
  failing company board), `targets_attempted/targets_failed`, and
  `unreadable_items`. This is the one deviation from a bare
  `list[RawJob]`: one bad board or item must not discard the rest, and
  "no jobs found" must never be confused with "source unreachable".
- A source that can't run at all raises a `SourceError` subclass
  (`SOURCE_UNAVAILABLE`, `SOURCE_TIMEOUT`, `SOURCE_RATE_LIMITED`,
  `SOURCE_HTTP_ERROR`, `SOURCE_NOT_FOUND`, `SOURCE_INVALID_RESPONSE`,
  `SOURCE_NOT_CONFIGURED`). Messages are user-safe.
- Acquisition methods are a property of the source, not of the
  architecture: an HTML/`schema.org JobPosting` source (WEB) or a
  multi-employer search API (API) implements the same interface and returns
  the same `RawJob`.

### Registry

`registry.py` registers every known source and marks which are enabled
(`JOB_SOURCES_ENABLED`). Adding a source: implement `JobSource`, add one
`registry.register(...)` call. The API exposes `GET /jobs/sources`.

## 3. Implemented sources and why

Two public, documented, unauthenticated ATS job-board APIs. Both were
verified against their official documentation (Greenhouse publishes its API
docs from `github.com/grnhse/greenhouse-api-docs`; Lever from
`github.com/lever/postings-api`). No API was invented; no scraping is
performed.

| Source | Endpoint(s) used | Auth | Why |
|---|---|---|---|
| **Greenhouse** Job Board API | `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` and `GET .../v1/boards/{token}` (company name) | None ("Job Board data is publicly available, so authentication is not required for any GET endpoints.") | Very widely used (incl. many German/EU tech companies), stable, versioned, JSON, includes full description. Primary source. |
| **Lever** Postings API | `GET https://api.lever.co/v0/postings/{site}?mode=json&skip=N&limit=100` (EU: `api.eu.lever.co`) | None for published postings ("all job postings in the `published` state are publicly viewable") | Second widely used ATS; structured workplace type, commitment and salary range. Demonstrates multiple sources and partial failure handling. |

Both are **per-company boards**: JobPilot reads the boards configured in
`GREENHOUSE_BOARD_TOKENS` / `LEVER_SITES`. Neither API offers full-text
search ("The API does not let you do full-text searches over open jobs" —
Lever), so both declare **no native filters**; JobPilot applies the criteria
after fetching (see §6).

Field notes:

- Greenhouse `id` is the job *post* id (used in the posting URL) and becomes
  `source_job_id`. `content` is entity-encoded HTML. `company_name`,
  `first_published` and `pay_input_ranges` are documented on the single-job
  endpoint; they're used when present in list responses, never assumed.
  Exposed custom fields (`metadata`) named like "Employment Type" /
  "Workplace Type" / "Salary" are used as hints.
- Lever has no company-name field; the configured display name
  (`site:Display Name`) or the site name is shown. `createdAt` is not in
  Lever's documented field table; it's used as posting date only when
  present.

Default boards (examples of real public boards, change freely):
Greenhouse `getyourguide,hellofresh,grover`; Lever `ppro:PPRO,netlight:Netlight`.

## 4. Configuration

All via environment (`backend/app/core/config.py`, `.env.example`). The
implemented sources need **no credentials**; there are no secrets to set.

| Variable | Default | Meaning |
|---|---|---|
| `JOB_SOURCES_ENABLED` | `greenhouse,lever` | Sources to enable |
| `GREENHOUSE_BOARD_TOKENS` | `getyourguide,hellofresh,grover` | Board tokens (`job-boards.greenhouse.io/{token}`); `token:Name` sets a display name |
| `LEVER_SITES` | `ppro:PPRO,netlight:Netlight` | Site names (`jobs.lever.co/{site}`), optional `:Display Name` |
| `LEVER_INSTANCE` | `global` | `global` or `eu` — a choice, not a URL |
| `JOB_SOURCE_TIMEOUT_SECONDS` | `20` | Per HTTP request |
| `JOB_SOURCE_MAX_JOBS_PER_BOARD` | `1000` | Safety cap per board per run |
| `JOB_SOURCE_MAX_RESPONSE_BYTES` | `25000000` | Response size cap |
| `JOB_DISCOVERY_STALE_MINUTES` | `10` | A run older than this no longer blocks a new one |

Board names are validated (`[A-Za-z0-9][A-Za-z0-9_.-]{0,99}`) and URL-encoded
into a path on a fixed host; invalid names are ignored with a log warning.

## 5. Search criteria

`JobSearchCriteria` (`app/schemas/jobs.py`):

| Field | Notes |
|---|---|
| `keywords[]` | whole-word match anywhere in the posting (title, company, description, lists, technologies) |
| `job_titles[]` | all words of the wanted title appear in the job title ("Backend Engineer" ⊂ "Senior Backend Software Engineer") |
| `locations[]` | whole-word match in location(s); `Remote` also matches jobs whose work model is remote |
| `work_models[]`, `employment_types[]` | same vocabularies as Job Preferences |
| `industries[]` | accepted, but **no implemented source provides industries** → reported `not_applied` |
| `salary_min`, `salary_max`, `salary_currency` | annual figures; monthly salaries are annualized; other periods/currencies aren't compared |
| `posted_after` | date; not in the future |
| `sources[]` | subset of enabled sources (`null` = all); unknown/disabled → 422 |
| `page`, `limit` | per source, over matching postings sorted newest first (`limit` ≤ 200) |

Lists are OR'ed internally; different criteria are AND'ed. Terms are
trimmed, de-duplicated and length-limited.

`GET /jobs/criteria/defaults` derives criteria from the **Job Preference
Profile** (desired titles, locations, work models, employment types,
preferred industries, minimum salary + currency). `POST /jobs/discover`
without criteria uses exactly these.

## 6. Who applies which filter — never pretended

Each discovery summary reports, per source and per requested filter,
`applied_by`:

- `source` — the source applied it natively (none of the two current
  sources can; future search APIs will),
- `jobpilot` — JobPilot applied it to the fetched postings,
- `not_applied` — nobody could; results are unfiltered for it (the UI says so).

JobPilot's own filtering (`filters.py`) is strict for text criteria. For
structured criteria (work model, employment type, salary, posted date) it
excludes a job only when the job's value is **known and contradicts** the
criterion; jobs whose posting doesn't state the value are kept and the UI
shows "Not stated". Dropping them would hide most jobs from sources that
rarely state salary or work model.

Note: list filters on `GET /jobs` are exact (a job with unknown work model
is not returned for `work_model=remote`), because there the user is
narrowing an existing list rather than collecting candidates.

## 7. Canonical model

```
Job (global, one row per posting)              UserJob (per user)            DiscoveryRun (per user)
  id                                             id                            id, user_id, status
  source, source_job_id, dedup_key (unique)      user_id, job_id (unique pair) criteria (JSONB)
  source_company_key                             first_discovered_at           summary (JSONB)
  canonical_url (required), source_url,          last_discovered_at            error_code, error_message
    apply_url                                    saved_at (NULL = not saved)   started_at, finished_at
  title, company_name, location, locations[],
    country, department, industry
  description (plain text), requirements[],
    responsibilities[], technologies[]
  salary_min/max (NUMERIC), salary_currency, salary_period
  employment_type, work_model, seniority  (shared enums with preferences/profile)
  posted_at, source_updated_at, discovered_at, last_seen_at
  raw_data (JSONB, never returned by the API), content_hash, normalizer_version
  created_at, updated_at
```

- **Global jobs.** The same posting discovered by several users is one
  `Job` row; each user has a `UserJob`. This keeps the canonical posting
  refreshable independently from user state and lets V0.4 cross-source
  dedup work on one set of rows.
- **Visibility.** A user can list and open exactly the jobs they have a
  `UserJob` for (their discovery results, or saved). Anything else is 404.
- **Future flow.** Saved → Applied → Interview / Rejected / No Response /
  Offer hangs off `UserJob` (or the existing `applications` table, which
  already references `jobs.id`). No per-user column ever goes on `Job`.
- List-like fields are JSONB arrays (the structured-JSON convention used by
  Resume Profiles); enumerations reuse the existing Postgres enum types so
  matching (V0.5) compares like with like.

`RawJob` (`raw_job.py`) is the loose, source-shaped intermediate: strings as
given, salary as `RawSalary` (amounts, currency, period text, or free text),
dates as ISO strings / epoch / datetime, plus `raw_payload`.

## 8. Normalization (deterministic, no AI)

`JobNormalizer` (version `job_normalizer_v1`):

- **Whitespace / empties**: collapse whitespace, empty → `NULL`, list
  entries cleaned and de-duplicated case-insensitively, long text truncated
  with "…" (never identifiers).
- **HTML**: descriptions become plain text (`html.py`); scripts/styles
  dropped; HTML is never stored or rendered. Requirements and
  responsibilities are taken from list items under recognized headings
  (English + German: "Requirements", "What you'll do", "Dein Profil",
  "Deine Aufgaben", ...). Unrecognized sections stay in the description.
- **URLs**: only absolute `http(s)`; no credentials, no whitespace, ≤ 2000
  chars. Canonical form lowercases scheme/host, drops default ports, the
  fragment and tracking parameters (`utm_*`, `gclid`, `fbclid`, ...). Every
  other query parameter is kept verbatim (e.g. Greenhouse's `?gh_jid=` on
  company career pages is required to reach the posting). `source_url`
  keeps the URL exactly as returned.
- **Dates**: ISO 8601, RFC 2822, epoch seconds/milliseconds → UTC.
  Implausible dates (before 1990, > 2 days in the future) → `NULL`.
- **Salary**: structured amounts first (Greenhouse cents → units), else
  simple text ("€60,000 – €75,000 per year", "60k-75k EUR",
  "EUR 60.000 - 75.000 p.a."). Currency symbols/codes → ISO; periods →
  year/month/week/day/hour. `min > max` → dropped, not guessed.
- **Employment type / work model / seniority**: keyword tables
  (`vocabulary.py`). From titles only unambiguous signals are inferred
  ("Werkstudent", "Intern"; never "contract" — "Contract Manager"). On-site
  is never inferred from the absence of "remote".
- **Technologies**: a curated vocabulary with whole-word matching;
  ambiguous words are case-sensitive ("React" vs "react quickly", "Rust"
  vs "trust"), "Java" doesn't match "JavaScript".
- **Validation** (`InvalidJobError`, counted as `invalid`): source name,
  title, company name and a valid original URL are required; a
  `source_job_id` longer than 255 chars is rejected (truncation would
  change identity).

## 9. Deduplication

`dedup_key` is unique in the database:

- `"{source}:id:{source_job_id}"` when the source has an id (both current
  sources do), else
- `"{source}:url:{sha256(canonical_url)}"`.

So the same posting from the same source is never stored twice — across
runs, across users, and under concurrent runs (`INSERT ... ON CONFLICT DO
NOTHING`). Titles are never used as keys: two different jobs with the same
title stay separate. A posting returned twice in one run (e.g. listed on two
boards) is counted under `duplicates`.

Cross-source deduplication (the same job on Greenhouse and Lever) is
**V0.4** — see [`job-intelligence.md`](job-intelligence.md). Since V0.4 the
source identity described here (`dedup_key`, source id, URLs, raw payload,
content hash) lives on `job_source_postings`; the canonical `jobs` row holds
the normalized content and a stable id.

## 10. Refresh behavior

Running discovery again:

- creates new jobs (`created`),
- updates jobs whose normalized content changed (`updated`; detected via
  `content_hash` or a newer `normalizer_version`),
- leaves unchanged jobs alone except `last_seen_at` / `updated_at`
  (`unchanged`),
- never changes `id`, `discovered_at`, or any `UserJob` / `applications`
  data (saved state survives),
- never deletes jobs that a source no longer returns (`last_seen_at` lets a
  later version mark them stale).

Only one active run per user; a run older than
`JOB_DISCOVERY_STALE_MINUTES` is treated as abandoned.

## 11. API

All under `/api/v1`, all require authentication.

```
GET    /jobs/sources                  sources, enabled flag, board count, native/JobPilot filters
GET    /jobs/criteria/defaults        criteria derived from Job Preferences
POST   /jobs/discover                 {criteria?} -> 202 DiscoveryRun (409 if one is active)
GET    /jobs/discover/runs/latest     latest run or null
GET    /jobs/discover/runs/{id}       run status + summary
GET    /jobs                          paginated: page, page_size (≤100), source, q, location,
                                      work_model (repeatable), employment_type (repeatable),
                                      posted_after, saved, sort=posted|discovered|saved
GET    /jobs/{id}                     full normalized job + canonical/source/apply URLs + your state
PUT    /jobs/{id}/save                save (idempotent)
DELETE /jobs/{id}/save                unsave
```

Discovery summary (also per source, with `status`, `errors[]`, `filters[]`):
`fetched, invalid, filtered_out, beyond_limit, duplicates, created,
updated, unchanged, discovered (= created + updated + unchanged)`.
Run status: `pending → running → completed | partial | failed`.

## 12. Frontend

- `/jobs` — one set of controls (keywords, location, work model,
  employment type, source, posted within) used both to **filter my jobs**
  and to **discover jobs**; "Discover with my preferences"; live run status
  ("Discovering jobs...", finished summary, "Some sources succeeded, some
  failed", "Unable to reach the job sources", filters not applied); job
  cards with Save / Details / View Job; Saved only; pagination; loading,
  empty and error states.
- `/jobs/{id}` — "JobPilot data" (structured facts, "Not stated" when
  unknown, technologies) separated from "Original job posting" (description,
  requirements, responsibilities, original URL, apply link); Save.
- Only `http(s)` links are rendered; external links use
  `target="_blank" rel="noopener noreferrer"`.

## 13. Security

- Authentication on every endpoint; cross-user access → 404.
- No credentials exist for the implemented sources; `/jobs/sources` exposes
  names and counts only; `raw_data` is never returned.
- **No SSRF surface**: the fetcher only requests `https` URLs on a fixed
  per-source host allowlist defined in code, never follows redirects, caps
  response size, and configuration can only supply validated board names.
  Users can't supply URLs to fetch.
- Source HTML is converted to text; React escapes all rendered text; links
  are restricted to `http(s)`.
- Errors shown to users are fixed, user-safe messages; logs contain ids,
  board names, status codes and exception *types* only.
- Tests replace real HTTP transports with a guard that raises, so no test
  can reach a real job board -- except tests explicitly marked
  `live_network`, which only run with `JOBPILOT_LIVE_SOURCES=1` (V0.4.1).

## 13a. Failure handling (V0.4.1)

- **Bounded retries** (`sources/http.py`): connection failures, timeouts,
  HTTP 429 and 500/502/503/504 are retried at most `JOB_SOURCE_MAX_RETRIES`
  times (default 2) with exponential backoff
  (`JOB_SOURCE_RETRY_BACKOFF_SECONDS` × 2ⁿ, default 0.5 s). `Retry-After`
  (seconds) is honored up to 10 s; a longer request is not waited for and
  the board is reported as rate-limited. 404/other 4xx and unreadable or
  oversized responses are never retried. Each retry is logged (host,
  board, attempt, reason) and counted in the run summary
  (`retried_requests`). A request that still fails is reported as failed --
  persistent provider failures are never hidden.
- **One bad record never sinks a run**:
  - adapters convert a board's records one by one; a record whose shape
    the adapter can't handle is counted as unreadable (`invalid`), the
    rest of the board is kept;
  - an unexpected error normalizing one record skips that record
    (`invalid`);
  - each posting is stored in its own savepoint: one the database rejects
    (data/integrity error) is rolled back completely -- no partial
    canonical job -- and reported as `POSTING_REJECTED` (`invalid`,
    `error_count`). Unexpected programming errors still fail the run with
    a generic message.
  - A board with unreadable or invalid records is never used to mark
    postings inactive.
- Counts stay consistent: `fetched = invalid + duplicates + filtered_out +
  beyond_limit + created + merged + updated + unchanged` per source.

## 14. Testing

- `tests/test_source_hardening.py` (V0.4.1) — retries (transient
  failures recover, retries are bounded, 4xx/unreadable never retried,
  backoff, Retry-After cap, Lever pages retried individually) and error
  isolation (malformed Greenhouse/Lever records, normalizer errors,
  database-rejected postings, no inactivation from boards with bad records).
- `tests/test_live_sources.py` (V0.4.1) — the live validation harness, opt-in
  live tests, and regression tests over captured real responses (see
  `docs/job-intelligence.md` §22).
- `tests/test_job_normalizer.py`, `test_job_filters.py`,
  `test_job_sources.py`, `test_jobs_api.py` — adapters run over
  `httpx.MockTransport` with fixtures in `tests/fixtures/jobs/`
  (Greenhouse documented response shape, Lever documented fields), covering
  parsing, normalization, validation, errors (404/429/5xx/timeout/
  connection/malformed/oversized/redirect), partial failure, pagination,
  persistence, dedup, refresh, ownership and the API.
- Frontend: `JobsDashboard.test.tsx`, `JobDetails.test.tsx`, `jobs.test.ts`.

## 15. Limitations

- Sources are per-company boards; JobPilot only finds jobs at configured
  companies. A multi-employer search source is a natural next addition.
- Neither source supports server-side search, so every run reads whole
  boards and filters locally (bounded by the per-board cap).
- Greenhouse list responses usually lack salary and work model; Lever
  rarely states salary. Those show as "Not stated".
- No industries in either source → industry filter is `not_applied`.
- No per-user cooldown or response caching: each run hits every configured
  board once. Fine for V0.3 volumes; V0.4 should add caching/scheduled refresh.
- Discovery runs in-process (FastAPI background task). A server restart
  mid-run leaves the run to be marked abandoned after
  `JOB_DISCOVERY_STALE_MINUTES`.
- Requirements/responsibilities extraction relies on recognizable headings.
- Automated tests never call the real APIs by default. Live validation is
  an explicit step: `python -m e2e.live_sources` (or
  `JOBPILOT_LIVE_SOURCES=1 pytest tests/test_live_sources.py`) from an
  environment that can reach `boards-api.greenhouse.io` and `api.lever.co`
  (see `docs/job-intelligence.md` §22: V0.4.1 live validation passed on
  2026-10-06 against 5 real boards; captured responses drive regression
  tests).

## 16. Future sources

Any of these is one `JobSource` subclass plus a registry entry:
Workday / SmartRecruiters / Personio / Recruitee (ATS), the German Federal
Employment Agency job-search API (API; would declare native keyword/location
filters), schema.org `JobPosting` pages (WEB). LinkedIn, Indeed and
StepStone require partnerships or prohibit automated access; they won't be
added via scraping or login automation.
