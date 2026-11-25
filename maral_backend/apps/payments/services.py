"""
Payment initiation, callback handling and (verified) capture.

Rules that hold everywhere in this module:
* The browser never changes a payment. Callbacks are authenticated (signature) or confirmed by a server-to-server
  verification call before anything is applied.
* The amount and currency come from the Payment row (computed on the server); a provider message that disagrees is
  recorded as MISMATCH and never applied.
* Every provider event is idempotent: (payment, kind, event_id) is unique in the ledger.
* No network call is made while the checkout row is locked.
"""

import hashlib
import re
from datetime import timedelta
from decimal import Decimal

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.common.exceptions import BusinessRuleViolation, Conflict, DomainError, UpstreamUnavailable

from . import methods
from .models import (
    LIVE,
    CallbackOutcome,
    Payment,
    PaymentStatus,
    PaymentTransaction,
    ProviderCallback,
    TransactionKind,
)
from .providers import registry
from .providers.base import (
    CAPTURED,
    FAILED,
    REFUNDED,
    UNKNOWN,
    CallbackInput,
    CallbackRejected,
    PaymentRequest,
    ProviderError,
    VerificationResult,
    WebhookRejected,  # noqa: F401  (Phase 7 name, still exported)
)

logger = structlog.get_logger("maral.payments")

STALE_INITIATION = timedelta(minutes=2)


# ------------------------------------------------------------------------------------------------ helpers
_SENSITIVE = re.compile(
    r"(pass(word|wd)?|secret|token|authorization|cookie|api[_-]?key|signature|hmac|credential|card|cvv|cvc|pan|iban)",
    re.IGNORECASE,
)


def _redact(value, depth=0):
    """Recursively mask credential-looking keys. Provider payloads are stored for audit but never carry secrets or card data."""
    if depth > 6:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SENSITIVE.search(str(k)) else _redact(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, depth + 1) for v in value]
    return value


def _safe(raw) -> dict:
    return _redact(dict(raw or {}))


def _ledger(payment: Payment, kind: str, *, event_id="", amount=None, currency="", amount_irr=Decimal(0), raw=None):
    return PaymentTransaction.objects.create(
        payment=payment, kind=kind, event_id=event_id, amount=amount, currency=currency, amount_irr=amount_irr,
        provider_ref=payment.provider_ref, raw=_safe(raw),
    )  # fmt: skip


def _receipt(provider, outcome, *, payment=None, reason="", event_id="", body=b"", remote_ip=None):
    ProviderCallback.objects.create(
        provider=provider[:30], payment=payment, outcome=outcome, reason=reason[:200], event_id=event_id[:120],
        body_sha256=hashlib.sha256(body or b"").hexdigest(), remote_ip=remote_ip or None,
    )  # fmt: skip


def flag(payment: Payment, reason: str) -> None:
    """Put a payment in front of finance (mismatch, unknown reference, provider-side refund ...)."""
    Payment.objects.filter(pk=payment.pk).update(needs_review=True, review_reason=reason[:200])
    audit.record("payment.flagged", target=payment, reason=reason[:200], provider=payment.provider)


def callback_url(provider_key: str, merchant_ref: str) -> str:
    base = settings.PAYMENTS_CALLBACK_BASE_URL.rstrip("/")
    return f"{base}/api/v1/payments/callback/{provider_key}?m={merchant_ref}"


def payable_orders(checkout):
    from apps.orders.state import OrderStatus

    return list(checkout.orders.filter(status=OrderStatus.PENDING_PAYMENT))


