from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.common.exceptions import BusinessRuleViolation

from ..models import Currency, ExchangeRate, ExchangeRateSource, ProviderKey, RateKind, RateStatus
from ..signals import rates_changed

MANUAL_SOURCE_CODE = "manual"


def manual_source() -> ExchangeRateSource:
    source, _ = ExchangeRateSource.objects.get_or_create(
        code=MANUAL_SOURCE_CODE,
        defaults={
            "name": "Manual rates",
            "provider_key": ProviderKey.MANUAL,
            "kind": RateKind.MANUAL,
            "description": "Rates entered by authorised staff.",
        },
    )
    return source


@transaction.atomic
def set_manual_rate(
    *,
    base: str,
    quote: str,
    rate,
    actor,
    note: str,
    valid_until: datetime | None = None,
    rate_at: datetime | None = None,
    source: ExchangeRateSource | None = None,
    request=None,
) -> ExchangeRate:
    """Record a manual rate (history is kept; nothing is overwritten). A written reason is mandatory."""
    if not note or not note.strip():
        raise BusinessRuleViolation("A reason is required for a manual rate.", code="note_required")
    try:
        value = Decimal(str(rate))
    except InvalidOperation:
        raise BusinessRuleViolation("The rate is not a number.", code="invalid_rate") from None
    if not value.is_finite() or value <= 0:
        raise BusinessRuleViolation("The rate must be greater than zero.", code="invalid_rate")
    currencies = {c.code: c for c in Currency.objects.filter(code__in=[base, quote], is_active=True)}
    if base == quote or len(currencies) != 2:
        raise BusinessRuleViolation("Unknown, inactive or identical currencies.", code="invalid_pair")
    source = source or manual_source()
    if source.is_polled:
        raise BusinessRuleViolation("Manual rates can only be recorded on a manual source.", code="source_not_manual")
    now = timezone.now()
    rate_at = rate_at or now
    if rate_at > now:
        raise BusinessRuleViolation("The rate timestamp cannot be in the future.", code="timestamp_in_future")
    if valid_until is not None and valid_until <= rate_at:
        raise BusinessRuleViolation("valid_until must be after the rate timestamp.", code="invalid_validity")
    row = ExchangeRate.objects.create(
        source=source,
        base_id=base,
        quote_id=quote,
        rate=value,
        rate_at=rate_at,
        fetched_at=now,
        valid_until=valid_until,
        status=RateStatus.ACCEPTED,
        is_manual=True,
        created_by=actor,
        note=note.strip()[:300],
    )
    audit.record(
        "currencies.manual_rate_set", request=request, actor=actor, target=row, pair=f"{base}/{quote}", rate=str(value)
    )
    transaction.on_commit(lambda: rates_changed.send(sender=ExchangeRateSource, currencies={base}, source=source.code))
    return row
