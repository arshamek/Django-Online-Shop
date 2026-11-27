"""
Base settings shared by every environment.

Rules
-----
* Every secret / environment-specific value comes from environment variables.
* This module never reads SECRET_KEY, DEBUG or ALLOWED_HOSTS: each environment
  module decides how those are sourced (production refuses insecure values).
* A ``.env`` file is read only when ``DJANGO_READ_DOT_ENV_FILE=true`` (local
  convenience). Containers/CI inject real environment variables instead.
"""

from pathlib import Path

import environ

from apps.common.logging_config import build_logging_config, configure_structlog

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # .../maral_backend

env = environ.Env()
if env.bool("DJANGO_READ_DOT_ENV_FILE", default=False):
    env.read_env(BASE_DIR / ".env")

APP_VERSION = env("APP_VERSION", default="0.1.0")
GIT_COMMIT = env("GIT_COMMIT", default="unknown")

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "django_celery_results",
]
LOCAL_APPS = [
    "apps.common",
    "apps.audit",
    "apps.accounts",
    "apps.categories",
    "apps.brands",
    "apps.product_sources",
    "apps.catalog",
    "apps.reviews",
    "apps.imports",
    "apps.currencies",
    "apps.translations",
    "apps.pricing",
    # Phase 7
    "apps.inventory",
    "apps.shipping",
    "apps.cart",
    "apps.wishlist",
    "apps.orders",
    "apps.payments",
    "apps.ai_assistant",  # Phase 9
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.IdentifierBackend"]
# email is USERNAME_FIELD but only conditionally unique (filtered index, NULLs allowed) - intentional.
SILENCED_SYSTEM_CHECKS = ["auth.W004"]

MIDDLEWARE = [
    "apps.common.middleware.RequestIDMiddleware",  # outermost: correlation id for everything below
    "apps.common.middleware.RequestLoggingMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",  # before anything that can generate responses
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# --------------------------------------------------------------------------
# Database - Microsoft SQL Server through mssql-django (pyodbc + ODBC Driver 18)
# --------------------------------------------------------------------------
def _sqlserver_database() -> dict:
    extra_params = ";".join(
        [
            f"Encrypt={env('DB_ENCRYPT', default='yes')}",
            f"TrustServerCertificate={env('DB_TRUST_SERVER_CERTIFICATE', default='no')}",
        ]
    )
    return {
        "ENGINE": "mssql",
        "NAME": env("DB_NAME", default="maral"),
        "USER": env("DB_USER", default=""),
        "PASSWORD": env("DB_PASSWORD", default=""),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="1433"),
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "driver": env("DB_DRIVER", default="ODBC Driver 18 for SQL Server"),
            "extra_params": extra_params,
        },
    }


DATABASES = {"default": _sqlserver_database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Redis / cache
# --------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("CACHE_REDIS_URL", default=REDIS_URL),
        "KEY_PREFIX": "maral",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_CONNECT_TIMEOUT": 2,
            "SOCKET_TIMEOUT": 2,
            # Fail open: if Redis is down the cache (and therefore throttling)
            # degrades instead of turning every request into a 500. The failure is
            # logged, and /ready/ reports Redis as down. See docs/setup/ENVIRONMENT.md.
            "IGNORE_EXCEPTIONS": True,
        },
    }
}
DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True

