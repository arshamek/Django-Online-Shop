"""Production guards added in Phase 8. Imports the settings module only (no database driver needed)."""

import subprocess
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
GOOD = {"SECRET_KEY": "k" * 60 + "!x9@Qz", "ALLOWED_HOSTS": "api.example.com", "PATH": "/usr/bin:/bin"}


def load(**extra):
    return subprocess.run(
        [sys.executable, "-c", "import config.settings.production as s; print('loaded')"],
        cwd=BASE, env={**GOOD, **extra, "DJANGO_SETTINGS_MODULE": "config.settings.production"}, capture_output=True, text=True,
    )  # fmt: skip


def test_production_loads_with_no_provider_configuration():
    assert "loaded" in load().stdout  # no real provider installed: nothing new is required


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"SHIPPING_ALLOW_MOCK_CARRIER": "true"}, "SHIPPING_ALLOW_MOCK_CARRIER"),
        ({"PAYMENTS_ALLOW_FAKE_PROVIDER": "true"}, "PAYMENTS_ALLOW_FAKE_PROVIDER"),
        ({"PAYMENT_PROVIDER_ADAPTERS": "pkg.Provider"}, "PAYMENTS_CALLBACK_BASE_URL"),
        (
            {"PAYMENT_PROVIDER_ADAPTERS": "pkg.Provider", "PAYMENTS_CALLBACK_BASE_URL": "http://api.example.com"},
            "PAYMENTS_CALLBACK_BASE_URL",
        ),
    ],
)
def test_unsafe_production_payment_settings_are_refused(env, message):
    result = load(**env)
    assert result.returncode != 0 and message in result.stderr


def test_a_real_adapter_with_an_https_callback_url_is_accepted():
    assert (
        "loaded"
        in load(PAYMENT_PROVIDER_ADAPTERS="pkg.Provider", PAYMENTS_CALLBACK_BASE_URL="https://api.example.com").stdout
    )
