# JobPilot

JobPilot is an AI-powered job application platform. It will eventually help
users upload and understand their resume, define job preferences, search
jobs across multiple sources, calculate an explainable match score, generate
tailored application documents, and track applications end to end.

This repository currently implements **V0 — Foundation**,
**V0.1 — User Profile & Job Preference Intelligence**,
**V0.2 — Resume Intelligence**, **V0.3 — Job Discovery Foundation**,
**V0.4 — Job Intelligence & Cross-Source Normalization**, the V0.5 matching
foundations (data model and taxonomy), **V0.6 — Job Matching &
Explainability**, **V0.7 — Application Workspace**, **V0.8 — Tailored CV &
Anschreiben Generation**, **V0.9 — Application Question AI + Human Control at
Every Stage**, **V0.10 — Browser Automation & Human-Controlled Application
Filling** and **V0.11 — Review & Submit**.

## 1. What JobPilot is

JobPilot aims to automate the tedious parts of job hunting: tracking
preferences, tailoring documents, and filling out applications — while
keeping the user in control of what gets submitted and where.

## 2. Current scope (V0 – V0.11)

V0 is a clean, production-oriented foundation. V0.1 turns the basic profile
from V0 into a structured Job Search Profile with enough detail for a future
Matching Engine to query, without implementing any matching itself. Both
intentionally do **not** implement job scraping, AI matching, AI document
generation, or application automation — those are future versions (see the
[Roadmap](#12-roadmap)).

V0 includes:

- A working Next.js frontend and FastAPI backend that can talk to each other.
- PostgreSQL with Alembic-managed migrations.
- Email/password authentication with hashed passwords and JWT sessions.
- Resume upload/storage with a swappable storage backend (local in V0).
- Lightweight architectural interfaces for future job-source connectors and
  AI providers, so later versions can be built without a rewrite.

V0.1 adds, on top of that foundation:

- A **personal profile** (phone, location, LinkedIn) and a separate
  **professional profile** (current job title, years of experience,
  seniority level, industries worked in).
- **Skills** and **spoken languages**, each with their own catalog table and
  per-user proficiency, addable/editable/removable from the UI.
- A richer **job preferences** model: desired job titles with priority,
  preferred locations, salary range (structured numeric, not formatted
  strings), employment types, work model (multi-select — e.g. "Hybrid +
  Remote"), preferred industries, relocation (yes/no/maybe), and travel
  willingness.
- **Deal breakers** — structured, user-defined conditions (e.g. "salary below
  minimum", "fully on-site") for a future Matching Engine to use. No matching
  logic reads them yet.
- A dynamically-calculated **profile completeness** score (not hard-coded)
  shown on the dashboard and profile page.

See [`docs/architecture.md`](docs/architecture.md) for the full data model
and the reasoning behind how V0.1 splits these concerns.

V0.2 adds **Resume Intelligence**: turning an uploaded resume into a
structured, evidence-backed Resume Profile that the user reviews and confirms.

- **Text extraction** from PDF (`pypdf`) and DOCX (`python-docx`). Scanned PDFs
  are detected and reported as `OCR_REQUIRED` rather than producing an empty
  profile. OCR itself is a future feature.
- A **provider-agnostic AI layer** (`AIProvider`), with a real Anthropic
  implementation using structured outputs. Credentials come only from
  environment variables, and analysis fails gracefully when no provider is
  configured.
- A **versioned resume parser** (`resume_parser_v1`) with a prompt stored as a
  versioned resource. Its output is validated against a Pydantic schema
  before anything is stored.
- A deterministic **truth guard** that checks every evidence quote against
  the resume text. It drops personal details and summaries that aren't in the
  resume, removes unmentioned technologies and dates, and resets unsupported
  skill or language levels to unknown.
- **Confidence** (extraction certainty) and **evidence** ("where did this come
  from?", with an in-context source view) on every extracted item.
- **User review**: accept, edit or reject each item, then confirm. Only a
  confirmed profile is trusted, and the raw AI output is kept separately.
- **Conflict detection** between the resume and the user's own profile, for
  example years of experience or current title. Both values are shown and
  neither is overwritten.
- **Resume versions** (new uploads no longer delete old ones), file hashing,
  and reprocessing. The same file analyzed by the same parser version is never
  re-analyzed automatically.
- Dashboard and Profile integration ("Your Profile" vs. "Resume Intelligence").

V0.2 deliberately does **not** search for jobs, compute match scores, or
generate documents. See [`docs/resume-intelligence.md`](docs/resume-intelligence.md).

V0.3 adds **Job Discovery**: finding real job postings and storing them in
one canonical model. See [`docs/job-discovery.md`](docs/job-discovery.md).

- A provider-agnostic **`JobSource`** interface and a source registry. The
  discovery service never depends on a specific provider.
- Two real, public, documented sources that need no credentials: the
  **Greenhouse Job Board API** and the **Lever Postings API**, reading the
  company boards configured in `.env`.
- `RawJob` → deterministic **normalizer** (no AI) → canonical **`Job`**:
  whitespace, URLs (canonical + original), dates, salary, employment type,
  work model, seniority, technologies, requirements/responsibilities, and
  required-field validation.
- **Deduplication** by source + source job id (canonical-URL fallback), safe
  under concurrent runs. Refreshing updates changed jobs and never deletes
  or duplicates.
- **Global jobs + per-user `UserJob`** (discovered / saved), ready for future
  application tracking. Search criteria can come from the Job Preference
  Profile.
- Asynchronous **discovery runs** with per-source results, partial-failure
  handling and an honest report of which filters each source could apply.
- A **Jobs** page (search/discover controls, job cards, saved jobs,
  pagination, loading/empty/error states) and a **job details** page that
  separates JobPilot's structured data from the original posting and links
  to it.

V0.3 deliberately does **not** provide AI matching, match scores,
auto-apply, browser automation, LinkedIn automation, or application
tracking.

V0.4 adds **Job Intelligence**: recognizing when postings from different
sources are the same real-world job. See
[`docs/job-intelligence.md`](docs/job-intelligence.md).

- **Source Posting vs Canonical Job**: every posting keeps its own row
  (`job_source_postings`: source, source id, original URL, raw and
  normalized data, first/last seen, active state); the canonical `Job` has a
  stable id that `user_jobs` and `applications` point at.
- Deterministic **normalization** for comparison: URLs (http/https, trailing
  slash, fragments, tracking parameters), titles (case, Unicode, hyphens,
  gender markers), companies (legal suffixes), locations (Köln = Cologne,
  never Bonn; remote/hybrid), employment type, work model, salary (with the
  original text kept), and a **content hash** of meaningful content.
- Conservative **cross-source deduplication**: `EXACT` (same URL) and
  `HIGH_CONFIDENCE` (same company + title + location and ≥ 0.80 description
  similarity, no conflicting facts) merge; `POSSIBLE_MATCH` is reported but
  never merged. Database constraints plus advisory locks keep concurrent
  runs from creating duplicates.
- A **preferred posting** per job (for future application automation),
  freshness (`active` / `stale` / `inactive`, nothing deleted), richer run
  statistics, and a migration that carries all V0.3 data over.
- UI: one card per job with `Sources: Greenhouse + Lever`; job details list
  every source with its own "View original posting" link.

V0.4 deliberately contains **no** AI matching, match scores or LLM calls —
that is V0.5.

V0.5.0 adds the **data foundation** for matching — and nothing user-facing
yet: immutable candidate profile snapshots, versioned job fact sheets with
their requirements, and reproducible match-result records (four new tables,
one create-only migration). Snapshot building, requirement extraction,
scoring, matching APIs and UI are later V0.5 phases. See
[`docs/candidate-job-intelligence.md`](docs/candidate-job-intelligence.md).

V0.5.1 adds the **taxonomy & deterministic normalization foundation**. It
contains versioned, domain-neutral vocabularies for skills, languages
(CEFR levels), education levels, job titles and locations. Each has a
conservative normalizer that returns MATCH, UNKNOWN or AMBIGUOUS, never a
guess. The seed comes from the 503 real postings. There is still nothing
user-facing, and no extraction, matching or AI. See
[`docs/taxonomy.md`](docs/taxonomy.md).

V0.6 adds **Job Matching & Explainability**. For each of your jobs,
JobPilot calculates — deterministically, never by AI — a **Job Match** (can
you do the job: skills, experience, role, seniority, industry, languages,
education), a **Preference Match** (does it fit what you want: location,
work model, salary, employment type, desired roles, travel) and an
**Overall Match** (70/30), plus a recommendation, strong matches, gaps,
unknowns and deal breakers, all with evidence from your profile and the
posting. Unknown information is never counted against you; only deal
breakers you configured can block a job. An optional AI explanation puts
the result in plain words and is validated against it. Results are
per-user, versioned, and marked as outdated when your profile, the job or
the model changes. See [`docs/job-matching.md`](docs/job-matching.md).

V0.7 adds the **Application Workspace & Application Preparation Foundation**.
A job match is not an application: only when you explicitly choose **Start
Application** on a job does JobPilot create your private workspace for it —
exactly one per job, owned by you, and nothing is submitted or sent to anyone.
The workspace (`/applications/[id]`) shows the job as it was when you started
(with its original posting link), and stores what is specific to *this*
application: salary expectation, whether to mention it in the cover letter,
availability, notice period, work authorization, application language, the
resume version it builds on, and private notes — separate from your profile and
preferences, which it never changes. The job and source posting are captured in
a write-once snapshot, so later versions can reproduce what you applied to even
after the posting changes. V0.7 contains **no** CV or cover-letter generation,
AI, browser automation, auto-fill or submission — those are V0.9 and later. See
[`docs/application-workspace.md`](docs/application-workspace.md).

V0.8 adds **Tailored CV & Anschreiben Generation**. From an Application's
**Documents** section you can generate a tailored CV and a cover letter
(Anschreiben) for *this* job, built from your **confirmed** resume, the job as it
was when you started, and the application's own data — in the application's
language (German or English), never guessed from the job. **Truth comes
first**: the AI never writes plain facts (employers, titles, dates, degrees and
skill names are copied from your resume), every sentence it writes cites the
resume facts it rests on, and a deterministic validator rejects invented
employers, tools, numbers, credentials, titles and pay talk — a rejected or
failed attempt is never saved as a document. Your salary expectation appears in
a letter only if you chose Yes, in a fixed sentence. Every generation is a new,
numbered version with its provenance (resume version, prompt version, provider
and model); regenerating never overwrites an earlier version, and the CV and
letter are generated independently. Without an AI provider generation fails
cleanly rather than inventing anything. See
[`docs/tailored-documents.md`](docs/tailored-documents.md). Not in V0.8:
editing or exporting generated documents, application question answering,
browser automation, auto-fill or submission.

V0.9 adds **Application Question AI** and makes **Human Control at Every Stage** a
core principle: *the user always owns the application and can take over manually at
any stage; AI output is a suggestion, user-authored content is authoritative.*

- **Application questions.** Add the questions an employer asks to an Application
  (entered by hand in V0.9; the model is source-neutral so V0.10 can add
  browser-discovered ones). Each question can be answered entirely by hand.
- **Suggestions, never inventions.** Structured questions (salary expectation, start
  date, notice period, work authorization, contact details, application language)
  are answered **from the application's own data without calling the AI**; the rest
  gets an AI suggestion grounded in your *confirmed* resume and validated by the
  same truth checks as V0.8 (invented numbers, tools, employers, options, dates and
  "No because nothing was found" are rejected, never repaired). When the facts don't
  support an answer JobPilot says **"Needs your input"** — it never guesses.
- **Your answer vs. the suggestion.** They are stored and shown separately. Accept,
  edit, reject, answer manually, clear, or copy your final answer. Regenerating
  creates a new suggestion and **never** overwrites your answer; character limits are
  enforced and your text is never truncated. Every attempt keeps its provenance
  (prompt version and hash, provider, resume version, input hash, cited facts).
- **✨ Humanize** rewords your Anschreiben to sound more natural, as a **new
  version**, going through the same truth validation (it can't add facts, drop
  facts, or change the company, role or salary); the original stays.
- **Edit, copy, keep.** Edit the CV or letter text (saved as a new version marked
  *edited by you*; regeneration never replaces your edit without asking), copy or
  download as text, and add, replace or remove your own **CV photo** — a user-owned
  asset that no AI operation touches.

See [`docs/application-questions.md`](docs/application-questions.md). Not in V0.9:
browser automation, auto-fill or submission (V0.10 and later), PDF / DOCX export, a
rich editor, image editing.

V0.10 adds the **browser assistant**: *JobPilot fills forms; you remain in control.*

- **Opened by you, on the application's own page.** From the Application Workspace,
  **Start browser assistant** opens the application's URL (from the application's
  snapshot, never one you type) in a controlled Chromium and reads the form. Nothing
  opens, fills or attaches until you ask.
- **Discover → map → review → approve → fill → verify.** Each field (text, email, phone,
  number, date, textarea, select, radio, checkbox groups, file) is mapped to one of the
  application's questions (the same V0.9 questions; new ones are created for fields
  nothing fits, never duplicated on rediscovery). Uncertain mappings wait for you.
  Only **approved** answers are typed in — yours, your saved application data, or an AI
  answer you accepted. An unreviewed AI suggestion is **never** filled; a missing answer
  is "Needs your input", also on required fields. Every value is read back and checked.
- **You are always in control.** Pause, take control, edit anything, resume, stop. While
  you are in control nothing is written; what you changed is never overwritten (resuming
  looks first); consent and legal checkboxes are never ticked for you.
- **It hands back, never works around.** A CAPTCHA or security check, a login, a refused
  page or an unexpected website stops the assistant and returns the browser to you.
- **Documents.** A file field proposes your current tailored CV or Anschreiben; it is
  attached only after **Use this file**, and exactly that version.
- **It never submits.** V0.10 has no way to press a button: final actions
  (*Submit, Apply, Bewerbung absenden …*) are recognized, never pressed, and you are told
  to submit manually.

See [`docs/browser-automation.md`](docs/browser-automation.md). Needs Chromium
(`playwright install chromium`); set `AUTOMATION_BROWSER_HEADLESS=true` on a machine
without a display. Not in V0.10: submission, review before submission (V0.11),
CAPTCHA or login handling beyond handing the browser back, link following, custom
(ARIA) controls.

V0.11 adds **Review & Submit**: *AI prepares the application; you review it and
authorize it; only then does JobPilot press the employer's final button — once.*

- **Final review.** Exactly what would be sent, read from the live page: destination,
  every value with its source ("Your answer", "From your application data", "AI-generated
  → accepted by you"), the exact CV / Anschreiben versions, every consent, and everything
  still unresolved. Confirming it stores an immutable snapshot and its SHA-256.
- **Consents, one by one.** Each legal consent is authorized for its exact text; only then
  may JobPilot tick that one box. A changed text needs a new authorization.
- **Explicit authorization.** A checkbox and **Authorize & Submit** — scoped to that review,
  its hash, the session and the website; it expires and works once. Any change (an edit,
  a reload, taking control, another document) invalidates it.
- **Exactly once, never retried.** The server re-checks the live page under a lock and
  presses the one final button through a separate, single-use capability (the fill
  assistant still cannot press anything). Success needs a confirmation on the page; anything
  uncertain is *SUBMISSION_UNKNOWN* — JobPilot will not submit again until you have checked.

See the Submission Safety Model in
[`docs/browser-automation.md` §24](docs/browser-automation.md#24-v011-review--submit-the-submission-safety-model).
Not in V0.11: application tracking, follow-ups, interviews (V0.12), multi-step forms,
retries of any kind, bulk submission.

## 3. Tech stack

| Layer      | Technology                                             |
| ---------- | ------------------------------------------------------- |
| Frontend   | Next.js (App Router), React, TypeScript, Tailwind CSS   |
| Backend    | Python, FastAPI, Pydantic, SQLAlchemy, Alembic          |
| AI         | Provider-agnostic `AIProvider`; Anthropic implementation |
| Job sources | Provider-agnostic `JobSource`; Greenhouse + Lever (httpx) |
| Extraction | pypdf (PDF), python-docx (DOCX)                          |
| Database   | PostgreSQL                                              |
| Infra      | Docker Compose (PostgreSQL)                             |
| Testing    | Pytest (backend), Vitest + Testing Library (frontend), Playwright (E2E) |

## 4. Project structure

```
jobpilot/
├── frontend/           Next.js app (App Router)
│   ├── app/            Routes: /, /login, /register, /dashboard, /profile, /jobs,
│   │                     /applications/[id], ...
│   ├── components/     Reusable UI, auth, profile, resume, dashboard components
│   ├── lib/             API client, auth token storage, typed API calls
│   ├── types/           Shared TypeScript types mirroring backend schemas
│   └── e2e/             Playwright real-browser tests
│
├── backend/
│   ├── app/
│   │   ├── ai/          AI provider abstraction, providers, versioned prompts
│   │   ├── api/v1/      Routers: auth, users, profile (+ skills/languages/
│   │   │                  completeness), preferences (+ job-titles/locations),
│   │   │                  resumes (+ Resume Intelligence), jobs (V0.3),
│   │   │                  matching (V0.6), applications (V0.7),
│   │   │                  documents (V0.8), questions (V0.9),
│   │                  automation (V0.10)
│   │   ├── core/        Config, security, logging, error handling
│   │   ├── db/          SQLAlchemy base + session
│   │   ├── models/      SQLAlchemy models
│   │   ├── schemas/     Pydantic request/response schemas
│   │   └── services/    Business logic, storage abstraction, resume_intelligence/
│   │                      pipeline, job_discovery/ (sources, normalizer,
│   │                      normalization/, deduplication/, discovery),
│   │                      matching/ (V0.6), applications/ (V0.7),
│   │                      documents/ (V0.8: facts, context, validation),
│   │                      questions/ (V0.9), automation/ (V0.10: browser/,
│   │                      discovery, mapping, resolver, filler, controller)
│   ├── alembic/         Database migrations
│   ├── e2e/             Backend launcher for real-browser E2E (mock job boards, stub AI)
│   └── tests/           Pytest suite
│
├── docs/
│   ├── architecture.md
│   ├── resume-intelligence.md
│   ├── job-discovery.md
│   ├── job-intelligence.md
│   ├── job-matching.md
│   ├── application-workspace.md
│   ├── tailored-documents.md
│   ├── application-questions.md
│   └── browser-automation.md
│
├── docker-compose.yml   PostgreSQL (dev infrastructure)
├── .env.example
└── README.md
```

## 5. Prerequisites

- Python 3.11+
- Node.js 20+ and npm
- Docker (for PostgreSQL) — or a local PostgreSQL 16 instance

## 6. Environment setup

```bash
cp .env.example .env
```

Edit `.env` as needed. Never commit `.env` — it's gitignored. Key variables:

- `SECRET_KEY` — JWT signing secret. Use a long random string in any
  non-local environment.
- `DATABASE_URL` — SQLAlchemy connection string for the backend.
- `STORAGE_PROVIDER` / `STORAGE_PATH` — resume storage backend (`local` in V0).
- `NEXT_PUBLIC_API_URL` — base URL the frontend uses to reach the backend API.
- `AI_PROVIDER` / `ANTHROPIC_API_KEY` / `AI_MODEL` — the AI provider for resume
  analysis (V0.2). Without an API key, everything else works and resume
  analysis reports "AI provider not configured". See
  [`docs/resume-intelligence.md`](docs/resume-intelligence.md#configuration-environment-only)
  for all AI-related settings.
- `JOB_SOURCES_ENABLED` / `GREENHOUSE_BOARD_TOKENS` / `LEVER_SITES` /
  `LEVER_INSTANCE`: which job sources and company boards Job Discovery
  (V0.3) reads. The sources are public APIs and need no credentials. See
  [`docs/job-discovery.md`](docs/job-discovery.md#4-configuration).

## 7. Start PostgreSQL

```bash
docker compose up -d
```

This starts PostgreSQL on `localhost:5432` with the credentials from `.env`.
(If you don't have Docker available, any local PostgreSQL 16 instance with a
matching `DATABASE_URL` works too.)

## 8. Run database migrations

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
```

To create a new migration after changing models:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

## 9. Start the backend

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

The API is served at `http://localhost:8000`, versioned under
`/api/v1/...` (e.g. `/api/v1/auth/register`). A health check is available at
`GET /health`.

## 10. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

The app is served at `http://localhost:3000` and talks to the backend using
`NEXT_PUBLIC_API_URL` (defaults to `http://localhost:8000/api/v1`).

## 11. Run tests

Backend:

```bash
cd backend
source .venv/bin/activate
pytest                  # tests
ruff check app tests    # lint
mypy app                # type check
```

Backend tests run against a separate `jobpilot_test` database (the
`DATABASE_URL` tests use is derived automatically; create the database once
with `createdb jobpilot_test` or let your Postgres role create it).
Tests never call a real AI API: they force `AI_PROVIDER=disabled` and inject a
deterministic fake provider backed by fixtures in `backend/tests/fixtures/resume/`
(for V0.8 documents, V0.9 answers and Humanize the fake restates the facts it is
shown, and tests make it unfaithful on purpose to prove invented claims are
rejected).
V0.10 browser tests drive a **real headless Chromium** against a local fake employer
website (`backend/tests/fake_site.py`; it needs `playwright install chromium`, or
`TEST_CHROMIUM_PATH`). They never touch the internet, and the page's *Submit application*
button reports every click so the tests can prove it was never pressed.
Tests never call a real job board either: sources run over a mocked HTTP
transport with fixtures in `backend/tests/fixtures/jobs/`, and real HTTP
transports raise inside the test suite.

Frontend:

```bash
cd frontend
npm test        # Vitest + Testing Library
npm run lint     # ESLint
npm run typecheck  # tsc --noEmit
npm run build    # production build sanity check
```

Real-browser E2E (Playwright + Chromium against the real backend, a freshly
migrated `<db>_e2e` database and the production frontend build; job boards
are served by deterministic local mocks and the AI is a deterministic stub, see
`backend/e2e/serve.py`):

```bash
cd frontend
npm run build
E2E_PYTHON=../backend/.venv/bin/python npm run e2e
```

(For V0.10 the E2E backend also serves a local fake employer website and a headless
Chromium for the browser assistant, plus a test-only `/e2e/human/...` stand-in for the
person at the browser window; see the docstring of `backend/e2e/serve.py`.)

Live source validation (V0.4.1) contacts the real Greenhouse and Lever APIs
through the production pipeline and needs network access to
`boards-api.greenhouse.io` and `api.lever.co` (in a Claude Code cloud environment, allow these hosts in the
environment's network access settings). The V0.4.1 run passed against 5 real
boards; see `docs/job-intelligence.md` §22:

```bash
cd backend
python -m e2e.live_sources --capture tests/fixtures/jobs/live
JOBPILOT_LIVE_SOURCES=1 pytest tests/test_live_sources.py
```

`backend/tests/test_migrations.py` runs the Alembic migrations against a
scratch database (`<test db>_migrations`); it is skipped if your Postgres
role can't create databases.

## 12. API endpoints (V0 – V0.11)

All endpoints are versioned under `/api/v1`. Full request/response shapes
are documented in `docs/architecture.md`.

```
POST   /auth/register
POST   /auth/login
POST   /auth/logout
GET    /users/me

GET    /profile                        personal + professional + industries
PUT    /profile
GET    /profile/completeness
GET    /profile/skills
POST   /profile/skills
PUT    /profile/skills/{id}
DELETE /profile/skills/{id}
GET    /profile/languages
POST   /profile/languages
DELETE /profile/languages/{id}

GET    /preferences                     salary, employment types, work model,
PUT    /preferences                     relocation, travel, deal breakers, ...
GET    /preferences/job-titles
POST   /preferences/job-titles
DELETE /preferences/job-titles/{id}
GET    /preferences/locations
POST   /preferences/locations
DELETE /preferences/locations/{id}

GET    /resumes                         all resume versions, newest first
GET    /resumes/current                 newest version
POST   /resumes                         upload (creates a new version)
DELETE /resumes/{id}

POST   /resumes/{id}/process            start analysis (202) or reuse (200); {"force": true} reprocesses
GET    /resumes/{id}/profile            status, latest attempt, extracted profile, conflicts
GET    /resumes/{id}/profile/source     extracted text ("View source")
PATCH  /resumes/{id}/profile/items/{item_id}   accept | edit | reject | reset
POST   /resumes/{id}/profile/accept-pending
POST   /resumes/{id}/profile/confirm
POST   /resumes/{id}/profile/reopen

GET    /jobs/sources                    job sources and the filters each can apply
GET    /jobs/criteria/defaults          search criteria derived from job preferences
POST   /jobs/discover                   start discovery (202); body {"criteria": {...}} optional
GET    /jobs/discover/runs/latest       latest discovery run
GET    /jobs/discover/runs/{id}         run status + per-source summary
GET    /jobs                            your jobs, one per canonical job (sources, source_count):
                                        page, page_size, source, q, location, work_model,
                                        employment_type, posted_after, saved, sort
GET    /jobs/{id}                       full job incl. every source posting (source_postings)
GET    /jobs/{id}/sources               where the job was found: postings + original URLs (V0.4)
PUT    /jobs/{id}/save                  save
DELETE /jobs/{id}/save                  unsave

GET    /jobs/{id}/match                 your match: not_calculated | calculated | stale (V0.6)
POST   /jobs/{id}/match                 calculate / recalculate (deterministic, no AI)
POST   /jobs/match                      batch: {"job_ids": [...≤25]} or {"limit": n}; no AI
POST   /jobs/{id}/match/explanation     optional AI explanation, validated and cached

POST   /jobs/{id}/application           Start Application (V0.7): 201 new, or 200 the existing one
GET    /applications                    your applications (page, page_size)
GET    /applications/{id}               workspace: preparation data, job context, resume, match, progress
PATCH  /applications/{id}               update application-specific data (partial; null clears)

GET    /applications/{id}/documents                  every generation attempt + what's needed next (V0.8)
GET    /applications/{id}/documents/{document_id}    one validated document, its validation and sources
POST   /applications/{id}/documents/cv               generate a new tailored CV version (no body)
POST   /applications/{id}/documents/cover-letter     generate a new Anschreiben version (no body)

POST   /applications/{id}/documents/cover-letter/humanize   ✨ reword the letter as a new, validated version (V0.9)
POST   /applications/{id}/documents/{document_id}/edit      save your edited text as a new version (V0.9)
PUT    /applications/{id}/cv-photo      GET / DELETE        your own CV photo (V0.9)

POST   /applications/{id}/questions                         add a question (V0.9)
GET    /applications/{id}/questions                         the questions with their answers
GET    /applications/{id}/questions/{qid}                   PATCH / DELETE the same
GET    /applications/{id}/questions/{qid}/generations       every attempt (audit trail)
POST   /applications/{id}/questions/{qid}/generate          suggest an answer (deterministic, or AI)
POST   /applications/{id}/questions/{qid}/regenerate        a new suggestion; never changes your answer
POST   /applications/{id}/questions/{qid}/accept            accept the suggestion as your answer
POST   /applications/{id}/questions/{qid}/reject            reject the suggestion
POST   /applications/{id}/questions/{qid}/manual            your own answer (no AI needed)
POST   /applications/{id}/questions/{qid}/clear             remove your answer

POST   /applications/{id}/automation/start                  open the application page in the browser assistant (V0.10)
GET    /applications/{id}/automation                        sessions, and whether one can be started
GET    /applications/{id}/automation/{sid}                  the session (status, page, counts, what you can do now)
GET    /applications/{id}/automation/{sid}/fields           discovered fields, their questions, what is approved
GET    /applications/{id}/automation/{sid}/events           what the assistant did (codes only)
GET    /applications/{id}/automation/{sid}/screenshot       a picture of the browser, never stored
POST   /applications/{id}/automation/{sid}/pause            stop changing anything
POST   /applications/{id}/automation/{sid}/take-control     the browser is yours
POST   /applications/{id}/automation/{sid}/resume           give it back (looks first; keeps your edits)
POST   /applications/{id}/automation/{sid}/stop             end the assistant
POST   /applications/{id}/automation/{sid}/discover         read the page again (reads only)
POST   /applications/{id}/automation/{sid}/fill             fill the approved answers into empty fields
POST   /applications/{id}/automation/{sid}/suggest          AI suggestions for unanswered questions (never accepted for you)
POST   /applications/{id}/automation/{sid}/confirm-domain   this website is part of my application
POST   /applications/{id}/automation/{sid}/fields/{fid}/mapping    confirm / change / leave unmapped / add a question
PUT    /applications/{id}/automation/{sid}/fields/{fid}/document   "Use this file" (DELETE: don't attach)
GET    /applications/{id}/automation/{sid}/final-review     what would be submitted now (V0.11)
POST   /applications/{id}/automation/{sid}/final-review     confirm the final review you saw
POST   /applications/{id}/automation/{sid}/consents/{fid}   give one consent, for its exact text (DELETE: withdraw)
POST   /applications/{id}/automation/{sid}/submission-authorization   your explicit authorization
POST   /applications/{id}/automation/{sid}/submit           submit under an authorization, once
GET    /applications/{id}/automation/submissions            submission attempts (the audit trail)
POST   /applications/{id}/automation/submissions/{aid}/verification   after an unknown result
```

`GET /jobs` and `GET /jobs/{id}` include each job's `match` summary (or
`null`) and your `application` (`{id, status}` or `null`);
`GET /jobs?sort=match` lists the best Overall Match first.

Note: `PUT /preferences` and `PUT /profile` replace the fields they cover in
full. The frontend always round-trips the job titles/locations it isn't
editing back into a `PUT /preferences` call so those dedicated-endpoint
lists aren't accidentally cleared — see "Profile & preferences data model"
in `docs/architecture.md`.

## 13. Roadmap

```
V0     Foundation                  ✅
V0.1   User Profile & Job Preference Intelligence   ✅
V0.2   Resume Intelligence         ✅
V0.3   Job Discovery               ✅
V0.4   Job Intelligence & Cross-Source Normalization   ✅
V0.5   Matching foundations        ✅ (V0.5.0 data model, V0.5.1 taxonomy)
V0.6   Job Matching & Explainability   ✅
       Job Dashboard / MVP
V0.7   Application Workspace        ✅
V0.8   Tailored CV + Anschreiben    ✅
V0.9   Application Question AI + Human Control   ✅
V0.10  Browser Automation & Human-Controlled Filling   ✅
V0.11  Review & Submit             ✅
V0.12  Application Tracking
V0.13  Multi-source Expansion
V0.14  Smart Apply Strategy
V1.0   Production Release
```

See [`docs/architecture.md`](docs/architecture.md) for the architectural
decisions behind this foundation and how later versions plug in.
