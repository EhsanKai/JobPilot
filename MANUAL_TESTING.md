# JobPilot Manual Testing — Quick Start

Everything runs in Docker. You need **only Docker** (Docker Desktop on macOS/Windows, or
Docker Engine with the Compose plugin on Linux). You do **not** need PostgreSQL, `psql`,
Python, a virtual environment, Node.js, `npm install` or `pip install`.

## 1. Start JobPilot with test data

```bash
git clone https://github.com/EhsanKai/JobPilot.git
cd JobPilot
cp .env.example .env                                  # optional: the defaults work
docker compose --profile manual-test up --build
```

What happens, in order (each step waits for the previous one's health check, not for a
fixed sleep):

1. **postgres** starts and becomes healthy (`pg_isready`).
2. **backend** waits for the database, runs **all Alembic migrations** (a fresh database is
   created from scratch; an existing one is upgraded), then starts the API. It reports
   healthy only when the database is reachable *and* its schema is at the current revision
   (`/health/ready`). A failed migration stops the backend with a clear `event=migration_failed`
   log line — nothing starts on a half-initialized database.
3. **frontend** starts once the backend is healthy.
4. **seed** (only with `--profile manual-test`) creates the synthetic test user, then exits.

When you see `seed-1 | Seeded synthetic manual-test data:` open **http://localhost:3000**.

The first build takes a few minutes (it downloads base images and dependencies); later
starts take seconds.

Prefer a short command? `./start-dev.sh test-data` does the same in the background
(`./start-dev.sh help` lists all commands).

## 2. Log in

| | |
|---|---|
| E-mail | `test.user@example.com` |
| Password | `TestPassword123!` |

`example.com` is reserved for documentation (RFC 2606) and passes JobPilot's real e-mail
validation. Do not use `.local` addresses: `.local` is a special-use name that the e-mail
validation (correctly) rejects.

## 3. What the test data contains

Synthetic only — the person, the company "Musterfirma Demo GmbH" and its postings do not
exist. Everything is created through JobPilot's real code paths (registration, profile
services, the resume upload validation, the Greenhouse/Lever discovery pipeline with
synthetic board payloads, deterministic matching, the tracking state machine):

- **Profile**: Max Mustermann, Senior Software Engineer, Munich; 8 skills; German (native),
  English (fluent), Spanish (basic).
- **Preferences**: Senior Backend Engineer / Full-Stack Engineer, Munich or Berlin, hybrid or
  remote, EUR 75–90k, deal breaker: working-student roles.
- **Resume**: one DOCX, uploaded but not analyzed (analysis needs `ANTHROPIC_API_KEY`).
- **Jobs** (7 jobs from 8 postings): an excellent match published on both Greenhouse and
  Lever (shown as one job with two sources), a good remote match, a partial match, a weak
  match, a junior role (saved), a management role, and a working-student role that hits
  the deal breaker.
- **Applications**: PREPARING, APPLIED (follow-up in 7 days), INTERVIEW, REJECTED, WITHDRAWN —
  each with its tracking timeline.

The postings' application links do not lead to real forms.

## 4. Reset

| Goal | Command |
|---|---|
| Re-create only the test user | `./start-dev.sh seed --reset` (= `docker compose run --rm seed --reset`) |
| Wipe **everything** (database + uploads) and start fresh with test data | `./start-dev.sh reset` |
| The same by hand | `docker compose down -v` then `docker compose --profile manual-test up --build` |
| Stop without losing data | `docker compose down` (or Ctrl+C in the `up` terminal) |

## 5. Test checklist (≈35 min)

1. **Auth & dashboard (5 min)** — log in; the dashboard shows profile completeness, resume
   status and the applications summary (1 applied, 1 interview, 1 rejected …). Log out and
   back in.
2. **Profile & resume (5 min)** — edit the profile; the seeded resume is listed. With an
   `ANTHROPIC_API_KEY` in `.env` (then `docker compose up -d backend`), analyze it and
   review the extracted facts.
3. **Jobs & matching (5 min)** — 7 jobs; the cross-source job shows both sources; the match
   panel explains strengths and gaps; the working-student job shows the deal breaker.
4. **Applications & documents (10 min)** — open the PREPARING application; with an AI key,
   generate a tailored CV and cover letter; add an application question and answer it.
5. **Tracking (5 min)** — move an application through statuses; add a note; check the
   timeline and that the dashboard counts follow.
6. **Privacy & security (5 min, V0.13)** — *Settings*: "My data" lists what is stored;
   *Download my data* gives a ZIP (JSON + your files); switch *external AI processing* off
   and see AI features refuse without calling the provider; *Cookie preferences* states
   that JobPilot uses no optional cookies; *Log out everywhere*; finally *Delete my account*
   on a throw-away account (needs the password and the phrase `DELETE MY ACCOUNT`).
7. **Wrong-password lockout** — 5 wrong passwords for one account → "Too many unsuccessful
   sign-in attempts"; it clears after 15 minutes.

Detailed scenarios: [docs/manual-product-test.md](docs/manual-product-test.md). Record
findings in [docs/product-validation.md](docs/product-validation.md).

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `port is already allocated` | Another program uses 3000, 8000 or 5432. Set `FRONTEND_PORT`, `BACKEND_PORT` or `POSTGRES_PORT` in `.env` (e.g. `POSTGRES_PORT=55432`). If you change `BACKEND_PORT`, also set `NEXT_PUBLIC_API_URL=http://localhost:<port>/api/v1` and rebuild (`docker compose up --build`). |
| backend keeps restarting | `./start-dev.sh logs backend` — look for `event=migration_failed reason=...`. |
| `relation "users" does not exist` | Should not happen any more: the backend migrates before it starts. If you run the backend outside Docker, run `python -m app.db.migrate` first. |
| Login says "Invalid email or password" | Was the seed run? `./start-dev.sh seed`. Use `test.user@example.com`. |
| "Too many unsuccessful sign-in attempts" | Wait 15 minutes, or restart the backend (`docker compose restart backend`) — the counter lives in the API process. |
| AI features say "not configured" | Add `ANTHROPIC_API_KEY` to `.env`, then `docker compose up -d backend`. |
| Everything is weird | `./start-dev.sh reset` |

Is it healthy? `./start-dev.sh status` — all services `healthy`, seed `exited (0)`.
Automated check of the whole path: `./start-dev.sh smoke` (wipes local data, builds,
starts, seeds, logs in as the test user, checks the dashboard data, schema and frontend).

## 7. Not for production

The manual-test profile, its synthetic account and the development defaults in
`docker-compose.yml` (database password, `SECRET_KEY`) are for local testing only. The
backend refuses those defaults when `APP_ENV=production`, and the seed refuses to run there.
See [docs/docker-development.md](docs/docker-development.md) and [docs/security.md](docs/security.md).
