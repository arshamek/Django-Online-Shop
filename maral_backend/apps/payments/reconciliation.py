"""
Reconciliation: compare our payments with what the provider says, server to server.

* live payments past a grace period (a webhook / browser return may have been lost)  -> applied if the provider says paid
* recently failed / expired / cancelled payments (money may arrive after we gave up)  -> captured if paid (a late-payment
                                                                                       refund is opened for cancelled orders)
* recently captured payments (divergence check)                                       -> NEVER reversed automatically,
                                                                                       flagged for finance review
Every run is stored with its discrepancies.
"""

from datetime import timedelta

import structlog
from django.utils import timezone

from apps.common.exceptions import DomainError

from .models import LIVE, Payment, PaymentStatus, ReconciliationRun
from .providers import registry
from .providers.base import CAPTURED, ProviderError
from .services import flag, verify_payment

logger = structlog.get_logger("maral.payments.reconcile")


def reconcile(
    *, provider: str = "", grace_minutes: int = 10, lookback_days: int = 7, limit: int = 500, actor=None
) -> ReconciliationRun:
    now = timezone.now()
    run = ReconciliationRun.objects.create(
        provider=provider, triggered_by=actor if getattr(actor, "is_authenticated", False) else None
    )
    base = Payment.objects.exclude(provider_ref="")
    if provider:
        base = base.filter(provider=provider)
    since = now - timedelta(days=lookback_days)
    groups = [
        ("live", base.filter(status__in=LIVE, created_at__lte=now - timedelta(minutes=grace_minutes)), True),
        (
            "closed",
            base.filter(
                status__in=(PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELLED), created_at__gte=since
            ),
            True,
        ),
        ("captured", base.filter(status=PaymentStatus.CAPTURED, captured_at__gte=since), False),
    ]
    discrepancies, scanned, applied, errors = [], 0, 0, 0
    for label, queryset, apply in groups:
        for payment in queryset.order_by("id")[: max(limit - scanned, 0)]:
            adapter = registry.adapters().get(payment.provider)
            if adapter is None or not adapter.capabilities.supports_server_verification:
                continue
            scanned += 1
            before = payment.status
            try:
                outcome = verify_payment(payment, actor=actor, apply=apply)
            except (DomainError, ProviderError) as exc:
                errors += 1
                discrepancies.append(
                    {"payment": str(payment.public_id), "kind": "verification_error", "detail": type(exc).__name__}
                )
                continue
            remote, local = outcome["provider_status"], outcome["local_status"]
            if outcome["outcome"] == "applied":
                applied += 1
                discrepancies.append(
                    {
                        "payment": str(payment.public_id),
                        "kind": "late_capture" if label == "closed" else "missed_capture",
                        "local_before": before,
                        "provider": remote,
                    }
                )
            elif label == "captured" and remote != CAPTURED:
                flag(payment, "captured_locally_but_not_at_provider")
                discrepancies.append(
                    {
                        "payment": str(payment.public_id),
                        "kind": "captured_not_confirmed",
                        "local": local,
                        "provider": remote,
                    }
                )
            elif remote == CAPTURED and local != PaymentStatus.CAPTURED:
                discrepancies.append(
                    {
                        "payment": str(payment.public_id),
                        "kind": "amount_or_reference_mismatch",
                        "local": local,
                        "provider": remote,
                    }
                )
    run.scanned, run.applied, run.errors, run.discrepancies, run.finished_at = (
        scanned,
        applied,
        errors,
        discrepancies,
        timezone.now(),
    )
    run.save()
    logger.info(
        "payment_reconciliation_done", scanned=scanned, applied=applied, errors=errors, discrepancies=len(discrepancies)
    )
    return run