# --------------------------------------------------------------------------
# Auth / passwords (final end-user auth method is open decision D-6)
# --------------------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",  # new hashes; older hashers still verify and re-hash on login
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
PASSWORD_RESET_TIMEOUT = 60 * 60  # 1 hour (Django's default is 3 days)
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        "OPTIONS": {"user_attributes": ("email", "phone", "first_name", "last_name")},
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# i18n / time
# --------------------------------------------------------------------------
LANGUAGE_CODE = env("LANGUAGE_CODE", default="fa")
LANGUAGES = [("fa", "Persian"), ("en", "English"), ("ar", "Arabic"), ("tr", "Turkish")]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static files (admin + Swagger UI assets). Media/object storage arrives with the catalogue phase.
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --------------------------------------------------------------------------
# Django REST Framework
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "VERSION_PARAM": "version",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.ManagedSessionAuthentication"],
    # Secure default: endpoints are private unless they explicitly opt in with AllowAny.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "NUM_PROXIES": env.int("TRUSTED_PROXY_COUNT", default=0),  # 0 = ignore X-Forwarded-For (anti-spoofing)
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_RATE_ANON", default="120/min"),
        "user": env("THROTTLE_RATE_USER", default="300/min"),
        # Auth endpoints (per client IP; per-account lockout is separate, see services/lockout.py)
        "auth_login": env("THROTTLE_RATE_AUTH_LOGIN", default="60/hour"),
        "auth_register": env("THROTTLE_RATE_AUTH_REGISTER", default="10/hour"),
        "auth_password_reset": env("THROTTLE_RATE_AUTH_PASSWORD_RESET", default="10/hour"),
        "auth_verify": env("THROTTLE_RATE_AUTH_VERIFY", default="20/hour"),
        "otp_ip": "10/hour",  # conventions: OTP request 10/hour per IP (+ 3 per 10 min per phone, in the service)
        "avatar_upload": "20/hour",
        "catalog_search": env("THROTTLE_RATE_CATALOG_SEARCH", default="60/min"),
        "price_quote": env("THROTTLE_RATE_PRICE_QUOTE", default="60/hour"),
        "price_calculate": env("THROTTLE_RATE_PRICE_CALCULATE", default="120/min"),
        # Phase 7
        "cart": env("THROTTLE_RATE_CART", default="120/min"),
        "cart_create": env("THROTTLE_RATE_CART_CREATE", default="30/hour"),  # anonymous carts are the abuse surface
        "checkout": env("THROTTLE_RATE_CHECKOUT", default="30/min"),
        "order_create": env("THROTTLE_RATE_ORDER_CREATE", default="20/hour"),
        "payment_webhook": env("THROTTLE_RATE_PAYMENT_WEBHOOK", default="600/min"),
        # Phase 9
        "ai_chat": env("THROTTLE_RATE_AI_CHAT", default="20/min"),
        "ai_read": env("THROTTLE_RATE_AI_READ", default="120/min"),
        "ai_inquiry": env("THROTTLE_RATE_AI_INQUIRY", default="10/hour"),
    },
    "DEFAULT_PAGINATION_CLASS": "apps.common.api.pagination.StandardPagination",
    "PAGE_SIZE": 24,
    "DEFAULT_FILTER_BACKENDS": [
        "apps.common.api.filters.StrictDjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "SEARCH_PARAM": "q",
    "ORDERING_PARAM": "sort",
    "EXCEPTION_HANDLER": "apps.common.api.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "MaralOnlineShop API",
    "DESCRIPTION": (
        "Backend API for MaralOnlineShop. Versioned under `/api/v1/`. "
        'Errors always use the `{"error": {...}}` envelope; collections use '
        "`{count, page, page_size, next, previous, results}`."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]",
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "POSTPROCESSING_HOOKS": [
        "apps.common.api.schema.normalise_path_slashes",
        "apps.common.api.schema.add_error_envelope",
    ],
}
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=True)

TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)

# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", default="http://localhost:5173").rstrip("/")
DEFAULT_PHONE_REGION = env("DEFAULT_PHONE_REGION", default="IR")
SMS_BACKEND = env("SMS_BACKEND", default="apps.accounts.delivery.DisabledSMSBackend")
EMAIL_VERIFICATION_MAX_AGE = 60 * 60 * 24  # signed link lifetime
PHONE_OTP_TTL = 300
PHONE_OTP_MAX_ATTEMPTS = 5
PHONE_OTP_MAX_REQUESTS = 3  # per phone ...
PHONE_OTP_REQUEST_WINDOW = 600  # ... per 10 minutes
LOGIN_LOCKOUT_THRESHOLD = 5  # consecutive failures before the first lock
LOGIN_LOCKOUT_BASE_SECONDS = 60  # doubles with each further failure ...
LOGIN_LOCKOUT_MAX_SECONDS = 3600  # ... capped at one hour
MAX_ADDRESSES_PER_USER = 20
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_MAX_PIXELS = 4096 * 4096
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --------------------------------------------------------------------------
# Catalogue (Phase 4)
# --------------------------------------------------------------------------
CATALOG_IMAGE_MAX_BYTES = env.int("CATALOG_IMAGE_MAX_BYTES", default=5 * 1024 * 1024)
CATALOG_IMAGE_MAX_PIXELS = 36_000_000
CATALOG_MAX_DEEP_PAGE_ITEMS = 10_000  # page * page_size ceiling: OFFSET paging degrades on SQL Server
CATALOG_MAX_FILTER_VALUES = 20
CATALOG_MAX_IDS = 50
CATALOG_SEARCH_MIN_CHARS = 2
CATALOG_SEARCH_MAX_CHARS = 100
CATALOG_SEARCH_MAX_TOKENS = 6
CATALOG_SHOW_MACHINE_TRANSLATIONS = env.bool("CATALOG_SHOW_MACHINE_TRANSLATIONS", default=True)

