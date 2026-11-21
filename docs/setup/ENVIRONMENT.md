# Environment configuration

All configuration comes from environment variables (12-factor). `.env.example` is the template; a real `.env` is git-ignored (`maral_backend/.gitignore`) and must never be committed. A test (`tests/test_env_example.py`) fails if settings read a variable that `.env.example` does not document, or if the template contains a real-looking secret.

`.env` is read only when `DJANGO_READ_DOT_ENV_FILE=true` (local convenience). Real environment variables always win over `.env`. In Docker, compose injects the file.

## Settings modules
| Module | Used by | Behaviour |
|---|---|---|
| `base` | all | shared; never reads `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS` |
| `development` | `manage.py` default | insecure fallback key, localhost hosts, console email, plain static storage |
| `testing` | pytest | fixed key, in-memory cache/storage, Celery eager, throttling off |
| `production` | wsgi/asgi/Celery default | **fails to start** if `SECRET_KEY` missing/short/`django-insecure*`, `ALLOWED_HOSTS` missing or `*`, or `DEBUG=true`; HTTPS redirect, HSTS, secure cookies, docs off |

`python manage.py check --deploy` is clean for production settings (HSTS preload stays opt-in, so W021 is silenced unless `SECURE_HSTS_PRELOAD=true`).

## Variables
**Django**
| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | dev fallback / **required in prod** | ≥ 50 random chars |
| `DEBUG` | `true` dev / `false` prod | prod refuses `true` |
| `ALLOWED_HOSTS` | localhost set (dev) / **required in prod** | comma list; include the name your health probe uses |
| `DJANGO_READ_DOT_ENV_FILE` | `false` | read `.env` |
| `DJANGO_ADMIN_URL` | `admin/` | change in production |
| `LANGUAGE_CODE` | `fa` | supported: fa, en, ar, tr |
| `APP_VERSION`, `GIT_COMMIT` | `0.1.0`, `unknown` | shown by `/health/` |
| `LOG_LEVEL`, `LOG_JSON` | `INFO`, `true` (dev: `DEBUG`, `false`) | JSON logs in production |

**Accounts / browser auth**: `FRONTEND_BASE_URL` (links in emails), `COOKIE_SAMESITE` (`Lax`; `None` only for cross-site HTTPS deployments), `SESSION_COOKIE_DOMAIN`, `CSRF_COOKIE_DOMAIN` (optional), `TRUSTED_PROXY_COUNT` (reverse proxies in front of Django; `0` ignores `X-Forwarded-For` so client IPs, and therefore rate limits, cannot be spoofed — set it to the real hop count behind a load balancer or every user shares one throttle bucket), `DEFAULT_PHONE_REGION` (`IR`), `SMS_BACKEND` (dotted path; production default `DisabledSMSBackend`, development `ConsoleSMSBackend` prints OTP codes to the log), `THROTTLE_RATE_AUTH_LOGIN|REGISTER|PASSWORD_RESET|VERIFY`. Session/lockout/OTP limits are constants in `base.py`.

**Browser access**: `CORS_ALLOWED_ORIGINS` (empty in prod = none; dev defaults to common local frontend ports), `CSRF_TRUSTED_ORIGINS`. Credentials are allowed, so never use a wildcard origin.

**Database** (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_DRIVER`, `DB_ENCRYPT`, `DB_TRUST_SERVER_CERTIFICATE`, `DB_CONN_MAX_AGE`, `TEST_DATABASE`) — see [DATABASE.md](DATABASE.md). Defaults are secure: `Encrypt=yes`, `TrustServerCertificate=no`.

**Redis / Celery**: `REDIS_URL` (cache, throttles, readiness), `CACHE_REDIS_URL` (optional override), `CELERY_BROKER_URL` (default = `REDIS_URL`; compose uses DB 1), `BEAT_HEARTBEAT_SECONDS`.
The cache is configured to **fail open**: if Redis is down, cache and throttling degrade (and log) instead of turning every request into a 500; `/ready/` reports Redis as down. Be aware that rate limiting is effectively off while Redis is unavailable.

**API**: `API_DOCS_ENABLED` (prod default `false`), `THROTTLE_RATE_ANON` (120/min), `THROTTLE_RATE_USER` (300/min), `THROTTLE_RATE_CATALOG_SEARCH` (60/min per client on `/products/search`) — values from `API_CONVENTIONS.md` §9.

**Production transport**: `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`. TLS terminates at the reverse proxy, which must set `X-Forwarded-Proto` and strip client-supplied copies. `/health/` and `/ready/` are exempt from the redirect.

**Email**: `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`.

**Reserved for later phases** — read into `settings.EXTERNAL_APIS`, `AI_SETTINGS`, `PAYMENT_SETTINGS`, `STORAGE_SETTINGS`, but **no code uses them yet**; providers default to `fake`/`manual`/`console`/`filesystem`:
`TRENDYOL_API_KEY`, `NAMSHI_API_KEY` (official API or licensed feed only), `CURRENCY_RATE_PROVIDER`, `CURRENCY_RATE_API_KEY`, `SMS_API_KEY`, `AI_PROVIDER`, `AI_API_KEY`, `AI_MODEL`, `TRANSLATION_PROVIDER`, `TRANSLATION_API_KEY`, `PAYMENT_GATEWAY`, `PAYMENT_MERCHANT_ID`, `PAYMENT_API_KEY`, `PAYMENT_WEBHOOK_SECRET`, `STORAGE_BACKEND`, `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION_NAME`. Product images use the S3 backend when `STORAGE_BACKEND=s3` (`django-storages`, unsigned public URLs; set `S3_CUSTOM_DOMAIN` to serve them through a CDN).

**docker-compose only**: `MSSQL_SA_PASSWORD` (required), `SQLSERVER_PORT`, `REDIS_PORT`, `CELERY_CONCURRENCY`, `WEB_CONCURRENCY`.

## Secrets handling
* Production secrets come from a secret store / orchestrator secrets, not files in the image.
* Logs redact keys that look like credentials (`password`, `token`, `authorization`, `api_key`, `otp`, ...). Request bodies, headers and query strings are never logged.
* Readiness output exposes only exception class names, never connection strings.
* Celery stores task arguments in the result table (`CELERY_RESULT_EXTENDED`): pass ids, never PII or secrets, as task arguments.

**Catalogue**: `CATALOG_IMAGE_MAX_BYTES` (upload ceiling, default 5 MiB), `CATALOG_SHOW_MACHINE_TRANSLATIONS` (`true`: unreviewed machine translations are shown and flagged `is_machine_translated`; `false`: only human/approved text, with fallback to the next language). Query bounds (`CATALOG_MAX_*`, search length) are constants in `base.py`.
