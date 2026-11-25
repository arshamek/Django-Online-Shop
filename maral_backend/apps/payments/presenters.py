"""Customer-safe and staff views of payments. Customers never see provider references, raw payloads or review notes."""

from . import methods  # noqa: F401


def payment_out(payment, *, staff: bool = False) -> dict:
    data = {
        "id": str(payment.public_id),
        "provider": payment.provider,
        "status": payment.status,
        "amount_irr": str(payment.amount_irr),
        "charge": {"amount": str(payment.charge_amount), "currency": payment.charge_currency},
        "exchange_rate": None
        if payment.fx_rate is None
        else {"rate": str(payment.fx_rate), "quote": "IRR", "source": payment.fx_rate_source},
        "redirect_url": payment.redirect_url if payment.status in ("initiated", "pending") else "",
        "expires_at": payment.expires_at,
        "captured_at": payment.captured_at,
        "refunded_irr": str(payment.refunded_irr),
        "failure_code": payment.failure_code,
        "created_at": payment.created_at,
    }
    if staff:
        data.update(
            {
                "provider_ref": payment.provider_ref,
                "provider_mode": payment.provider_mode,
                "checkout_id": str(payment.checkout.public_id),
                "user_id": str(payment.user.public_id) if hasattr(payment.user, "public_id") else payment.user_id,
                "needs_review": payment.needs_review,
                "review_reason": payment.review_reason,
                "failure_reason": payment.failure_reason,
                "verified_at": payment.verified_at,
                "refunded_charge_amount": str(payment.refunded_charge_amount),
            }
        )
    return data


def transaction_out(t) -> dict:
    return {
        "kind": t.kind, "event_id": t.event_id, "amount": None if t.amount is None else str(t.amount),
        "currency": t.currency, "amount_irr": str(t.amount_irr), "created_at": t.created_at,
    }  # fmt: skip


def refund_out(r) -> dict:
    return {
        "id": str(r.public_id), "status": r.status, "amount_irr": str(r.amount_irr),
        "charge": {"amount": str(r.charge_amount), "currency": r.charge_currency},
        "provider_ref": r.provider_ref, "error_code": r.error_code, "created_at": r.created_at, "completed_at": r.completed_at,
    }  # fmt: skip