# --------------------------------------------------------------------------
# CORS (browser -> API on a different origin; allow-list only)
# --------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-language",
    "accept-currency",
    "authorization",
    "content-type",
    "idempotency-key",
    "if-none-match",
    "x-csrftoken",
    "x-guest-token",
    "x-request-id",
]
CORS_EXPOSE_HEADERS = ["x-request-id", "retry-after", "etag", "location"]
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# --------------------------------------------------------------------------
# Secure defaults (production tightens further)
# --------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# Cookie-based browser auth: HttpOnly session cookie + CSRF header (token delivered as JSON by GET /auth/csrf).
# SameSite=Lax needs the frontend and API on the same site (e.g. shop.example.com + api.example.com).
# For a different registrable domain use COOKIE_SAMESITE=None (requires HTTPS) - see docs/api/ACCOUNTS_API.md.
SESSION_COOKIE_NAME = "maral_sid"
CSRF_COOKIE_NAME = "maral_csrf"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = env("COOKIE_SAMESITE", default="Lax")
CSRF_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
SESSION_COOKIE_DOMAIN = env("SESSION_COOKIE_DOMAIN", default=None)
CSRF_COOKIE_DOMAIN = env("CSRF_COOKIE_DOMAIN", default=None)
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # idle lifetime, extended by POST /auth/refresh
SESSION_ABSOLUTE_AGE = 60 * 60 * 24 * 30  # hard cap: re-login required after 30 days
SESSION_ENGINE = "django.contrib.sessions.backends.db"
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")

# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="MaralOnlineShop <no-reply@example.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --------------------------------------------------------------------------
# Credentials RESERVED for later phases. They are read here so configuration is
# complete and documented, but NO code consumes them yet and every provider
# defaults to "fake". Nothing below means an integration exists.
# --------------------------------------------------------------------------
EXTERNAL_APIS = {
    "trendyol_api_key": env("TRENDYOL_API_KEY", default=""),  # only an official API / licensed feed
    "namshi_api_key": env("NAMSHI_API_KEY", default=""),
    "currency_rate_provider": env("CURRENCY_RATE_PROVIDER", default="manual"),
    "currency_rate_api_key": env("CURRENCY_RATE_API_KEY", default=""),
    "sms_api_key": env("SMS_API_KEY", default=""),
}
# --------------------------------------------------------------------------
# Product import engine (Phase 5). docs/imports/PHASE5_IMPORT_ENGINE.md
# --------------------------------------------------------------------------
# "fixture" replays bundled synthetic data and never touches the network. "live" additionally needs the source
# row to record its authorisation (ProductSource.integration_type + terms_note) and credentials below.
IMPORTS_PROVIDER_MODE = env("IMPORTS_PROVIDER_MODE", default="fixture")
# Fixture data must never reach the storefront in production.
IMPORTS_ALLOW_FIXTURE_PUBLISH = env.bool("IMPORTS_ALLOW_FIXTURE_PUBLISH", default=False)
IMPORTS_AUTO_PUBLISH = env.bool("IMPORTS_AUTO_PUBLISH", default=False)  # off: staff publish READY_TO_PUBLISH items
IMPORTS_CREDENTIALS = {  # secrets come from the environment only
    "trendyol": {
        "seller_id": env("TRENDYOL_SELLER_ID", default=""),
        "api_key": env("TRENDYOL_API_KEY", default=""),
        "api_secret": env("TRENDYOL_API_SECRET", default=""),
        "integrator": env("TRENDYOL_INTEGRATOR_NAME", default=""),
        "stage": env.bool("TRENDYOL_STAGE", default=True),
    },
    "namshi": {"feed_path": env("NAMSHI_FEED_PATH", default="")},
}
IMPORTS_SUPPORTED_CURRENCIES = ("TRY", "AED", "USD", "EUR")
IMPORTS_TRANSLATION_TARGETS = ("fa", "en", "ar", "tr")
IMPORTS_REQUIRED_LANGUAGES = ("fa",)  # reviewed translations needed before publication
IMPORTS_REQUIRE_IMAGE = True
IMPORTS_TRANSLATION_HANDLER = "apps.translations.handlers.import_translation_handler"  # `fn(product_id, targets)`
IMPORTS_PRICING_HANDLER = "apps.pricing.handlers.import_pricing_handler"  # `fn(product_id, variant_ids)`
IMPORTS_UNMATCHED_POLICY = "create_draft"  # or "review"
IMPORTS_MATCH_REVIEW_THRESHOLD = 0.55
IMPORTS_MATCH_WEAK_THRESHOLD = 0.35
IMPORTS_PAGE_SIZE = 50
IMPORTS_SLICE_SECONDS = 120  # work per task run; must stay under CELERY_TASK_SOFT_TIME_LIMIT
IMPORTS_MAX_ATTEMPTS = 5
IMPORTS_STALE_JOB_MINUTES = 15
IMPORTS_LOG_RETENTION_DAYS = 90
IMPORTS_SCHEDULES = {}  # e.g. {"trendyol": {"job_type": "sync", "every_minutes": 360}}; empty = nothing scheduled

