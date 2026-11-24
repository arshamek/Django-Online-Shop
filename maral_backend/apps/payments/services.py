"""Payment initiation and (verified) capture. Browser redirects never change a payment."""

import json
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.exceptions import BusinessRuleViolation, Conflict

from .models import LIVE, Payment, PaymentStatus, PaymentTransaction, TransactionKind
from .providers import registry
from .providers.base import WebhookRejected

logger = structlog.get_logger("maral.payments")


def payable_orders(checkout):
    from apps.orders.state import OrderStatus

    return list(checkout.orders.filter(status=OrderStatus.PENDING_PAYMENT))


def initiate(checkout, user, *, idempotency_key: str, provider_key: str = "") -> Payment:
    """Start (or resume) the payment of every unpaid order of a checkout. Amount = server-side sum of order totals."""
    if checkout.user_id != user.pk:
        raise BusinessRuleViolation("Checkout not found.", code="checkout_not_found")  # ownership: looks missing
    if not idempotency_key or len(idempotency_key) > 80:
        raise BusinessRuleViolation("Send an Idempotency-Key header (max 80 characters).", code="idempotency_key_required")
    with transaction.atomic():
        from apps.orders.models import Checkout

        locked = Checkout.objects.select_for_update().get(pk=checkout.pk)
        previous = Payment.objects.filter(user=user, idempotency_key=idempotency_key).first()
        if previous is not None:
            if previous.checkout_id != locked.pk:
                raise Conflict("This Idempotency-Key was used for another checkout.", code="idempotency_conflict")
            return previous
        orders = payable_orders(locked)
        if not orders:
            raise Conflict("Nothing is waiting for payment.", code="nothing_to_pay")
        now = timezone.now()
        live = Payment.objects.filter(checkout=locked, status__in=LIVE, expires_at__gt=now).first()
        if live is not None:
            return live  # one live payment per checkout; a retry with a new key resumes it
        amount = sum((o.total_irr for o in orders), 0)
        expiry = min(o.payment_due_at for o in orders)
        if expiry <= now:
            raise Conflict("The payment window has closed.", code="payment_window_closed")
        provider = registry.get(provider_key)
        payment = Payment.objects.create(
            checkout=locked, user=user, provider=provider.key, amount_irr=amount, idempotency_key=idempotency_key, expires_at=expiry
        )
        result = provider.initiate(payment)
        payment.provider_ref, payment.redirect_url, payment.status = result.provider_ref, result.redirect_url, PaymentStatus.PENDING
        payment.save(update_fields=["provider_ref", "redirect_url", "status", "updated_at"])
        PaymentTransaction.objects.create(
            payment=payment, kind=TransactionKind.INITIATE, amount_irr=amount, provider_ref=result.provider_ref, raw=result.raw
        )
        return payment


def process_webhook(provider_key: str, headers, body: bytes) -> str:
    """Returns 'applied' | 'duplicate' | 'ignored'. Raises WebhookRejected for unauthenticated callbacks."""
    provider = registry.available().get(provider_key)
    if provider is None:
        raise WebhookRejected("unknown provider")
    event = provider.verify_webhook(headers, body)
    payment = Payment.objects.filter(provider=provider.key, provider_ref=event.provider_ref).first()
    if payment is None:
        logger.warning("payment_webhook_unknown_reference", provider=provider.key)
        return "ignored"
    if event.status == "captured":
        return capture(payment, amount_irr=event.amount_irr, event_id=event.event_id, raw=event.raw)
    if event.status == "failed":
        return fail(payment, event_id=event.event_id, raw=event.raw)
    return "ignored"


def capture(payment: Payment, *, amount_irr, event_id: str, raw=None) -> str:
    """Server-verified capture: moves unpaid orders to PAID. Idempotent per event id."""
    from apps.orders.services import lifecycle

    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        try:
            with transaction.atomic():
                PaymentTransaction.objects.create(
                    payment=locked, kind=TransactionKind.CAPTURE, amount_irr=amount_irr, event_id=event_id, provider_ref=locked.provider_ref, raw=raw or {}
                )
        except IntegrityError:
            return "duplicate"
        if amount_irr != locked.amount_irr:
            # Never apply a payment whose amount differs from what the server asked for: keep it for finance review.
            PaymentTransaction.objects.create(
                payment=locked, kind=TransactionKind.MISMATCH, amount_irr=amount_irr, event_id=event_id, provider_ref=locked.provider_ref, raw=raw or {}
            )
            logger.error("payment_amount_mismatch", payment=str(locked.public_id))
            return "ignored"
        if locked.status == PaymentStatus.CAPTURED:
            return "duplicate"
        locked.status, locked.captured_at, locked.captured_amount_irr = PaymentStatus.CAPTURED, timezone.now(), amount_irr
        locked.save(update_fields=["status", "captured_at", "captured_amount_irr", "updated_at"])
        lifecycle.on_payment_captured(locked)
        return "applied"


def fail(payment: Payment, *, event_id: str, raw=None) -> str:
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        try:
            with transaction.atomic():
                PaymentTransaction.objects.create(payment=locked, kind=TransactionKind.FAILURE, event_id=event_id, provider_ref=locked.provider_ref, raw=raw or {})
        except IntegrityError:
            return "duplicate"
        if locked.status in LIVE:
            locked.status = PaymentStatus.FAILED
            locked.save(update_fields=["status", "updated_at"])
        return "applied"


def expire_due(now=None) -> int:
    now = now or timezone.now()
    return Payment.objects.filter(status__in=LIVE, expires_at__lte=now).update(status=PaymentStatus.EXPIRED, updated_at=now)
