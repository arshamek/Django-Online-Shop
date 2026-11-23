from datetime import timedelta

from django.utils import timezone

from ..models import ExchangeRateSource, Health


def source_health(source: ExchangeRateSource, now=None) -> str:
    now = now or timezone.now()
    if not source.is_enabled:
        return Health.DISABLED
    if not source.is_polled:
        return Health.HEALTHY if source.rates.exists() else Health.UNKNOWN
    if source.last_attempt_at is None:
        return Health.UNKNOWN
    if source.consecutive_failures >= 3:
        return Health.FAILING
    overdue = source.last_success_at is None or now - source.last_success_at > timedelta(
        seconds=2 * source.poll_interval_seconds
    )
    if source.consecutive_failures >= 1 or overdue:
        return Health.DEGRADED
    return Health.HEALTHY
