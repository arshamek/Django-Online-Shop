"""Guard against drift: every env var read by settings must be documented in .env.example."""

import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _settings_vars() -> set[str]:
    names: set[str] = set()
    for path in (BASE / "config" / "settings").glob("*.py"):
        names |= set(re.findall(r"env(?:\.\w+)?\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']", path.read_text()))
    return names


def test_every_setting_env_var_is_in_env_example():
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", (BASE / ".env.example").read_text(), flags=re.M))
    missing = _settings_vars() - documented
    assert not missing, f"Add to .env.example: {sorted(missing)}"


def test_env_example_contains_no_real_secrets():
    text = (BASE / ".env.example").read_text()
    for name in (
        "SECRET_KEY",
        "DB_PASSWORD",
        "EMAIL_HOST_PASSWORD",
        "AI_API_KEY",
        "PAYMENT_API_KEY",
        "MSSQL_SA_PASSWORD",
    ):
        value = re.search(rf"^{name}=(.*)$", text, flags=re.M).group(1).strip()
        assert value in {"", "change-me"}, f"{name} must be empty or a placeholder"


def test_real_env_files_are_git_ignored():
    ignore = (BASE / ".gitignore").read_text().splitlines()
    assert ".env" in ignore and "!.env.example" in ignore
