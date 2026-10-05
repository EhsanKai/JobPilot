# JobPilot Architecture — V0 to V0.3

This document captures the architectural decisions behind the V0 foundation,
the V0.1 structured Job Search Profile, V0.2 Resume Intelligence and V0.3
Job Discovery, and how later versions are expected to build on them without
a rewrite. V0.3 is summarized in §13 and documented in full in
[`job-discovery.md`](job-discovery.md). V0.2 is
summarized in §12 and documented in full in
[`resume-intelligence.md`](resume-intelligence.md).

## 1. Layered, modular pipeline

The long-term product is a pipeline:

```
Job Sources → Connectors → Normalized Job Model → Matching Engine → Application Engine
```

V0 implemented the data model at the "Normalized Job Model" stage (the
`jobs` and `applications` tables). V0.3 implements the first two stages:
real `JobSource` connectors and the normalized, deduplicated `Job` model
(§13). No matching engine or application engine logic exists yet — they are
later versions. The point of building the
interface now is that `app/api`, `app/services`, and the database schema
never need to know which job source or AI provider is behind them.

## 2. Backend layering

```
app/api/v1/*      → FastAPI routers. Thin: parse request, call a service, return a schema.
app/services/*    → Business logic. Owns transactions, validation, orchestration.
app/models/*      → SQLAlchemy ORM models (the persistence layer).
app/schemas/*     → Pydantic request/response models. Never expose ORM models directly.
app/core/*        → Cross-cutting concerns: config, security, logging, error envelope.
```

Routers depend on services; services depend on models. This keeps business
rules (e.g. "replacing a resume deletes the old file") out of the HTTP layer
and out of the ORM layer, so they're testable without spinning up FastAPI.

## 3. Provider-agnostic job sources

V0 sketched a `JobSource` placeholder in `app/services/integrations/`.
V0.3 replaced it with the real abstraction in
`app/services/job_discovery/sources/base.py` (the placeholder was removed so
the concept exists once). Implementations: `GreenhouseJobSource` and
`LeverJobSource`, registered in a `JobSourceRegistry`. The discovery
service, persistence and API depend only on `JobSource`, never on a
specific provider. Sources declare their acquisition method (ATS job board,
search API, web page) and the filters they apply natively; all of them
produce the same `RawJob`. See [`job-discovery.md`](job-discovery.md).

## 4. AI-provider-agnostic design

V0 defined placeholder interfaces (`AIProvider`, `ResumeParser`,
`JobMatcher`, `CoverLetterGenerator`) in
`app/services/integrations/ai_provider.py`. V0.2 makes the first two real:

- `app/ai/provider.py`: `AIProvider.generate_structured(system, prompt,
  schema)` returns a Pydantic-validated instance of `schema` or raises an
  `AIError` subclass with a user-safe `code` and message.
- `app/ai/providers/anthropic.py`: the only module that imports a vendor
  SDK. `app/ai/factory.py` picks the provider from `AI_PROVIDER` and
  degrades to a `DisabledAIProvider` when unconfigured.
- `app/services/resume_intelligence/parser.py`: `ResumeParser` /
  `AIResumeParser`, which depend only on `AIProvider`.

`JobMatcher` and `CoverLetterGenerator` remain placeholders. Adding OpenAI,
Google or a local LLM means one new `AIProvider` class.

## 5. Storage abstraction

Resume files are never written directly to `backend/storage/...` from
application code. Everything goes through `app/services/storage/base.py`'s
`FileStorage` interface (`save`, `read`, `delete`, `exists`). `read` was
added in V0.2 so the original file can be re-analyzed. Nothing ever
modifies a stored file. V0 ships
`LocalFileStorage`, suitable for local development only. Switching to S3,
GCS, Azure Blob, or Cloudflare R2 in a later version means writing one new
class that implements `FileStorage` — no changes to `resume_service.py` or
the API layer.

## 6. Database schema (V0)

The schema is normalized rather than storing lists as delimited strings:

