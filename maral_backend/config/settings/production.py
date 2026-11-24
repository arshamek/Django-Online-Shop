"""
Production. Fails fast on missing/insecure configuration instead of guessing.
Run ``python manage.py check --deploy`` in CI against this module.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env

SECRET_KEY = env("SECRET_KEY")  # required - no default
DEBUG = env.bool("DEBUG", default=False)
if DEBUG:
    raise ImproperlyConfigured("DEBUG must be false in production.")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")  # required - no default
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be an explicit list of hostnames (no '*').")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure"):
    raise ImproperlyConfigured("SECRET_KEY must be a random value of at least 50 characters.")

# HTTPS everywhere. TLS terminates at the reverse proxy, which must set X-Forwarded-Proto
# and strip any client-supplied copy of that header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_REDIRECT_EXEMPT = [r"^health/$", r"^ready/$"]  # orchestrator probes use plain HTTP inside the network
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_NAME = "__Secure-maral_sid"  # __Secure- prefix: browsers reject it unless Secure is set
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_NAME = "__Secure-maral_csrf"
if SESSION_COOKIE_SAMESITE.lower() == "none" and not SESSION_COOKIE_SECURE:
    raise ImproperlyConfigured("COOKIE_SAMESITE=None requires secure cookies.")

API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=False)  # opt in explicitly
# HSTS preload is a near-irreversible commitment (browser preload lists), so it stays opt-in.
if not SECURE_HSTS_PRELOAD:
    SILENCED_SYSTEM_CHECKS = [*SILENCED_SYSTEM_CHECKS, "security.W021"]

# Phase 7: the fake payment provider moves no money; refuse to boot with it enabled.
if PAYMENTS_ALLOW_FAKE_PROVIDER:  # noqa: F405
    raise ImproperlyConfigured("PAYMENTS_ALLOW_FAKE_PROVIDER must be false in production.")
