# Docker development environment (V0.13)

Docker is the default way to run JobPilot locally and the only thing a tester needs.
Contributors can still run the backend and frontend directly on the host (last section).

## Quick reference

```bash
cp .env.example .env                                   # optional; every value has a default
docker compose up --build                              # db + backend + frontend
docker compose --profile manual-test up --build        # ... plus the synthetic test user
docker compose run --rm seed [--reset]                 # (re)create the test user any time
docker compose down                                    # stop, keep data
docker compose down -v                                 # stop and DELETE all local data
```

`./start-dev.sh` wraps these (`up`, `test-data`, `seed`, `reset`, `status`, `logs`, `down`,
`smoke`). Open http://localhost:3000; the API is on http://localhost:8000 (`/docs` in
development).

## Services

| Service | Image | Purpose | Health check |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | Database (volume `jobpilot_postgres_data`) | `pg_isready` for the configured user and database |
| `backend` | built from `backend/Dockerfile` | Migrations, then the FastAPI API (volume `jobpilot_storage` for uploads) | `GET /health/ready`: database reachable **and** schema at the Alembic head |
| `frontend` | built from `frontend/Dockerfile` | Next.js production server | `GET /login` |
| `seed` | the backend image | Synthetic manual-test data; profile `manual-test` only; runs once and exits | — |

Ports are published on `127.0.0.1` only (not on your network): 3000, 8000, 5432 by default,
configurable with `FRONTEND_PORT`, `BACKEND_PORT`, `POSTGRES_PORT`.

## Startup order and database initialization

```
postgres starts ──▶ pg_isready healthy
                        │  (depends_on: service_healthy — no fixed sleeps)
                        ▼
backend container: docker-entrypoint.sh
   1. python -m app.db.migrate
        - waits for a real database connection (bounded backoff, DB_STARTUP_TIMEOUT_SECONDS)
        - takes a PostgreSQL advisory lock (two replicas never migrate at once)
        - alembic upgrade head   (fresh database: every revision from the initial schema)
        - verifies the schema is at the head revision
        - any failure: logs event=migration_failed reason=..., exits 1 → the API never starts
   2. exec uvicorn app.main:app
                        │  /health/ready == 200
                        ▼
frontend starts, seed (manual-test profile) runs
```

You never run `alembic upgrade head` by hand for a Docker environment. `RUN_MIGRATIONS=false`
skips step 1's upgrade (for deployments where a separate release job migrates); the
entrypoint then only *checks* the schema and refuses to start if it is not current.

Liveness vs. readiness: `/health` only says the process answers; `/health/ready` says it
can serve requests. Before V0.13 the only health endpoint reported "ok" on an empty
database, which is how the `relation "users" does not exist` failure went unnoticed.

## Seed data

`backend/scripts/seed_manual_test_data.py` (see MANUAL_TESTING.md for the content). It is
idempotent (an existing test user is left alone unless `--reset`), refuses to run when
`APP_ENV=production`, and refuses an unmigrated database. `--reset` removes the test user
through the real account-deletion code (database rows and files), then seeds again.

The default `docker compose up` never creates the synthetic user; only the `manual-test`
profile or an explicit `seed` run does.

## Environments

| `APP_ENV` | Used for | Notes |
|---|---|---|
| `development` | local work, Docker Compose default | API docs on; private browser hosts configurable |
| `test` | the pytest suite (set by `tests/conftest.py`) | AI and job boards are faked |
| `production` | anything real users reach | startup **refuses** development secrets, the `jobpilot/jobpilot` database credentials, wildcard CORS and `AUTOMATION_ALLOW_PRIVATE_HOSTS=true`; HSTS on; API docs off; SQL parameters hidden from logs |

"Manual test" is not a separate `APP_ENV`: it is development plus the `manual-test` Compose
profile. `docker-compose.yml` is a development/manual-test file — its defaults are public.
A production deployment must provide its own configuration (secrets from a secret store,
TLS in front, its own database credentials); see docs/security.md.

## Images

- **backend**: Ubuntu 24.04 (Python 3.12) runtime; dependencies from the pinned
  `requirements.txt` in a virtualenv built in a separate stage; Chromium's system libraries
  installed through Playwright's own dependency list; the version-matched headless Chromium
  copied from Microsoft's official `mcr.microsoft.com/playwright/python:v1.56.0-noble`
  image (only the headless shell, not Firefox/WebKit) — so builds need no browser
  download. Runs as the unprivileged user `jobpilot` (uid 10001); application code is
  read-only for it, only `/app/storage` is writable. ≈1.5 GB, mostly Chromium and its
  libraries.
- **frontend**: `node:22-alpine`, `npm ci` from the lockfile, Next.js `standalone` output;
  the runtime image holds only the server bundle and static assets and runs as `node`.
  ≈350 MB. `NEXT_PUBLIC_API_URL` is a build argument (it is compiled into the browser
  bundle and must never be a secret).
- No `.env`, secrets, tests or caches are copied into either image (`.dockerignore`, and
  explicit `COPY` lists).

When upgrading Playwright, update `playwright` in `requirements.txt`, `PLAYWRIGHT_IMAGE`
and `CHROMIUM_REVISION` in `backend/Dockerfile` together.

## The Browser Assistant in Docker

There is no display in a container, so Chromium runs headless and the assistant shows a
live screenshot instead of a window (`AUTOMATION_BROWSER_HEADLESS=true`). Private and
loopback addresses are refused (`AUTOMATION_ALLOW_PRIVATE_HOSTS=false`): all of the
browser's traffic goes through the egress filter described in docs/security.md. Chromium
gets `shm_size: 512m`.

## Clean-start verification

```bash
./start-dev.sh smoke
```

wipes local volumes, builds, starts, seeds, then — inside the containers — checks
readiness, logs in as `test.user@example.com`, reads the applications summary (5) and the
job list (7), verifies the schema is at head and that the frontend serves `/login` with a
Content-Security-Policy. It prints `SMOKE TEST PASSED` or stops at the first failure.

## Running behind a corporate proxy

Builds download Python and npm packages and Ubuntu packages. Docker passes the standard
`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` build arguments automatically when they are set
in `~/.docker/config.json` (`proxies`). A TLS-intercepting proxy additionally needs its CA
in the build; keep such adjustments in a local `docker-compose.override.yml` (git-ignored)
rather than in the shared files.

## Host development (contributors)

Not needed for testing. For fast backend/frontend iteration on the host:

```bash
docker compose up -d postgres                       # only the database, on 127.0.0.1:5432
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp ../.env.example .env                             # DATABASE_URL points at localhost:5432
.venv/bin/python -m app.db.migrate                  # same step the container runs
.venv/bin/uvicorn app.main:app --reload
cd ../frontend && npm ci && npm run dev
```

Tests: `backend/.venv/bin/pytest` needs a `jobpilot_test` database
(`docker compose exec postgres createdb -U jobpilot jobpilot_test`); the frontend gates are
`npm test`, `npm run typecheck`, `npm run lint`, `npm run build`, `npm run e2e`.