# ------------------------------------------------------------------------------------------------ initiation
def initiate(checkout, user, *, idempotency_key: str, provider_key: str = "", now=None) -> Payment:
    """Start (or resume) the payment of every unpaid order of a checkout. Amount = server-side sum of order totals."""
    if checkout.user_id != user.pk:
        raise BusinessRuleViolation("Checkout not found.", code="checkout_not_found")  # ownership: looks missing
    if not idempotency_key or len(idempotency_key) > 80:
        raise BusinessRuleViolation(
            "Send an Idempotency-Key header (max 80 characters).", code="idempotency_key_required"
        )
    now = now or timezone.now()
    with transaction.atomic():
        from apps.orders.models import Checkout

        locked = Checkout.objects.select_for_update().get(pk=checkout.pk)
        previous = Payment.objects.filter(user=user, idempotency_key=idempotency_key).first()
        if previous is not None:
            if previous.checkout_id != locked.pk or (provider_key and provider_key != previous.provider):
                raise Conflict("This Idempotency-Key was used for another request.", code="idempotency_conflict")
            return previous
        orders = payable_orders(locked)
        if not orders:
            raise Conflict("Nothing is waiting for payment.", code="nothing_to_pay")
        Payment.objects.filter(
            checkout=locked, status=PaymentStatus.INITIATED, provider_ref="", created_at__lt=now - STALE_INITIATION
        ).update(status=PaymentStatus.FAILED, failure_code="initiation_incomplete", updated_at=now)
        live = Payment.objects.filter(checkout=locked, status__in=LIVE, expires_at__gt=now).first()
        if live is not None:
            if provider_key and provider_key != live.provider:
                raise Conflict(
                    "A payment is already in progress; finish or cancel it first.", code="payment_in_progress"
                )
            return live  # one live payment per checkout; a retry with a new key resumes it
        amount = sum((o.total_irr for o in orders), Decimal(0))
        expiry = min(o.payment_due_at for o in orders)
        if expiry <= now:
            raise Conflict("The payment window has closed.", code="payment_window_closed")
        country = (locked.address_snapshot or {}).get("country", "")
        charge = methods.resolve(
            provider_key, amount_irr=amount, display_currency=locked.display_currency, country=country
        )
        payment = Payment.objects.create(
            checkout=locked, user=user, provider=charge.adapter.key, amount_irr=amount, idempotency_key=idempotency_key,
            expires_at=expiry, charge_currency=charge.currency, charge_amount=charge.amount, fx_rate=charge.fx_rate,
            fx_rate_source=charge.fx_source, provider_mode=charge.mode, currency="IRR", created_at=now,
        )  # fmt: skip
        customer = locked.customer_snapshot or {}
        codes = ", ".join(o.code for o in orders)

    # ---- outside the checkout lock: talk to the provider
    request = PaymentRequest(
        merchant_ref=payment.merchant_ref, amount=charge.amount, currency=charge.currency, amount_irr=amount,
        callback_url=callback_url(payment.provider, payment.merchant_ref), description=f"Maral {codes}"[:100],
        customer_email=customer.get("email", ""), customer_phone=customer.get("phone", ""),
        metadata={"orders": [o.code for o in orders]},
    )  # fmt: skip
    try:
        result = charge.adapter.initiate(request)
    except ProviderError as exc:
        _mark_failed(
            payment,
            "provider_unavailable",
            "The provider could not start the payment.",
            event_id=f"initiate-failed:{payment.public_id}",
        )
        logger.error("payment_initiate_failed", provider=payment.provider, error=str(exc)[:120])
        raise UpstreamUnavailable(
            "The payment provider is unavailable. Try again shortly.", code="payment_provider_error"
        ) from None
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        payment.provider_ref, payment.redirect_url, payment.status = (
            result.provider_ref,
            result.redirect_url,
            PaymentStatus.PENDING,
        )
        payment.save(update_fields=["provider_ref", "redirect_url", "status", "updated_at"])
        _ledger(
            payment,
            TransactionKind.INITIATE,
            amount=charge.amount,
            currency=charge.currency,
            amount_irr=amount,
            raw=result.raw,
        )
    audit.record("payment.initiated", actor=user, target=payment, provider=payment.provider, currency=charge.currency)
    return payment


