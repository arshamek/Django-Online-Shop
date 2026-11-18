# Docker (development)

Files: `maral_backend/Dockerfile`, `maral_backend/docker-compose.yml`, `maral_backend/.dockerignore`. The frontend is **not** containerised here; run it from the repository root as before.

> **Not executed in Phase 2.** Docker was unavailable in the build sandbox. The compose file was validated as YAML only; the first `docker compose up --build` is the real test. Expect to fix small things (Microsoft repo URLs, SQL Server tool paths) on first run.

## Start
```bash
cd maral_backend
cp .env.example .env        # set MSSQL_SA_PASSWORD (>= 8 chars, upper+lower+digit+symbol)
docker compose up --build
```
Needs Docker Compose ≥ 2.24 (optional `env_file`). SQL Server runs as `linux/amd64` (emulated on Apple Silicon: slow, needs enough RAM).

| Service | Role |
|---|---|
| `sqlserver` | SQL Server 2022 Developer, data in volume `sqlserver-data`, port 1433 |
| `db-init` | one-shot: creates the database and enables RCSI |
| `redis` | Redis 7.4, AOF on, `noeviction`, volume `redis-data` |
| `migrate` | one-shot `manage.py migrate` |
| `web` | `runserver` on :8000 (source bind-mounted) |
| `worker` | Celery worker, all queues |
| `beat` | Celery beat |
| `flower` | profile `monitoring`: `docker compose --profile monitoring up flower` → http://127.0.0.1:5555 |

Compose overrides `DB_HOST`, `DB_USER` (`sa`, development only), `REDIS_URL`, `CELERY_BROKER_URL` regardless of `.env`.

## Common commands
```bash
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py check_database
docker compose exec web python manage.py check_celery
docker compose exec web bash scripts/acceptance.sh
docker compose exec web pytest
docker compose down            # keep data;  `down -v` deletes the volumes
```

## Image
One multi-stage image, different commands (web, worker, beat, flower), so there is no separate worker Dockerfile. Stages: `development` (dev requirements, runserver) and `production` (last stage; gunicorn, collected static files, non-root user `maral`, health probe through `scripts/healthcheck.py`). Both include Microsoft ODBC Driver 18 (Debian 12 repo) and compile nothing at runtime.
```bash
docker build -t maral-backend:prod .
docker run --env-file prod.env -p 8000:8000 maral-backend:prod
```
Production notes: no SQL Server/Redis containers in the production stack (managed services per decision D-3); run exactly one beat; the reverse proxy sets `X-Forwarded-Proto`; list the host your probe uses in `ALLOWED_HOSTS`.
