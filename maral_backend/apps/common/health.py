"""Dependency checks shared by /ready/ and the check_* management commands."""

import time
from dataclasses import dataclass

import redis
from django.conf import settings
from django.db import connection


@dataclass
class CheckResult:
    status: str  # "ok" | "error"
    latency_ms: float
    error: str | None = None  # exception class name only - never connection strings/credentials

    def as_dict(self) -> dict:
        data = {"status": self.status, "latency_ms": self.latency_ms}
        if self.error:
            data["error"] = self.error
        return data


def _timed(fn) -> CheckResult:
    started = time.perf_counter()
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - a health probe must never raise
        return CheckResult("error", round((time.perf_counter() - started) * 1000, 2), type(exc).__name__)
    return CheckResult("ok", round((time.perf_counter() - started) * 1000, 2))


def check_database() -> CheckResult:
    def probe():
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    return _timed(probe)


def ping_redis(url: str) -> None:
    client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    finally:
        client.close()


def check_redis() -> CheckResult:
    return _timed(lambda: ping_redis(settings.REDIS_URL))


def check_broker() -> CheckResult | None:
    """Only separate from `redis` when the broker is configured on a different Redis URL."""
    url = settings.CELERY_BROKER_URL
    if not url.startswith(("redis://", "rediss://")) or url == settings.REDIS_URL:
        return None
    return _timed(lambda: ping_redis(url))


def run_readiness_checks() -> dict[str, CheckResult]:
    checks = {"database": check_database(), "redis": check_redis()}
    broker = check_broker()
    if broker is not None:
        checks["broker"] = broker
    return checks