def _mark_failed(payment: Payment, code: str, reason: str, *, event_id: str) -> None:
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status in LIVE:
            locked.status, locked.failure_code, locked.failure_reason = PaymentStatus.FAILED, code, reason[:300]
            locked.save(update_fields=["status", "failure_code", "failure_reason", "updated_at"])
            _ledger(locked, TransactionKind.FAILURE, event_id=event_id, raw={"code": code})


# ------------------------------------------------------------------------------------------------ callbacks
def process_callback(provider_key: str, *, headers, body: bytes, query=None, method="POST", remote_ip=None) -> str:
    """Returns 'applied' | 'duplicate' | 'ignored'. Raises CallbackRejected for unauthenticated / inconsistent callbacks.

    Deliberately NOT gated on the provider's admin configuration: if an administrator disables a method while a customer
    is mid-payment, the money that arrives must still be recognised.
    """
    adapter = registry.adapters().get(provider_key)
    if adapter is None:
        _receipt(provider_key, CallbackOutcome.REJECTED, reason="unknown provider", body=body, remote_ip=remote_ip)
        raise CallbackRejected("unknown provider")
    try:
        event = adapter.parse_callback(
            CallbackInput(method=method, headers=dict(headers), body=body, query=dict(query or {}))
        )
    except CallbackRejected as exc:
        _receipt(provider_key, CallbackOutcome.REJECTED, reason=str(exc), body=body, remote_ip=remote_ip)
        logger.warning("payment_callback_rejected", provider=provider_key, reason=str(exc)[:80])
        raise
    payment = Payment.objects.filter(provider=adapter.key, provider_ref=event.provider_ref).first()
    if payment is None:
        _receipt(
            provider_key,
            CallbackOutcome.IGNORED,
            reason="unknown_reference",
            event_id=event.event_id,
            body=body,
            remote_ip=remote_ip,
        )
        logger.warning("payment_callback_unknown_reference", provider=adapter.key)
        return "ignored"
    if event.merchant_ref and event.merchant_ref != payment.merchant_ref:
        flag(payment, "callback_reference_mismatch")
        _receipt(
            provider_key,
            CallbackOutcome.REJECTED,
            payment=payment,
            reason="reference_mismatch",
            event_id=event.event_id,
            body=body,
            remote_ip=remote_ip,
        )
        raise CallbackRejected("reference mismatch")
    outcome = _apply_event(adapter, payment, event)
    _receipt(provider_key, outcome, payment=payment, event_id=event.event_id, body=body, remote_ip=remote_ip)
    return outcome


def process_webhook(provider_key: str, headers, body: bytes, query=None, remote_ip=None) -> str:  # Phase 7 name
    return process_callback(provider_key, headers=headers, body=body, query=query, remote_ip=remote_ip)


def _apply_event(adapter, payment: Payment, event) -> str:
    if event.status == REFUNDED:
        flag(payment, "provider_reported_refund")  # a refund we did not issue: finance must look at it
        return CallbackOutcome.IGNORED
    if event.status not in (CAPTURED, FAILED):
        return CallbackOutcome.IGNORED
    if not event.trusted or adapter.always_verify:
        if not adapter.capabilities.supports_server_verification:
            return CallbackOutcome.IGNORED  # an unsigned claim we cannot confirm is never applied
        try:
            result = adapter.verify(payment.provider_ref)
        except ProviderError:
            logger.warning("payment_verification_unavailable", provider=adapter.key)
            return CallbackOutcome.IGNORED  # reconciliation will confirm it later
        _ledger(payment, TransactionKind.VERIFY, raw=result.raw)
        Payment.objects.filter(pk=payment.pk).update(verified_at=timezone.now())
        return _apply_verification(payment, result, event_id=event.event_id, raw=event.raw)
    if event.status == CAPTURED:
        return capture(payment, amount=event.amount, currency=event.currency, event_id=event.event_id, raw=event.raw)
    return fail(payment, event_id=event.event_id, raw=event.raw)


