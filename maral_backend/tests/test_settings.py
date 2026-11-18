"""Settings architecture: production must refuse insecure configuration."""

import subprocess
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
GOOD_ENV = {
    "SECRET_KEY": "k" * 60 + "!x9@Qz",
    "ALLOWED_HOSTS": "api.example.com",
    "PATH": "/usr/bin:/bin",
}


def _run(env, code="import django; django.setup(); print('loaded')", settings="config.settings.production"):
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BASE,
        env={**env, "DJANGO_SETTINGS_MODULE": settings},
        capture_output=True,
        text=True,
    )


def test_production_loads_with_required_env():
    result = _run(GOOD_ENV)
    assert result.returncode == 0, result.stderr
    assert "loaded" in result.stdout


@pytest.mark.parametrize(
    "override, message",
    [
        ({"SECRET_KEY": None}, "SECRET_KEY"),
        ({"ALLOWED_HOSTS": None}, "ALLOWED_HOSTS"),
        ({"ALLOWED_HOSTS": "*"}, "ALLOWED_HOSTS"),
        ({"DEBUG": "true"}, "DEBUG"),
        ({"SECRET_KEY": "short"}, "SECRET_KEY"),
        ({"SECRET_KEY": "django-insecure-" + "a" * 60}, "SECRET_KEY"),
    ],
)
def test_production_rejects_insecure_config(override, message):
    env = {**GOOD_ENV, **override}
    env = {k: v for k, v in env.items() if v is not None}
    result = _run(env)
    assert result.returncode != 0
    assert message in result.stderr


def test_production_deploy_check_is_clean():
    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        cwd=BASE,
        env={**GOOD_ENV, "DJANGO_SETTINGS_MODULE": "config.settings.production"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_database_settings_come_from_environment():
    code = (
        "import django; django.setup(); from django.conf import settings as s; d=s.DATABASES['default'];"
        "print(d['ENGINE'], d['HOST'], d['PORT'], d['NAME'], d['USER'], "
        "d['OPTIONS']['driver'], d['OPTIONS']['extra_params'])"
    )
    env = {
        **GOOD_ENV,
        "DB_HOST": "db.internal",
        "DB_PORT": "1444",
        "DB_NAME": "shop",
        "DB_USER": "svc",
        "DB_PASSWORD": "pw",
    }
    result = _run(env, code)
    assert (
        result.stdout.strip()
        == "mssql db.internal 1444 shop svc ODBC Driver 18 for SQL Server Encrypt=yes;TrustServerCertificate=no"
    )


def test_production_docs_off_by_default():
    code = "import django; django.setup(); from django.conf import settings as s; print(s.API_DOCS_ENABLED)"
    assert _run(GOOD_ENV, code).stdout.strip() == "False"