# --------------------------------------------------------------------------
# Currencies, translation, pricing (Phase 6). docs/pricing/PHASE6_*.md
# --------------------------------------------------------------------------
# The "fixture" rate provider replays configured numbers and is never a real market source.
CURRENCY_ALLOW_FIXTURE_PROVIDER = env.bool("CURRENCY_ALLOW_FIXTURE_PROVIDER", default=False)
# Hosts an `http_json` rate source may call. Empty = no outbound rate requests at all.
CURRENCY_HTTP_ALLOWED_HOSTS = env.list("CURRENCY_HTTP_ALLOWED_HOSTS", default=[])
CURRENCY_FUTURE_TOLERANCE_SECONDS = 120  # a provider timestamp further ahead than this is rejected
# Secrets for rate sources: env var CURRENCY_CRED_<CREDENTIAL_REF> (never stored in the database).
TRANSLATION_PROVIDER = env("TRANSLATION_PROVIDER", default="fake")  # fake | llm
TRANSLATION_LLM_BASE_URL = env("TRANSLATION_LLM_BASE_URL", default="https://api.anthropic.com")
TRANSLATION_PROMPT_VERSION = "v1"
TRANSLATION_MAX_ATTEMPTS = 5
TRANSLATION_STALE_JOB_MINUTES = 15
TRANSLATION_MAX_CHARS = 8000
TRANSLATION_ALLOW_FAKE_PROVIDER = env.bool("TRANSLATION_ALLOW_FAKE_PROVIDER", default=False)

AI_SETTINGS = {
    "provider": env("AI_PROVIDER", default="fake"),
    "api_key": env("AI_API_KEY", default=""),
    "model": env("AI_MODEL", default=""),
    "translation_provider": env("TRANSLATION_PROVIDER", default="fake"),
    "translation_api_key": env("TRANSLATION_API_KEY", default=""),
    # Phase 9 shopping assistant (server-side only; the key is never stored in the database or returned by an API)
    "base_url": env("AI_BASE_URL", default="https://api.anthropic.com"),
}
PAYMENT_SETTINGS = {
    "gateway": env("PAYMENT_GATEWAY", default="fake"),
    "merchant_id": env("PAYMENT_MERCHANT_ID", default=""),
    "api_key": env("PAYMENT_API_KEY", default=""),
    "webhook_secret": env("PAYMENT_WEBHOOK_SECRET", default=""),
}
STORAGE_SETTINGS = {
    "custom_domain": env("S3_CUSTOM_DOMAIN", default=""),  # CDN host serving the public bucket
    "backend": env("STORAGE_BACKEND", default="filesystem"),
    "endpoint_url": env("S3_ENDPOINT_URL", default=""),
    "bucket_name": env("S3_BUCKET_NAME", default=""),
    "access_key_id": env("S3_ACCESS_KEY_ID", default=""),
    "secret_access_key": env("S3_SECRET_ACCESS_KEY", default=""),
    "region_name": env("S3_REGION_NAME", default=""),
}

if STORAGE_SETTINGS["backend"] == "s3":
    # Object storage for product images. Catalogue images are public assets: unsigned URLs (stable, cacheable).
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": STORAGE_SETTINGS["bucket_name"],
            "endpoint_url": STORAGE_SETTINGS["endpoint_url"] or None,
            "access_key": STORAGE_SETTINGS["access_key_id"],
            "secret_key": STORAGE_SETTINGS["secret_access_key"],
            "region_name": STORAGE_SETTINGS["region_name"] or None,
            "custom_domain": STORAGE_SETTINGS["custom_domain"] or None,
            "querystring_auth": False,
            "default_acl": None,
            "file_overwrite": False,
        },
    }

