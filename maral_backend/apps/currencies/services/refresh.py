"""Poll a source, validate every observation (timestamp, value, jump), record history, update health."""

import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..errors import ProviderError
from ..models import Currency, ExchangeRate, ExchangeRateSource, RateFetchLog, RateStatus
from ..providers.registry import get_provider
from ..signals import rates_changed

logger = structlog.get_logger("maral.currencies")
MAX_DETAILS = 25


def validate_observation(source, obs, now, known: set[str], last: ExchangeRate | None):
    """Returns (status, reason). status is 'accepted' | 'quarantined' | 'rejected'."""
    if obs.base not in known or obs.quote not in known or obs.base == obs.quote:
        return RateStatus.REJECTED, "unknown_or_identical_currency"
    try:
        rate = Decimal(obs.rate)
    except (InvalidOperation, TypeError):
        return RateStatus.REJECTED, "rate_not_a_number"
    if not rate.is_finite() or rate <= 0:
        return RateStatus.REJECTED, "rate_not_positive"
    if rate >= Decimal(10) ** 14:
        return RateStatus.REJECTED, "rate_out_of_range"
    if obs.as_of is None:
        return RateStatus.REJECTED, "timestamp_missing"
    if timezone.is_naive(obs.as_of):
        return RateStatus.REJECTED, "timestamp_naive"
    if obs.as_of > now + timedelta(seconds=settings.CURRENCY_FUTURE_TOLERANCE_SECONDS):
        return RateStatus.REJECTED, "timestamp_in_future"
    if obs.as_of < now - timedelta(seconds=source.max_observation_age_seconds):
        return RateStatus.REJECTED, "observation_too_old"
    if last is not None and obs.as_of < last.rate_at:
        return RateStatus.REJECTED, "older_than_recorded"
    if source.max_jump_percent is not None and last is not None and last.rate > 0:
        jump = abs(rate - last.rate) / last.rate * Decimal(100)
        if jump > source.max_jump_percent:
            return RateStatus.QUARANTINED, f"jump_{jump.quantize(Decimal('0.01'))}pct"
    return RateStatus.ACCEPTED, ""


def refresh_source(source: ExchangeRateSource, *, now=None) -> RateFetchLog:
    now = now or timezone.now()
    log = RateFetchLog.objects.create(source=source, started_at=now)
    started = time.monotonic()
    details: list[dict] = []
    try:
        observations = get_provider(source).fetch(source)
    except ProviderError as exc:
        return _fail(source, log, exc.code, exc.message, started)
    except Exception as exc:  # noqa: BLE001 - an adapter bug must not stop the scheduler
        logger.error("rate_provider_crashed", source=source.code, exc_type=type(exc).__name__)
        return _fail(source, log, "adapter_error", type(exc).__name__, started)

    known = set(Currency.objects.filter(is_active=True).values_list("code", flat=True))
    accepted_currencies: set[str] = set()
    counts = {RateStatus.ACCEPTED: 0, RateStatus.QUARANTINED: 0, RateStatus.REJECTED: 0}
    with transaction.atomic():
        for obs in observations:
            last = (
                ExchangeRate.objects.filter(
                    source=source, base_id=obs.base, quote_id=obs.quote, status=RateStatus.ACCEPTED
                )
                .order_by("-rate_at", "-id")
                .first()
            )
            status, reason = validate_observation(source, obs, now, known, last)
            counts[status] += 1
            if status == RateStatus.REJECTED:
                if len(details) < MAX_DETAILS:
                    details.append({"pair": f"{obs.base}/{obs.quote}", "status": status, "reason": reason})
                continue
            ExchangeRate.objects.create(
                source=source,
                base_id=obs.base,
                quote_id=obs.quote,
                rate=Decimal(obs.rate),
                rate_at=obs.as_of,
                fetched_at=now,
                status=status,
                status_reason=reason,
            )
            if status == RateStatus.QUARANTINED and len(details) < MAX_DETAILS:
                details.append({"pair": f"{obs.base}/{obs.quote}", "status": status, "reason": reason})
            if status == RateStatus.ACCEPTED:
                accepted_currencies.add(obs.base)
        ok = bool(observations) and counts[RateStatus.ACCEPTED] > 0
        log.ok = ok
        log.n_received = len(observations)
        log.n_accepted = counts[RateStatus.ACCEPTED]
        log.n_quarantined = counts[RateStatus.QUARANTINED]
        log.n_rejected = counts[RateStatus.REJECTED]
        log.details = details
        if not observations:
            log.error_code, log.error_message = "no_observations", "The provider returned no rates."
        elif not ok:
            log.error_code, log.error_message = "nothing_accepted", "No observation passed validation."
        log.finished_at = timezone.now()
        log.latency_ms = int((time.monotonic() - started) * 1000)
        log.save()
        source.last_attempt_at = now
        if ok:
            source.last_success_at, source.consecutive_failures, source.last_error = now, 0, ""
        else:
            source.last_failure_at, source.consecutive_failures = now, source.consecutive_failures + 1
            source.last_error = log.error_code
        source.save(
            update_fields=[
                "last_attempt_at",
                "last_success_at",
                "last_failure_at",
                "consecutive_failures",
                "last_error",
                "updated_at",
            ]
        )
        if accepted_currencies:
            transaction.on_commit(
                lambda: rates_changed.send(
                    sender=ExchangeRateSource, currencies=accepted_currencies, source=source.code
                )
            )
    return log


def _fail(source, log, code, message, started) -> RateFetchLog:
    now = timezone.now()
    log.ok, log.error_code, log.error_message = False, code[:60], message[:300]
    log.finished_at, log.latency_ms = now, int((time.monotonic() - started) * 1000)
    log.save()
    source.last_attempt_at, source.last_failure_at = now, now
    source.consecutive_failures += 1
    source.last_error = code[:300]
    source.save(
        update_fields=["last_attempt_at", "last_failure_at", "consecutive_failures", "last_error", "updated_at"]
    )
    logger.warning("rate_refresh_failed", source=source.code, code=code)
    return log


def due_sources(now=None):
    now = now or timezone.now()
    for source in ExchangeRateSource.objects.filter(is_enabled=True).exclude(provider_key="manual"):
        if source.last_attempt_at is None or now - source.last_attempt_at >= timedelta(
            seconds=source.poll_interval_seconds
        ):
            yield source