- `users` — account + credentials.
- `user_profiles` — one-to-one with `users`; personal contact info only
  (phone, location, LinkedIn URL) as of V0.1 — see §10 for why career fields
  moved out of this table.
- `job_preferences` — one-to-one with `users` for scalar preferences.
  List-like preferences live in their own child tables
  (`job_preference_job_titles`, `job_preference_locations`, etc.) rather
  than as comma-separated strings, so they can be queried and validated
  properly. Extended significantly in V0.1 — see §10.
- `resumes` — upload/storage metadata (`storage_reference`, MIME type,
  size, status). Since V0.2 it is also versioned (`version_number`) and
  hashed (`file_hash`), and `status` tracks Resume Intelligence processing.
  Parsed content lives in `resume_profiles` (see §12).
- `jobs` — the canonical job posting. A foundation table in V0; since V0.3
  populated by Job Discovery and global (one row per posting), with
  per-user state in `user_jobs` (§13).
- `applications` — a foundation table for the future application workflow
  (status, salary expectation, start date). No document generation or
  submission logic exists yet.

Migrations are managed with Alembic (`backend/alembic/`). The initial
migration creates the V0 schema; a second migration adds the V0.1 entities
described in §10.

## 7. Authentication

Email/password registration and login, bcrypt-hashed passwords (via the
`bcrypt` library directly — not `passlib`, which has known compatibility
issues with modern `bcrypt` releases), and stateless JWT access tokens
(`python-jose`). `app/api/deps.py` provides a `get_current_user` dependency
used to protect routes. No OAuth provider is implemented in V0; the
dependency-injection shape makes adding one later (e.g. LinkedIn or Google
OAuth) additive rather than a redesign.

## 8. Error handling

