"""Local development. Convenience defaults; never use in production."""

from .base import *  # noqa: F401,F403
from .base import MIDDLEWARE, build_logging_config, env

DEBUG = env.bool("DEBUG", default=True)
SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-only-change-me-0123456789abcdef0123456789abcdef")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "[::1]", "0.0.0.0"])

# Local frontend origins (Vite/TanStack dev server). Override with CORS_ALLOWED_ORIGINS.
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://localhost:8080"],
)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=CORS_ALLOWED_ORIGINS)

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
SMS_BACKEND = env("SMS_BACKEND", default="apps.accounts.delivery.ConsoleSMSBackend")
STORAGES = {  # noqa: F405 - plain static storage, no manifest, so runserver works without collectstatic
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
LOGGING = build_logging_config(level=env("LOG_LEVEL", default="DEBUG"), json_logs=env.bool("LOG_JSON", default=False))

# WhiteNoise is only needed where gunicorn serves collected static files (production).
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405

IMPORTS_ALLOW_FIXTURE_PUBLISH = env.bool("IMPORTS_ALLOW_FIXTURE_PUBLISH", default=True)
CURRENCY_ALLOW_FIXTURE_PROVIDER = env.bool("CURRENCY_ALLOW_FIXTURE_PROVIDER", default=True)  # noqa: F405
TRANSLATION_ALLOW_FAKE_PROVIDER = env.bool("TRANSLATION_ALLOW_FAKE_PROVIDER", default=True)  # noqa: F405
