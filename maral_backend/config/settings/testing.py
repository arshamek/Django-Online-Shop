"""
Test settings.

By default tests run against SQL Server (the architecture requires DB-sensitive tests
on the real engine). ``TEST_DATABASE=sqlite`` is a *smoke mode only* for machines
without SQL Server; it does not validate anything SQL-Server-specific.
"""

from .base import *  # noqa: F401,F403
from .base import DATABASES, MIDDLEWARE, build_logging_config, env
from .base import REST_FRAMEWORK as _BASE_REST_FRAMEWORK

DEBUG = False
SECRET_KEY = "testing-only-secret-key-not-for-any-real-use-0123456789abcdef"
ALLOWED_HOSTS = ["testserver", "localhost"]

if env("TEST_DATABASE", default="sqlserver") == "sqlite":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False  # failures are recorded on the result like in a worker
CELERY_TASK_STORE_EAGER_RESULT = True

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
REST_FRAMEWORK = {**_BASE_REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": []}  # throttling tests opt in explicitly
LOGGING = build_logging_config(level="WARNING", json_logs=False)

# WhiteNoise is only needed where gunicorn serves collected static files (production).
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405
