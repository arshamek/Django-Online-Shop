"""
Provider refunds. One `PaymentRefund` row per `orders.Refund` (unique), which makes executing a refund twice
harmless: the provider is asked at most once per refund (and a provider that sees the same idempotency key twice
answers with the first result). The cap check (never refund more than was captured, counting refunds whose outcome is
still unknown) runs under a row lock on the payment.
"""

from decimal import ROUND_DOWN, Decimal

import structlog
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit import services as audit
from apps.common.exceptions import BusinessRuleViolation, UpstreamUnavailable
from apps.currencies.models import Currency

from .models import (
    COUNTING_PAYMENT_REFUND,
    Payment,
    PaymentRefund,
    PaymentRefundStatus,
    PaymentStatus,
    TransactionKind,
)
from .providers import registry
from .providers.base import ProviderError, RefundRejected
from .services import _ledger

logger = structlog.get_logger("maral.payments.refunds")


def captured_payment_for(order) -> Payment | None:
    return order.checkout.payments.filter(status=PaymentStatus.CAPTURED).order_by("-captured_at").first()


def _quantum(currency: str) -> Decimal:
    if currency == "IRR":
        return Decimal(1)
    cur = Currency.objects.filter(code=currency).first()
    return Decimal(1).scaleb(-(cur.decimal_places if cur else 2))


def _counted(payment: Payment, exclude_pk=None):
    qs = payment.refunds.filter(status__in=COUNTING_PAYMENT_REFUND)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    totals = qs.aggregate(irr=Sum("amount_irr"), charge=Sum("charge_amount"))
    return totals["irr"] or Decimal(0), totals["charge"] or Decimal(0)


def _charge_share(payment: Payment, amount_irr: Decimal, counted_irr: Decimal, counted_charge: Decimal) -> Decimal:
    if (
        counted_irr + amount_irr == payment.amount_irr
    ):  # the last slice takes the remainder: no rounding dust is left over
        return payment.charge_amount - counted_charge
    return (amount_irr * payment.charge_amount / payment.amount_irr).quantize(
        _quantum(payment.charge_currency), rounding=ROUND_DOWN
    )