def _apply_verification(payment: Payment, result: VerificationResult, *, event_id: str, raw=None) -> str:
    if result.merchant_ref and result.merchant_ref != payment.merchant_ref:
        flag(payment, "verification_reference_mismatch")
        return CallbackOutcome.IGNORED
    if result.status == CAPTURED:
        return capture(
            payment, amount=result.amount, currency=result.currency, event_id=event_id, raw=raw or result.raw
        )
    if result.status == FAILED:
        return fail(payment, event_id=event_id, code=result.failure_code, raw=raw or result.raw)
    if result.status == UNKNOWN and payment.status in LIVE:
        flag(payment, "provider_has_no_record")
    return CallbackOutcome.IGNORED


# ------------------------------------------------------------------------------------------------ state changes
def capture(payment: Payment, *, amount, currency: str, event_id: str, raw=None) -> str:
    """Server-verified capture: moves unpaid orders to PAID. Idempotent per event id and per payment."""
    from apps.orders.services import lifecycle

    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status == PaymentStatus.CAPTURED:
            return CallbackOutcome.DUPLICATE
        if amount is None or currency != locked.charge_currency or Decimal(amount) != locked.charge_amount:
            # Never apply money that differs from what the server asked for: keep it for finance review.
            try:
                with transaction.atomic():
                    _ledger(
                        locked, TransactionKind.MISMATCH, event_id=event_id, amount=amount, currency=currency, raw=raw
                    )
            except IntegrityError:
                return CallbackOutcome.DUPLICATE
            flag(locked, "amount_or_currency_mismatch")
            logger.error("payment_amount_mismatch", payment=str(locked.public_id))
            return CallbackOutcome.IGNORED
        try:
            with transaction.atomic():
                _ledger(
                    locked,
                    TransactionKind.CAPTURE,
                    event_id=event_id,
                    amount=amount,
                    currency=currency,
                    amount_irr=locked.amount_irr,
                    raw=raw,
                )
        except IntegrityError:
            return CallbackOutcome.DUPLICATE
        now = timezone.now()
        locked.status, locked.captured_at, locked.captured_amount_irr = PaymentStatus.CAPTURED, now, locked.amount_irr
        locked.verified_at = locked.verified_at or now
        locked.failure_code = locked.failure_reason = ""
        locked.save(
            update_fields=[
                "status",
                "captured_at",
                "captured_amount_irr",
                "verified_at",
                "failure_code",
                "failure_reason",
                "updated_at",
            ]
        )
        lifecycle.on_payment_captured(locked)
        audit.record("payment.captured", target=locked, provider=locked.provider, amount_irr=str(locked.amount_irr))
        return CallbackOutcome.APPLIED


def fail(payment: Payment, *, event_id: str, code: str = "", reason: str = "", raw=None) -> str:
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status == PaymentStatus.CAPTURED:
            return CallbackOutcome.IGNORED  # a captured payment is final
        try:
            with transaction.atomic():
                _ledger(locked, TransactionKind.FAILURE, event_id=event_id, raw=raw)
        except IntegrityError:
            return CallbackOutcome.DUPLICATE
        if locked.status in LIVE:
            locked.status, locked.failure_code, locked.failure_reason = PaymentStatus.FAILED, code[:40], reason[:300]
            locked.save(update_fields=["status", "failure_code", "failure_reason", "updated_at"])
            audit.record("payment.failed", target=locked, provider=locked.provider, code=code[:40])
        return CallbackOutcome.APPLIED


