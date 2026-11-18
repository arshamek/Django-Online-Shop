# Installation (local, without Docker)

Backend lives in `maral_backend/`. The frontend (repository root) is untouched and keeps its own `npm`/`bun` workflow.

## Prerequisites
| Tool | Version | Notes |
|---|---|---|
| Python | 3.12 | matches `mssql-django` 2.0 and Django 5.2 LTS |
| Microsoft ODBC Driver 18 for SQL Server | 18.x | required by `pyodbc`; install from Microsoft (Windows installer, `msodbcsql18` on Debian/Ubuntu, `brew install msodbcsql18` on macOS) |
| SQL Server | 2017–2025 | Developer edition in a container is fine for development |
| Redis | 7.x | cache, throttling, Celery broker |

## Steps
```bash
cd maral_backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
cp .env.example .env                                   # never commit .env
```
Edit `.env`: at minimum `DB_*` and `REDIS_URL` (see [ENVIRONMENT.md](ENVIRONMENT.md)). Create an empty database first ([DATABASE.md](DATABASE.md)).

```bash
python manage.py check_database     # connectivity + server details
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver          # http://localhost:8000
```
In other terminals: `celery -A config worker -l INFO -Q default,imports,translations,pricing,notifications` and `celery -A config beat -l INFO` ([BACKGROUND_WORKERS.md](BACKGROUND_WORKERS.md)).

## Entry points
| URL | Purpose |
|---|---|
| `/health/` | liveness (no dependencies) |
| `/ready/` | readiness: database + Redis (503 when either is down) |
| `/api/v1/` | versioned API root |
| `/api/docs/`, `/api/redoc/`, `/api/schema/` | Swagger UI, ReDoc, OpenAPI 3 (on by default in development, **off by default in production**) |
| `/admin/` | Django admin (path configurable with `DJANGO_ADMIN_URL`) |

## Settings modules
`manage.py` defaults to `config.settings.development`; `wsgi.py`, `asgi.py` and Celery default to `config.settings.production`. Tests use `config.settings.testing` (see `pytest.ini`).

## Verification
```bash
pytest                              # needs SQL Server (TEST_DATABASE=sqlserver, the default)
TEST_DATABASE=sqlite pytest         # smoke mode only - validates nothing SQL-Server-specific
ruff check . && ruff format --check .
bash scripts/acceptance.sh          # full Phase 2 checklist against a running stack
```
`makemigrations --check` needs a reachable database; if the ODBC driver is missing it fails with a driver error rather than a warning.

## Project layout
```
maral_backend/
  config/            settings/{base,development,testing,production}.py, urls.py, api_v1.py, celery.py
  apps/common/       API helpers (errors, pagination, filters), middleware, health, Celery tasks, check_* commands
  apps/accounts/     custom User model only (no endpoints yet)
  tests/  scripts/  requirements/  Dockerfile  docker-compose.yml  .env.example
```
Business apps (catalogue, pricing, orders, ...) are added in later phases and register routes in `config/api_v1.py`.
