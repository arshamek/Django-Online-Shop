#!/usr/bin/env bash
# Phase 2 acceptance checks against a RUNNING environment (real SQL Server + Redis + worker + web).
# Typical use with the dev stack:
#   docker compose up -d --build
#   docker compose exec web bash scripts/acceptance.sh
# or from a host virtualenv with .env pointing at reachable services and `runserver`/`celery worker` running.
# Exit code is non-zero if any check fails.
set -u
cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://localhost:8000}"
PY="${PYTHON:-python}"
pass=0; fail=0; results=()

run() {  # run "<label>" <command...>
  local label="$1"; shift
  local out
  if out="$("$@" 2>&1)"; then
    pass=$((pass + 1)); results+=("PASS  $label")
  else
    fail=$((fail + 1)); results+=("FAIL  $label"); printf '\n--- %s failed ---\n%s\n' "$label" "$out" >&2
  fi
}

http_ok() {  # http_ok <path> [expected_status]
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL$1")" || return 1
  [ "$code" = "${2:-200}" ] || { echo "GET $1 -> $code (expected ${2:-200})"; return 1; }
}

json_has() {  # json_has <path> <python-expression on d>
  curl -fsS --max-time 10 "$BASE_URL$1" | "$PY" -c "import json,sys; d=json.load(sys.stdin); assert $2, d"
}

run "Django starts / system check"            "$PY" manage.py check
run "SQL Server connection"                   "$PY" manage.py check_database
run "Migrations apply"                        "$PY" manage.py migrate --noinput
run "No model changes missing a migration"    "$PY" manage.py makemigrations --check --dry-run
run "Redis connects (cache + broker)"         "$PY" manage.py check_redis
run "Celery task executes (needs worker)"     "$PY" manage.py check_celery
run "GET /health/ (liveness)"                 http_ok /health/
run "GET /ready/ (database + redis)"          http_ok /ready/
run "GET /api/v1/ (API responds)"             json_has /api/v1/ "d['status'] == 'ok' and d['version'] == 'v1'"
run "GET /api/v2/ is a JSON 404"              http_ok /api/v2/ 404
run "OpenAPI schema loads"                    http_ok /api/schema/
run "Swagger UI loads"                        http_ok /api/docs/
run "Test suite (pytest)"                     "$PY" -m pytest -q

printf '\n==== Phase 2 acceptance ====\n'
printf '%s\n' "${results[@]}"
printf 'passed: %d  failed: %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
