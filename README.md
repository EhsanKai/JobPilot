# JobPilot

JobPilot is an AI-powered job application platform. It will eventually help
users upload and understand their resume, define job preferences, search
jobs across multiple sources, calculate an explainable match score, generate
tailored application documents, and track applications end to end.

This repository currently implements **V0 — Foundation** and
**V0.1 — User Profile & Job Preference Intelligence**.

## 1. What JobPilot is

JobPilot aims to automate the tedious parts of job hunting: tracking
preferences, tailoring documents, and filling out applications — while
keeping the user in control of what gets submitted and where.

## 2. Current scope (V0 + V0.1)

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

## 3. Tech stack

| Layer      | Technology                                             |
| ---------- | ------------------------------------------------------- |
| Frontend   | Next.js (App Router), React, TypeScript, Tailwind CSS   |
| Backend    | Python, FastAPI, Pydantic, SQLAlchemy, Alembic          |
| Database   | PostgreSQL                                              |
| Infra      | Docker Compose (PostgreSQL)                             |
| Testing    | Pytest (backend), Vitest + Testing Library (frontend)   |

## 4. Project structure

```
jobpilot/
├── frontend/           Next.js app (App Router)
│   ├── app/            Routes: /, /login, /register, /dashboard, /profile, ...
│   ├── components/     Reusable UI, auth, profile, resume, dashboard components
│   ├── lib/             API client, auth token storage, typed API calls
│   └── types/           Shared TypeScript types mirroring backend schemas
│
├── backend/
│   ├── app/
│   │   ├── api/v1/      Routers: auth, users, profile (+ skills/languages/
│   │   │                  completeness), preferences (+ job-titles/locations), resumes
│   │   ├── core/        Config, security, logging, error handling
│   │   ├── db/          SQLAlchemy base + session
│   │   ├── models/      SQLAlchemy models
│   │   ├── schemas/     Pydantic request/response schemas
│   │   └── services/    Business logic, storage abstraction, future interfaces
│   ├── alembic/         Database migrations
│   └── tests/           Pytest suite
│
├── docs/
│   └── architecture.md
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

Frontend:

```bash
cd frontend
npm test        # Vitest + Testing Library
npm run lint     # ESLint
npm run typecheck  # tsc --noEmit
npm run build    # production build sanity check
```

## 12. API endpoints (V0 + V0.1)

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

GET    /resumes/current
POST   /resumes
DELETE /resumes/{id}
```

Note: `PUT /preferences` and `PUT /profile` replace the fields they cover in
full. The frontend always round-trips the job titles/locations it isn't
editing back into a `PUT /preferences` call so those dedicated-endpoint
lists aren't accidentally cleared — see "Profile & preferences data model"
in `docs/architecture.md`.

## 13. Roadmap

```
V0     Foundation                  ✅
V0.1   User Profile & Job Preference Intelligence   ✅ (current)
V0.2   Resume Intelligence
V0.3   Job Discovery
V0.4   Job Normalization & Deduplication
V0.5   AI Matching
V0.6   Job Dashboard / MVP
V0.7   Application Workspace
V0.8   Tailored CV + Anschreiben
V0.9   Application Question AI
V0.10  Browser Automation
V0.11  Review & Submit
V0.12  Application Tracking
V0.13  Multi-source Expansion
V0.14  Smart Apply Strategy
V1.0   Production Release
```

See [`docs/architecture.md`](docs/architecture.md) for the architectural
decisions behind this foundation and how later versions plug in.