# --------------------------------------------------------------------------
# Celery (broker: Redis, results + failed-task tracking: database)
# --------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = "django-db"
CELERY_RESULT_EXTENDED = True  # store task name, args, worker, queue -> useful for failed-task review
CELERY_RESULT_EXPIRES = 60 * 60 * 24 * 30  # 30 days; Celery's built-in backend_cleanup beat entry prunes
CELERY_CACHE_BACKEND = "default"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 270
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# With acks_late on Redis the visibility timeout must exceed the longest task runtime.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}
CELERY_WORKER_HIJACK_ROOT_LOGGER = False  # keep our structured logging config in workers
CELERY_WORKER_SEND_TASK_EVENTS = True  # lets Flower / `celery events` monitor workers
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "imports.request_translation": {"queue": "translations"},
    "imports.request_pricing": {"queue": "pricing"},
    "imports.*": {"queue": "imports"},
    "translations.*": {"queue": "translations"},
    "currencies.*": {"queue": "pricing"},
    "pricing.*": {"queue": "pricing"},
}
# Per-domain queues from the approved architecture. Only `default` carries tasks in Phase 2.
CELERY_QUEUE_NAMES = ["default", "imports", "translations", "pricing", "notifications"]
BEAT_HEARTBEAT_SECONDS = env.int("BEAT_HEARTBEAT_SECONDS", default=300)
CELERY_BEAT_SCHEDULE = {
    # Static in-code schedule (no DatabaseScheduler -> no extra tables). Proves beat is alive.
    "common-heartbeat": {
        "task": "common.heartbeat",
        "schedule": float(BEAT_HEARTBEAT_SECONDS),
        "options": {"queue": "default", "expires": BEAT_HEARTBEAT_SECONDS},
    },
    "accounts-cleanup": {
        "task": "accounts.cleanup_expired",
        "schedule": 60 * 60 * 24.0,
        "options": {"queue": "default"},
    },
    "payments-reconcile": {
        "task": "payments.reconcile",
        "schedule": env.int("PAYMENTS_RECONCILE_INTERVAL_MINUTES", default=15) * 60.0,
        "options": {"queue": "default"},
    },
    "shipping-sync-tracking": {
        "task": "shipping.sync_tracking",
        "schedule": env.int("SHIPPING_TRACKING_SYNC_MINUTES", default=30) * 60.0,
        "options": {"queue": "default"},
    },
    "imports-run-scheduled": {
        "task": "imports.run_scheduled",
        "schedule": 15 * 60.0,
        "options": {"queue": "imports"},
    },
    "imports-recover-stale": {
        "task": "imports.recover_stale",
        "schedule": 5 * 60.0,
        "options": {"queue": "imports"},
    },
    "imports-refresh-readiness": {
        "task": "imports.refresh_readiness",
        "schedule": 10 * 60.0,
        "options": {"queue": "imports"},
    },
    "imports-purge-logs": {
        "task": "imports.purge_logs",
        "schedule": 60 * 60 * 24.0,
        "options": {"queue": "imports"},
    },
    # Phase 9: delete AI conversations past the configured retention window
    "ai-purge-conversations": {
        "task": "ai_assistant.purge_expired_conversations",
        "schedule": 60 * 60 * 24.0,
        "options": {"queue": "default"},
    },
    # Phase 6
    "currencies-refresh-due": {
        "task": "currencies.refresh_due",
        "schedule": 60.0,  # each source has its own poll interval; this only checks which are due
        "options": {"queue": "pricing"},
    },
    "pricing-expire-quotes": {
        "task": "pricing.expire_quotes",
        "schedule": 5 * 60.0,
        "options": {"queue": "pricing"},
    },
    "translations-recover": {
        "task": "translations.recover",
        "schedule": 5 * 60.0,
        "options": {"queue": "translations"},
    },
    # Phase 7
    "orders-expire-unpaid": {
        "task": "orders.expire_unpaid",
        "schedule": 60.0,
        "options": {"queue": "default"},
    },
    "orders-expire-checkouts": {
        "task": "orders.expire_checkouts",
        "schedule": 5 * 60.0,
        "options": {"queue": "default"},
    },
    "inventory-expire-reservations": {
        "task": "inventory.expire_reservations",
        "schedule": 5 * 60.0,
        "options": {"queue": "default"},
    },
    "cart-purge-stale": {
        "task": "cart.purge_stale",
        "schedule": 60 * 60 * 24.0,
        "options": {"queue": "default"},
    },
}