All API errors — expected (`AppError` subclasses like `NotFoundError`,
`UnauthorizedError`, `ConflictError`, `ValidationAppError`) and unexpected
(uncaught exceptions) — are normalized by `app/core/errors.py` into a single
envelope:

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "...", "details": {} } }
```

Unhandled exceptions are logged server-side with `logger.exception` and
never leak a stack trace to the client.

## 9. Frontend architecture

- `app/` — Next.js App Router pages. Pages are thin: they compose
  components and wrap authenticated routes in `AppShell`.
- `components/` — grouped by domain (`auth/`, `profile/`, `resume/`,
  `dashboard/`, `jobs/`) plus a small `ui/` kit (`Button`, `Input`, `Card`, `Alert`)
  so loading/error/success states look consistent everywhere. `profile/`
  holds one component per Job Search Profile section (`ProfileForm`,
  `SkillsManager`, `LanguagesManager`, `DesiredRolesManager`,
  `LocationsManager`, `PreferencesForm`, `ProfileCompleteness`) rather than
  one large form, per §10.
- `lib/http.ts` — a single typed `fetch` wrapper (`api.get/post/put/delete`)
  that attaches the JWT, parses the backend's error envelope into an
  `ApiError`, and is the only place that knows about `NEXT_PUBLIC_API_URL`.
- `lib/api/*` — one typed module per backend domain (`auth.ts`,
  `profile.ts`, `preferences.ts`, `resumes.ts`, `users.ts`, `skills.ts`,
  `languages.ts`, `completeness.ts`, `jobs.ts`), so components never construct fetch
  calls or URLs themselves.
- `types/*` — TypeScript interfaces mirroring the backend's Pydantic
  schemas, kept in sync by hand in V0 (a generated client is a reasonable
  future improvement, not required for the foundation).

Every form/upload component manages explicit loading, success, error, and
empty states rather than leaving the user guessing whether an action worked.

## 10. V0.1 — User Profile & Job Preference Intelligence

V0.1's goal is a structured **Job Search Profile**: enough normalized data
for a future Matching Engine to query directly, without implementing any
matching, parsing, or AI itself. It builds on V0's `user_profiles` and
`job_preferences` rather than replacing them.

### 10.1 The five-way split

```
User
├── Personal Profile       (user_profiles)       phone, location, LinkedIn
├── Professional Profile   (professional_profiles) job title, experience, seniority, industries worked in
├── Skills                 (skills + user_skills)  catalog + per-user proficiency
├── Languages               (languages + user_languages) catalog + per-user proficiency
└── Job Preferences        (job_preferences + children)
    ├── Desired Roles       job_preference_job_titles (+ priority)
    ├── Locations            job_preference_locations (+ radius_km placeholder)
    ├── Employment Types     job_preference_employment_types
    ├── Preferred Industries job_preference_industries
    ├── Salary               job_preferences.minimum_salary / desired_salary / salary_currency
    ├── Work Model           job_preference_work_models (multi-select)
    ├── Relocation            job_preferences.relocation (yes/no/maybe)
    ├── Travel                job_preferences.travel_willingness
    └── Deal Breakers         deal_breakers
```

Two reclassifications from V0, each for a concrete reason rather than a
cosmetic rename:

- **Seniority and career fields moved out of `user_profiles` into a new
  `professional_profiles` table.** V0.1's own architecture puts "Seniority
  level" under Professional Profile, not Personal Profile or Job
  Preferences — keeping personal contact info and professional background
  as separate one-to-one tables lets them be edited, and eventually sourced
  (e.g. from a parsed resume in V0.2), independently.
- **Spoken languages moved out of `job_preferences` into top-level
  `languages` / `user_languages` catalog tables.** A language the user
  speaks (e.g. "German — native") is a fact about the person, not a
  preference about a future job, and V0.1's own profile diagram lists
  Languages as a sibling of Job Preferences rather than nested under it.

Two new concepts reuse the existing child-table pattern rather than
inventing a new one:

- **Work model became multi-select.** V0's `job_preferences.remote_preference`
  was a single enum column. V0.1 explicitly requires combinations like
  "Hybrid + Remote", so it's now `job_preference_work_models`, structured
  exactly like `job_preference_employment_types` (one row per selected
  value) instead of a single column.
- **Relocation became a three-state enum.** `relocation_willingness: bool`
  couldn't represent "maybe" — a real, common answer — so it's now
  `job_preferences.relocation: yes | no | maybe`.

### 10.2 Skills, languages, and industries: catalog + link tables

Skills (`skills` + `user_skills`) and spoken languages (`languages` +
`user_languages`) both follow the same shape: a shared catalog table (just
a `name`) plus a per-user link table carrying the user-specific attributes
(proficiency, years of experience). Industries follow it too, but **twice**,
for two different concepts that are easy to conflate:

- `industries` + `user_industries` — industries the user has **worked in**
  (a Professional Profile fact, queried directly by `user_id`).
- `job_preference_industries` — industries the user **wants to work in**
  (a Job Preference, kept as the free-text child table it already was in
  V0, just renamed to `preferred_industries` in the API for clarity).

The catalog pattern means "Python" or "German" is stored once and reused
across users, while each user's own proficiency/experience lives in the
link row. Adding the same skill or language twice updates the existing row
instead of duplicating it (skills); adding a language the user already has
is rejected with `409 Conflict` rather than silently duplicating it.

### 10.3 Salary model

`minimum_salary` and `desired_salary` are `NUMERIC(12,2)` — structured
annual gross salary, never a formatted string like `"€60k-€68k"`. A
`model_validator` on `JobPreferencesUpdate` rejects `desired_salary <
minimum_salary` with a specific, user-facing message (surfaced by the
frontend's error handling, not a generic "invalid input"). The same
`salary_currency` / `minimum_salary` / `desired_salary` shape is intended to
generalize to a future `jobs.salary_min` / `salary_max` / `salary_currency`
/ `salary_period` once job postings exist (V0.3+), so the Matching Engine
can compare like with like.

### 10.4 Deal breakers

`deal_breakers` is a flexible table (`user_id`, `type`, `value`, `enabled`)
rather than dedicated boolean columns, because the Matching Engine (V0.5+)
will need to grow new deal-breaker types without another migration. `type`
is a closed enum (`minimum_salary`, `remote_requirement`, `employment_type`,
`location`, `relocation`, `travel`) with a `UNIQUE (user_id, type)`
constraint — one row per deal-breaker kind per user, matching the fixed
checklist UI in `PreferencesForm`. `value` is an optional free-form payload
for deal-breaker types that need one in the future (e.g. a specific
employment type to exclude); V0.1 doesn't populate it. No matching logic
reads this table yet.

### 10.5 Profile completeness

`app/services/completeness_service.py` computes a 0-100% score from eight
equally-weighted categories (12.5% each), so a change to the weighting is a
one-line, deliberate edit rather than an accident of field count:

| Category                 | Full credit when...                                   |
| ------------------------- | ------------------------------------------------------ |
| Personal information      | fraction of {phone, location, LinkedIn} filled         |
| Professional information   | fraction of {job title, years of experience, seniority} filled |
| Skills                     | at least one skill added                               |
| Languages                  | at least one language added                            |
| Job titles                 | at least one desired job title added                   |
| Locations                  | at least one preferred location added                   |
| Salary                      | both minimum and desired salary set (half credit for one) |
| Work model                 | at least one work model selected                        |

This is the single source of truth: both the dashboard and the profile page
render the same `GET /profile/completeness` response via one
`<ProfileCompleteness />` component, rather than each computing their own
approximation.

### 10.6 Full-replace endpoints and the pass-through problem

`PUT /profile` and `PUT /preferences` replace every field they cover in a
single transaction — the same convention V0 already used for preference
lists. V0.1 adds dedicated add/delete endpoints for desired job titles and
preferred locations (`/preferences/job-titles`, `/preferences/locations`),
which creates a real hazard: if the frontend's bulk preferences form (salary,
work model, employment type, deal breakers) submitted without those two
lists, `PUT /preferences` would silently wipe them out, since the schema
defaults omitted list fields to empty.

The fix lives entirely in the frontend: `app/profile/preferences/page.tsx`
holds one `preferences` object fetched once and shared by all three
sections. `DesiredRolesManager` and `LocationsManager` mutate it via their
own granular endpoints and lift the result back up through an `onChange`
callback; `PreferencesForm` always echoes the current `desired_job_titles`
and `preferred_locations` back unchanged in its own `PUT` payload. A test
in `PreferencesForm.test.tsx` specifically asserts this pass-through so a
future refactor can't reintroduce the bug silently.

## 11. Why no job matching or scraping yet

Matching requires significant business logic and product decisions (what
scoring model, what automation risk is acceptable) that are out of scope so
far. V0.2 introduces AI only for resume extraction (§12). V0.3 reads public,
documented job-board APIs; it does not scrape websites (§13). Building the interfaces
now (`JobSource`, `AIProvider`, `ResumeParser`, `JobMatcher`,
`CoverLetterGenerator`) without the implementations means V0.3–V0.9 can be
built additively on top of this schema and these service boundaries,
instead of requiring a redesign of the data model or API surface.

## 12. V0.2 — Resume Intelligence

Full documentation: [`resume-intelligence.md`](resume-intelligence.md).

### 12.1 Pipeline

```
Resume file ─► extraction.py ─► parser.py ─► validation.py ─► processing_service.py ─► review_service.py
 (unchanged)   PDF/DOCX → text   AIProvider     truth guard      persistence, statuses     accept/edit/reject
               OCR_REQUIRED      ParsedResume   evidence check   reuse, reprocess          → confirm
```

`POST /resumes/{id}/process` does the cheap synchronous checks (ownership,
concurrency guard, file-hash reuse) and queues the pipeline as a FastAPI
background task with its own DB session. The frontend polls
`GET /resumes/{id}/profile`. No queue infrastructure (Redis/Celery) was
introduced. The background task is a single function, so moving it to a
worker later only changes how it is scheduled.

### 12.2 Data model

- `resume_profiles` holds one row per processing attempt. It records
  provenance (`parser_version`, `source_file_hash`, `ai_provider`,
  `ai_model`), the extracted text, two JSONB documents (`raw_data`, the
  validated parser output that is never edited, and `reviewed_data`, which
  carries the user's per-item decisions), truth-guard `warnings`, and
  user-safe `error_code`/`error_message` for failures. Statuses:
  `processing | needs_review | confirmed | superseded | failed | ocr_required`.
- `resumes.status` uses the new `resume_processing_status` enum
  (`uploaded → extracting → extracted → parsing → parsed → needs_review →
  confirmed`, or `failed` / `ocr_required`). The migration converts the V0
  `resume_status` type with an explicit `USING` mapping and drops it, rather
  than reusing its name with different values (see the V0/V0.1 enum notes in
  §6 and §10).
- Uploads create new versions instead of deleting old ones, so the model
  supports "Resume 1 — Original, Resume 2 — Updated, …" and future tailored
  versions.

JSONB was chosen over one table per entity because a Resume Profile is a
versioned, document-shaped snapshot of one parser run, with nested evidence.
Pydantic validates it on every read and write, and its schema will change
with parser versions. If matching later needs relational queries, the
confirmed profile can be projected into tables then.

### 12.3 Two profiles, never merged

```
User Profile (V0.1)          what the user tells JobPilot      user_profiles, professional_profiles, skills, ...
Resume Profile (V0.2)        what the resume says              resume_profiles (confirmed)
```

Resume analysis never writes to V0.1 tables. `conflicts.py` compares the
two on read (name, email, phone, location, LinkedIn, current title, years of
experience) and reports both values with `potential_conflict: true`.

### 12.4 Trust boundary

AI output is untrusted until two things have happened. First, the
deterministic truth guard has checked it against the resume text. Second,
the user has reviewed every item and confirmed. Only a `confirmed` profile
counts as the trusted Resume Profile for future features.

## 13. V0.3 — Job Discovery

Full documentation: [`job-discovery.md`](job-discovery.md).

### 13.1 Pipeline

```
JobSearchCriteria ─► JobDiscoveryService ─► JobSource.search() ─► RawJob ─► JobNormalizer ─► filters ─► upsert Job + UserJob
 (or from Job        (no provider logic)    Greenhouse, Lever      loose     deterministic    criteria   dedup_key unique
  Preferences)                              over JsonFetcher                 no AI             JobPilot
```

`POST /jobs/discover` creates a `DiscoveryRun` and returns 202; the run
executes as a FastAPI background task with its own DB session (the same
pattern as resume processing; no Redis/Celery). Sources run concurrently and
are isolated: one failing board or source makes the run `partial`, not
failed. The frontend polls the run.

### 13.2 Data model decisions

- **Global `jobs`, per-user `user_jobs`.** A posting is stored once however
  many users discover it. `user_jobs` records discovery and the saved flag
  and is the visibility boundary (no row → 404). Future application status
  attaches to `user_jobs` / `applications`, never to `jobs`.
- **The V0 `jobs` table was evolved in place** (`external_id → source_job_id`,
  `company → company_name`, `url → source_url`, plus the new columns), so
  `applications.job_id` keeps working. `canonical_url` is required.
- **Deduplication key** `dedup_key` is unique: source + source job id, or
  source + hash of the canonical URL. Never the title. Cross-source
  deduplication is V0.4.
- **Shared vocabularies.** `jobs.employment_type`, `work_model` and
  `seniority` reuse the `employment_type`, `remote_preference` and
  `professional_seniority_level` enum types, so V0.5 matching compares the
  same values the user picked in preferences. The V0.3 migration also adds
  `WORKING_STUDENT` to `employment_type`. The ORM had it since V0.1, but no
  earlier migration created it.
- **JSONB lists** for requirements, responsibilities, technologies and
  locations, the structured-JSON convention of Resume Profiles.
- **`discovery_runs`** keep criteria and per-source summaries for status
  polling, debugging and later analytics.

### 13.3 Honesty about filters

Each source declares `native_filters`. Everything else JobPilot either
applies to the fetched postings or reports as `not_applied`; the summary
says which, per source. A source never claims a filter it didn't apply.

