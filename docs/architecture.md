# JobPilot Architecture — V0

This document captures the architectural decisions behind the V0 foundation
and how later versions are expected to build on it without a rewrite.

## 1. Layered, modular pipeline

The long-term product is a pipeline:

```
Job Sources → Connectors → Normalized Job Model → Matching Engine → Application Engine
```

V0 only implements the data model at the "Normalized Job Model" stage (the
`jobs` and `applications` tables) and the `JobSource` interface future
connectors will implement. No connector, matching engine, or application
engine logic exists yet — they are later versions. The point of building the
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

`app/services/integrations/job_source.py` defines a `JobSource` abstract
base class with a single `search()` method. No implementation exists in V0.
Future connectors (`LinkedInConnector`, `IndeedConnector`,
`StepStoneConnector`, `GreenhouseConnector`, `WorkdayConnector`, ...) will
each implement this interface. The core application (matching, storage,
application flow) will depend only on `JobSource`, never on a specific
provider's API or scraping logic.

## 4. AI-provider-agnostic design

`app/services/integrations/ai_provider.py` defines abstract interfaces
(`AIProvider`, `ResumeParser`, `JobMatcher`, `CoverLetterGenerator`) that
future implementations (OpenAI, Anthropic, Google, a local LLM) will
satisfy. No AI calls are made in V0 — these are placeholders so later
versions don't require redesigning how AI is wired into the application.

## 5. Storage abstraction

Resume files are never written directly to `backend/storage/...` from
application code. Everything goes through `app/services/storage/base.py`'s
`FileStorage` interface (`save`, `delete`, `exists`). V0 ships
`LocalFileStorage`, suitable for local development only. Switching to S3,
GCS, Azure Blob, or Cloudflare R2 in a later version means writing one new
class that implements `FileStorage` — no changes to `resume_service.py` or
the API layer.

## 6. Database schema

The schema is normalized rather than storing lists as delimited strings:

- `users` — account + credentials.
- `user_profiles` — one-to-one with `users`; contact and career info. Kept
  separate from `users` so account fields (auth-related) and profile fields
  (editable career info) can evolve independently.
- `job_preferences` — one-to-one with `users` for scalar preferences
  (salary range, seniority, remote preference, relocation/travel
  willingness). List-like preferences (desired job titles, preferred
  locations, employment types, industries, languages) live in their own
  child tables (`job_preference_job_titles`, `job_preference_locations`,
  etc.) rather than as comma-separated strings, so they can be queried and
  validated properly.
- `resumes` — upload/storage metadata only (`storage_reference`, MIME type,
  size, status). No parsed content; V0 does not parse resumes.
- `jobs` — a normalized-job foundation table. Not populated by anything in
  V0; exists so `applications.job_id` has somewhere to point once job
  discovery (V0.3+) lands.
- `applications` — a foundation table for the future application workflow
  (status, salary expectation, start date). No document generation or
  submission logic exists yet.

Migrations are managed with Alembic (`backend/alembic/`); the initial
migration creates this full V0 schema.

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
  `dashboard/`) plus a small `ui/` kit (`Button`, `Input`, `Card`, `Alert`)
  so loading/error/success states look consistent everywhere.
- `lib/http.ts` — a single typed `fetch` wrapper (`api.get/post/put/delete`)
  that attaches the JWT, parses the backend's error envelope into an
  `ApiError`, and is the only place that knows about `NEXT_PUBLIC_API_URL`.
- `lib/api/*` — one typed module per backend domain (`auth.ts`,
  `profile.ts`, `preferences.ts`, `resumes.ts`, `users.ts`), so components
  never construct fetch calls or URLs themselves.
- `types/*` — TypeScript interfaces mirroring the backend's Pydantic
  schemas, kept in sync by hand in V0 (a generated client is a reasonable
  future improvement, not required for the foundation).

Every form/upload component manages explicit loading, success, error, and
empty states rather than leaving the user guessing whether an action worked.

## 10. Why no job matching, AI, or scraping yet

These require external APIs, significant business logic, and product
decisions (which providers, what scoring model, what automation risk is
acceptable) that are out of scope for a foundation. Building the interfaces
now (`JobSource`, `AIProvider`, `ResumeParser`, `JobMatcher`,
`CoverLetterGenerator`) without the implementations means V0.3–V0.9 can be
built additively on top of this schema and these service boundaries,
instead of requiring a redesign of the data model or API surface.