# ------------------------------------------------------------------------------------------------ verification
def verify_payment(payment: Payment, *, actor=None, apply: bool = True) -> dict:
    """Ask the provider (server to server) for the truth. With apply=False nothing changes (reconciliation of captured payments)."""
    adapter = registry.adapters().get(payment.provider)
    if adapter is None:
        raise BusinessRuleViolation("The provider adapter is not available.", code="payment_provider_unavailable")
    if not adapter.capabilities.supports_server_verification:
        raise BusinessRuleViolation(
            "This provider cannot be verified automatically.", code="verification_not_supported"
        )
    if not payment.provider_ref:
        raise BusinessRuleViolation("The payment was never started at the provider.", code="no_provider_reference")
    try:
        result = adapter.verify(payment.provider_ref)
    except ProviderError:
        raise UpstreamUnavailable("The provider could not be reached.", code="provider_unreachable") from None
    _ledger(payment, TransactionKind.VERIFY, raw=result.raw)
    Payment.objects.filter(pk=payment.pk).update(verified_at=timezone.now())
    outcome = (
        _apply_verification(payment, result, event_id=f"verify:{payment.provider_ref}", raw=result.raw)
        if apply
        else "not_applied"
    )
    audit.record("payment.verified", actor=actor, target=payment, provider_status=result.status, outcome=str(outcome))
    payment.refresh_from_db()
    return {
        "provider_status": result.status,
        "outcome": str(outcome),
        "local_status": payment.status,
        "provider_amount": None if result.amount is None else str(result.amount),
        "provider_currency": result.currency,
    }


def cancel_payment(payment: Payment, user=None) -> Payment:
    """The customer abandons a live payment so they can start another (possibly with a different method)."""
    locked = Payment.objects.get(pk=payment.pk)
    if locked.status not in LIVE:
        raise Conflict("Only a payment in progress can be cancelled.", code="payment_not_cancellable")
    adapter = registry.adapters().get(locked.provider)
    if adapter and locked.provider_ref and adapter.capabilities.supports_server_verification:
        try:
            verify_payment(locked, apply=True)  # if the customer already paid, that wins
        except (DomainError, ProviderError):
            raise UpstreamUnavailable(
                "Could not confirm the payment state with the provider.", code="provider_unreachable"
            ) from None
        locked.refresh_from_db()
        if locked.status == PaymentStatus.CAPTURED:
            raise Conflict("The payment has already been completed.", code="payment_already_captured")
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=locked.pk)
        if locked.status not in LIVE:
            raise Conflict("Only a payment in progress can be cancelled.", code="payment_not_cancellable")
        locked.status = PaymentStatus.CANCELLED
        locked.save(update_fields=["status", "updated_at"])
        _ledger(locked, TransactionKind.CANCEL, event_id=f"cancel:{locked.public_id}")
    if adapter and adapter.capabilities.supports_cancel:
        try:
            adapter.cancel(locked.provider_ref)
        except ProviderError:
            pass  # best effort; the session expires on its own
    audit.record("payment.cancelled", actor=user, target=locked)
    return locked


def expire_due(now=None) -> int:
    """Payments past their window. A payment the provider says is paid is captured, not expired."""
    now = now or timezone.now()
    count = 0
    for payment in Payment.objects.filter(status__in=LIVE, expires_at__lte=now).order_by("id"):
        adapter = registry.adapters().get(payment.provider)
        if adapter and payment.provider_ref and adapter.capabilities.supports_server_verification:
            try:
                verify_payment(payment, apply=True)
            except (DomainError, ProviderError):
                logger.warning("payment_expiry_verification_failed", payment=str(payment.public_id))
        with transaction.atomic():
            locked = Payment.objects.select_for_update().get(pk=payment.pk)
            if locked.status not in LIVE:
                continue
            locked.status = PaymentStatus.EXPIRED
            locked.save(update_fields=["status", "updated_at"])
            _ledger(locked, TransactionKind.EXPIRE, event_id=f"expire:{locked.public_id}")
        if adapter and adapter.capabilities.supports_cancel and payment.provider_ref:
            try:
                adapter.cancel(payment.provider_ref)
            except ProviderError:
                pass
        count += 1
    return count
