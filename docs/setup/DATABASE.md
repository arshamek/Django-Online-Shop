# Database: Microsoft SQL Server

Backend: `mssql-django` 2.0.0 (Microsoft-supported) over `pyodbc` 5.5 and ODBC Driver 18, Django 5.2 LTS (decisions D-1/D-2 in `docs/architecture/TECHNICAL_DECISIONS.md`).

> **Verification status.** The sandbox this phase was built in has no SQL Server and no ODBC driver. Verified there: the `mssql` backend imports, settings are read from the environment, a missing driver/server produces a clean error from `check_database` and a `503` from `/ready/`. **NOT verified there:** a real connection, migrations on SQL Server, and the filtered unique indexes. Run `bash scripts/acceptance.sh` (or the commands below) against your SQL Server before relying on it.

## Configuration
`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` (1433), `DB_DRIVER` (`ODBC Driver 18 for SQL Server`), `DB_ENCRYPT` (`yes`), `DB_TRUST_SERVER_CERTIFICATE` (`no`; `yes` only for local self-signed containers), `DB_CONN_MAX_AGE` (60 s, with connection health checks). Transactions are not wrapped per request (`ATOMIC_REQUESTS` off); services own their transactions.

## Create the database
```sql
CREATE DATABASE [maral] COLLATE Latin1_General_100_CI_AS_SC;
ALTER DATABASE [maral] SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE;
```
* `READ_COMMITTED_SNAPSHOT` (RCSI) is recommended so readers do not block writers; `check_database` warns when it is off. The compose `db-init` service does this for you.
* Collation: `nvarchar` columns everywhere; case-insensitive, accent-sensitive `_100_` collation as a **provisional default**. Final choice (and Persian normalisation) is validated by spike T9 (decision D-4).
* Production: use a least-privilege login, never `sa`. Migrations need DDL rights; the runtime login ideally does not (separate migration credentials are not set up yet).
* Express edition has hard size/memory limits (decision D-3, still open).

## Verify
```bash
python manage.py check_database     # version, driver, collation, RCSI; exit 1 on failure
python manage.py migrate
python manage.py makemigrations --check --dry-run
```

## Migrations
The custom user model (`accounts.User`: email/phone identifiers, no username) exists **before** the first production migration. **Phase 3 replaced `accounts/0001_initial`** (Phase 2's version was username-based): any development database that already applied the old file must be dropped and recreated (`docker compose down -v`). Project migrations: `accounts/0001_initial`, `audit/0001_initial`; Django, admin, sessions and `django_celery_results` migrations come with their packages. The `django_celery_results` tables have not been run on SQL Server yet — treat as part of the first acceptance run.

## SQL Server-specific limitations (from the `mssql-django` docs; see TECHNICAL_DECISIONS §3)
* No DB-level `on_delete` (`DB_CASCADE`, ...) and the **multiple cascade path** rule (error 1785): use Django-level cascades, PROTECT-first; `manage.py check` reports `fields.E324`.
* `bulk_create(ignore_conflicts=True)` unsupported; no triggers (they break ID retrieval in `bulk_create`).
* `JSONField` is stored as `nvarchar`: no `contains` lookup, nested lookups differ — keep JSON for opaque payloads.
* `select_for_update(of=...)`, `Avg` on `DurationField`, bitwise aggregates, `ORDER BY` in subqueries are unsupported; limited timezone/timedelta support (store UTC).
* Adding and removing a constraint in the same migration can conflict — use separate migrations.
* Known bugs: pattern lookups with `F()` do not escape `[ ]` (#573); `JSONField key__iexact=None` (#574).
* `OFFSET/FETCH` degrades on deep pages: use keyset pagination for staff/feeds (not implemented yet).
* Fixes ship only in the newest `mssql-django` release: pin and upgrade deliberately.

## Nullable-unique columns
`User.email`, `User.phone` and `Address.is_default` (one default per user) use `UniqueConstraint(condition=...)`; email/phone are nullable and unique. They are declared as `UniqueConstraint(condition=... IS NOT NULL)`, which becomes a filtered unique index on SQL Server (a plain `UNIQUE` would allow only one NULL). `save()` converts `""` to `NULL`. Tests cover this on SQLite; the SQL Server behaviour is spike test T3.

## Tests
`TEST_DATABASE=sqlserver` (default) runs against SQL Server, which can create/drop `test_<DB_NAME>` (the login needs `CREATE DATABASE`). `TEST_DATABASE=sqlite` is a smoke mode only.

## Phase 3 items to verify on SQL Server (not testable in the build sandbox)
Run `pytest` with `TEST_DATABASE=sqlserver` and check specifically: (1) filtered unique indexes `uniq_user_email_not_null`, `uniq_user_phone_not_null`, `uniq_default_address_per_user`; (2) CHECK constraint `user_has_email_or_phone`; (3) `AuditLog.metadata` (`JSONField` → `nvarchar(max)`, write-only, never filtered); (4) `select_for_update()` row lock used by address mutations; (5) `django_session` and the `django_celery_results` tables; (6) the `post_migrate` role sync (creates 5 groups). All 232 backend tests passed on SQLite only.