def execute(order_refund, staff=None) -> PaymentRefund:
    """Send the money back through the provider. Idempotent per orders.Refund."""
    payment = captured_payment_for(order_refund.order)
    if payment is None:
        raise BusinessRuleViolation("No captured payment found.", code="nothing_to_refund")
    adapter = registry.adapters().get(payment.provider)
    if adapter is None or not adapter.capabilities.supports_refund:
        raise BusinessRuleViolation(
            "This provider needs a manual refund; record it with 'complete'.", code="manual_refund_required"
        )
    amount = order_refund.amount_irr

    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        existing = PaymentRefund.objects.filter(order_refund=order_refund).first()
        if existing is not None and existing.status in (PaymentRefundStatus.SUCCEEDED, PaymentRefundStatus.MANUAL):
            return existing  # already sent back: never ask the provider again
        counted_irr, counted_charge = _counted(locked, exclude_pk=existing.pk if existing else None)
        if counted_irr + amount > (locked.captured_amount_irr or Decimal(0)):
            raise BusinessRuleViolation("The refund exceeds what was captured.", code="refund_exceeds_payment")
        if not adapter.capabilities.supports_partial_refund and (counted_irr > 0 or amount != locked.amount_irr):
            raise BusinessRuleViolation(
                "This provider can only refund the full payment; record it manually.",
                code="partial_refund_not_supported",
            )
        charge = _charge_share(locked, amount, counted_irr, counted_charge)
        if existing is None:
            existing = PaymentRefund.objects.create(
                payment=locked, order_refund=order_refund, idempotency_key=f"refund-{order_refund.public_id}", amount_irr=amount,
                charge_amount=charge, charge_currency=locked.charge_currency, created_by=staff if getattr(staff, "is_authenticated", False) else None,
            )  # fmt: skip
        else:  # a previous attempt failed or its outcome is unknown: retry with the SAME idempotency key
            existing.status, existing.error_code, existing.error_message = PaymentRefundStatus.PENDING, "", ""
            existing.amount_irr, existing.charge_amount = amount, charge
            existing.save(update_fields=["status", "error_code", "error_message", "amount_irr", "charge_amount"])
        pr_pk = existing.pk

    pr = PaymentRefund.objects.get(pk=pr_pk)
    try:  # network call: no locks held
        result = adapter.refund(payment.provider_ref, pr.charge_amount, pr.charge_currency, pr.idempotency_key)
    except RefundRejected as exc:
        PaymentRefund.objects.filter(pk=pr.pk).update(
            status=PaymentRefundStatus.FAILED, error_code=exc.code[:40], error_message=str(exc)[:300]
        )
        _ledger(
            payment,
            TransactionKind.REFUND_FAILED,
            event_id=f"{pr.idempotency_key}:{timezone.now().timestamp()}",
            amount=pr.charge_amount,
            currency=pr.charge_currency,
            amount_irr=pr.amount_irr,
        )
        audit.record("payment.refund_failed", actor=staff, target=payment, code=exc.code)
        raise BusinessRuleViolation(f"The provider refused the refund: {exc}", code="provider_refund_failed") from None
    except ProviderError:
        logger.error("payment_refund_outcome_unknown", payment=str(payment.public_id))
        raise UpstreamUnavailable(
            "The provider could not be reached; the refund may or may not have been sent. Retry is safe.",
            code="provider_unreachable",
        ) from None

    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        pr = PaymentRefund.objects.select_for_update().get(pk=pr.pk)
        if pr.status != PaymentRefundStatus.SUCCEEDED:
            pr.status, pr.provider_ref, pr.completed_at = (
                PaymentRefundStatus.SUCCEEDED,
                result.provider_ref[:120],
                timezone.now(),
            )
            pr.save(update_fields=["status", "provider_ref", "completed_at"])
            _book(locked, pr)
            _ledger(
                locked,
                TransactionKind.REFUND,
                event_id=pr.idempotency_key,
                amount=pr.charge_amount,
                currency=pr.charge_currency,
                amount_irr=pr.amount_irr,
                raw=result.raw,
            )
    audit.record(
        "payment.refunded", actor=staff, target=payment, amount_irr=str(pr.amount_irr), provider=payment.provider
    )
    return pr


def _book(payment: Payment, pr: PaymentRefund) -> None:
    payment.refunded_irr += pr.amount_irr
    payment.refunded_charge_amount += pr.charge_amount
    payment.save(update_fields=["refunded_irr", "refunded_charge_amount", "updated_at"])


def record_completion(order_refund, provider_ref: str = "", staff=None) -> PaymentRefund | None:
    """Keep the payment's refunded totals in step when staff completed a refund that was paid back outside the system."""
    payment = captured_payment_for(order_refund.order)
    if payment is None:
        return None
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        existing = PaymentRefund.objects.filter(order_refund=order_refund).first()
        if existing is not None and existing.status in (PaymentRefundStatus.SUCCEEDED, PaymentRefundStatus.MANUAL):
            return existing
        counted_irr, counted_charge = _counted(locked, exclude_pk=existing.pk if existing else None)
        if counted_irr + order_refund.amount_irr > (locked.captured_amount_irr or Decimal(0)):
            raise BusinessRuleViolation("The refund exceeds what was captured.", code="refund_exceeds_payment")
        charge = _charge_share(locked, order_refund.amount_irr, counted_irr, counted_charge)
        if existing is None:
            existing = PaymentRefund(
                payment=locked, order_refund=order_refund, idempotency_key=f"refund-{order_refund.public_id}"
            )
        existing.amount_irr, existing.charge_amount, existing.charge_currency = (
            order_refund.amount_irr,
            charge,
            locked.charge_currency,
        )
        existing.status, existing.provider_ref, existing.completed_at = (
            PaymentRefundStatus.MANUAL,
            provider_ref[:120],
            timezone.now(),
        )
        existing.created_by = staff if getattr(staff, "is_authenticated", False) else None
        existing.save()
        _book(locked, existing)
        _ledger(
            locked,
            TransactionKind.REFUND,
            event_id=f"manual:{order_refund.public_id}",
            amount=charge,
            currency=locked.charge_currency,
            amount_irr=existing.amount_irr,
            raw={"manual": True},
        )
        return existing