# --------------------------------------------------------------------------
# Phase 7 - cart, checkout, orders, payments (docs/orders/PHASE7_OVERVIEW.md). Every number below is a
# configurable default, not a confirmed business rule; the ones that need an owner decision are listed in
# docs/orders/PHASE7_DECISIONS.md.
# --------------------------------------------------------------------------
CART_MAX_LINES = env.int("CART_MAX_LINES", default=50)  # must stay <= pricing quote MAX_ITEMS
CART_MAX_QUANTITY_PER_LINE = env.int("CART_MAX_QUANTITY_PER_LINE", default=99)  # must stay <= pricing MAX_QTY
CART_GUEST_TTL_DAYS = env.int("CART_GUEST_TTL_DAYS", default=30)
WISHLIST_MAX_ITEMS = env.int("WISHLIST_MAX_ITEMS", default=200)
ORDER_PAYMENT_WINDOW_MINUTES = env.int("ORDER_PAYMENT_WINDOW_MINUTES", default=30)  # also the stock-hold time
ORDER_RETURN_WINDOW_DAYS = env.int("ORDER_RETURN_WINDOW_DAYS", default=14)  # 0 disables customer return refunds
ORDER_NON_RETURNABLE_CATEGORY_SLUGS = env.list("ORDER_NON_RETURNABLE_CATEGORY_SLUGS", default=[])
# Proxy-purchase orders: the customer may not cancel once staff started buying (PURCHASE_PENDING and later).
PAYMENTS_ALLOW_FAKE_PROVIDER = env.bool("PAYMENTS_ALLOW_FAKE_PROVIDER", default=False)  # never true in production
PAYMENTS_DEFAULT_PROVIDER = env("PAYMENTS_DEFAULT_PROVIDER", default="")
PAYMENTS_FAKE_WEBHOOK_SECRET = env("PAYMENTS_FAKE_WEBHOOK_SECRET", default="")

# --- Phase 8: payment providers and shipping --------------------------------------------------------------------
# Real provider adapters are plain Python classes listed by dotted path; none ships enabled. A listed adapter is still
# unusable until an administrator creates its PaymentProviderConfig AND records verified merchant eligibility.
PAYMENT_PROVIDER_ADAPTERS = env.list("PAYMENT_PROVIDER_ADAPTERS", default=[])
# Provider credentials: server-side only, from the environment, never in the database or an API response.
# JSON object keyed by adapter key, e.g. {"my_gateway": {"merchant_id": "...", "api_key": "...", "webhook_secret": "..."}}
PAYMENT_PROVIDER_CREDENTIALS = env.json("PAYMENT_PROVIDER_CREDENTIALS", default={})
# Public URL of THIS backend as the provider / customer's browser reaches it (https in production).
PAYMENTS_CALLBACK_BASE_URL = env("PAYMENTS_CALLBACK_BASE_URL", default="http://localhost:8000").rstrip("/")
PAYMENTS_RESULT_PATH = env(
    "PAYMENTS_RESULT_PATH", default="/checkout/payment-result"
)  # storefront page after a provider return
PAYMENTS_RECONCILE_INTERVAL_MINUTES = env.int("PAYMENTS_RECONCILE_INTERVAL_MINUTES", default=15)
SHIPPING_PROVIDER_ADAPTERS = env.list("SHIPPING_PROVIDER_ADAPTERS", default=[])  # real carrier adapters (dotted paths)
SHIPPING_ALLOW_MOCK_CARRIER = env.bool("SHIPPING_ALLOW_MOCK_CARRIER", default=False)  # never true in production
SHIPPING_TRACKING_SYNC_MINUTES = env.int("SHIPPING_TRACKING_SYNC_MINUTES", default=30)

# --------------------------------------------------------------------------
# Logging (structured JSON; request/correlation ids injected via contextvars)
# --------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOG_JSON = env.bool("LOG_JSON", default=True)
configure_structlog()
LOGGING = build_logging_config(level=LOG_LEVEL, json_logs=LOG_JSON)
