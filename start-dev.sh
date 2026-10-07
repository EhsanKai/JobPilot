#!/usr/bin/env bash
# JobPilot local environment -- a thin wrapper around Docker Compose.
#
# Only Docker (with the Compose plugin) is required on this machine: no PostgreSQL, psql,
# Python or Node.js. Every command below runs inside the containers.
#
#   ./start-dev.sh                 start everything (= up)
#   ./start-dev.sh up              build and start db, backend (migrates on start), frontend
#   ./start-dev.sh test-data       start everything AND load the synthetic test user
#   ./start-dev.sh seed [--reset]  load the test user into a running stack (--reset: recreate it)
#   ./start-dev.sh reset           delete ALL local data (database + uploads), start fresh with test data
#   ./start-dev.sh status          services and their health
#   ./start-dev.sh logs [service]  follow logs (backend, frontend, postgres, seed)
#   ./start-dev.sh down            stop (data is kept)
#   ./start-dev.sh smoke           clean-start test: wipe, build, start, seed, log in, check, report
#
# Test account (after test-data / seed / reset): test.user@example.com / TestPassword123!
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE=(docker compose)
FRONTEND_URL="http://localhost:${FRONTEND_PORT:-3000}"
API_URL="http://localhost:${BACKEND_PORT:-8000}"

need_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "Docker is not installed. Install Docker Desktop (or Docker Engine + Compose plugin)." >&2
        exit 1
    fi
    if ! docker compose version >/dev/null 2>&1; then
        echo "The Docker Compose plugin is missing ('docker compose version' failed)." >&2
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        echo "The Docker daemon is not running. Start Docker Desktop / dockerd and retry." >&2
        exit 1
    fi
}

banner() {
    echo
    echo "  JobPilot is running:  ${FRONTEND_URL}"
    echo "  API:                  ${API_URL}/health/ready"
    echo
}

credentials() {
    echo "  Test account:  test.user@example.com / TestPassword123!"
    echo
}

cmd="${1:-up}"
shift || true

case "$cmd" in
    up)
        need_docker
        "${COMPOSE[@]}" up --build --detach --wait
        banner
        ;;
    test-data)
        need_docker
        "${COMPOSE[@]}" --profile manual-test up --build --detach --wait backend frontend
        "${COMPOSE[@]}" run --rm seed
        banner
        credentials
        ;;
    seed)
        need_docker
        "${COMPOSE[@]}" run --rm seed "$@"
        ;;
    reset)
        need_docker
        echo "This deletes ALL local JobPilot data (database and uploaded files)."
        if [ "${1:-}" != "--yes" ]; then
            read -r -p "Type 'yes' to continue: " answer
            [ "$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
        fi
        "${COMPOSE[@]}" --profile manual-test down --volumes --remove-orphans
        "${COMPOSE[@]}" up --build --detach --wait
        "${COMPOSE[@]}" run --rm seed
        banner
        credentials
        ;;
    status)
        need_docker
        "${COMPOSE[@]}" ps --all
        ;;
    logs)
        need_docker
        "${COMPOSE[@]}" logs --follow "$@"
        ;;
    down)
        need_docker
        "${COMPOSE[@]}" --profile manual-test down
        ;;
    smoke)
        # The release check for the local environment (V0.13): from nothing to a logged-in
        # dashboard, exactly as a tester would do it. Fails loudly at the first problem.
        need_docker
        echo "==> Wiping local state"
        "${COMPOSE[@]}" --profile manual-test down --volumes --remove-orphans
        echo "==> Building and starting (migrations run on backend start)"
        "${COMPOSE[@]}" up --build --detach --wait
        echo "==> Seeding the test user"
        "${COMPOSE[@]}" run --rm seed
        echo "==> Checking readiness"
        "${COMPOSE[@]}" exec -T backend python -c "
import json, urllib.request
r = json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5))
assert r == {'status': 'ok', 'database': 'ok'}, r
print('   readiness:', r)
"
        echo "==> Logging in as the test user and reading the dashboard data"
        "${COMPOSE[@]}" exec -T backend python -c "
import json, urllib.request
api = 'http://127.0.0.1:8000/api/v1'
def call(path, body=None, token=None):
    req = urllib.request.Request(api + path, method='POST' if body is not None else 'GET')
    req.add_header('Content-Type', 'application/json')
    if token: req.add_header('Authorization', 'Bearer ' + token)
    data = json.dumps(body).encode() if body is not None else None
    return json.load(urllib.request.urlopen(req, data, timeout=10))
token = call('/auth/login', {'email': 'test.user@example.com', 'password': 'TestPassword123!'})['access_token']
me = call('/users/me', token=token)
summary = call('/applications/summary', token=token)
jobs = call('/jobs?page=1&page_size=50', token=token)
assert me['email'] == 'test.user@example.com', me
assert summary['total'] == 5, summary
assert jobs['total'] == 7, jobs['total']
print('   login ok as', me['email'], '| applications:', summary['total'], '| jobs:', jobs['total'])
"
        echo "==> Checking the database schema"
        "${COMPOSE[@]}" exec -T backend python -m app.db.migrate --check
        echo "==> Checking the frontend"
        "${COMPOSE[@]}" exec -T frontend node -e "
fetch('http://127.0.0.1:3000/login').then(r => {
  if (!r.ok) { console.error('frontend /login', r.status); process.exit(1); }
  if (!r.headers.get('content-security-policy')) { console.error('no CSP header'); process.exit(1); }
  console.log('   frontend /login', r.status, '(CSP set)');
}).catch(e => { console.error(e); process.exit(1); })
"
        echo
        echo "SMOKE TEST PASSED"
        banner
        credentials
        ;;
    -h|--help|help)
        sed -n '2,17p' "$0"
        ;;
    *)
        echo "Unknown command: $cmd" >&2
        sed -n '2,17p' "$0" >&2
        exit 1
        ;;
esac
