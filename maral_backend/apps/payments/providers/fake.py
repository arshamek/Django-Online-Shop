"""Deterministic provider for development and tests ONLY (settings.PAYMENTS_ALLOW_FAKE_PROVIDER). Never real money."""

import hashlib
import hmac
import json
import uuid
from decimal import Decimal

from django.conf import settings

from .base import InitiateResult, PaymentProvider, RefundResult, WebhookEvent, WebhookRejected


def sign(body: bytes, secret: str | None = None) -> str:
    return hmac.new((secret or settings.PAYMENTS_FAKE_WEBHOOK_SECRET).encode(), body, hashlib.sha256).hexdigest()


class FakeProvider(PaymentProvider):
    key = "fake"
    supports_refund = True

    def initiate(self, payment) -> InitiateResult:
        ref = f"fake-{uuid.uuid4().hex[:16]}"
        return InitiateResult(provider_ref=ref, redirect_url=f"https://pay.fake.test/{ref}", raw={"fake": True})

    def verify_webhook(self, headers, body: bytes) -> WebhookEvent:
        secret = settings.PAYMENTS_FAKE_WEBHOOK_SECRET
        if not secret:
            raise WebhookRejected("webhook secret not configured")
        given = headers.get("X-Signature", "")
        if not hmac.compare_digest(given, sign(body, secret)):
            raise WebhookRejected("bad signature")
        try:
            data = json.loads(body)
            return WebhookEvent(
                event_id=str(data["event_id"]),
                provider_ref=str(data["provider_ref"]),
                status=str(data["status"]),
                amount_irr=Decimal(str(data["amount_irr"])),
                raw=data,
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise WebhookRejected("malformed payload") from exc

    def refund(self, payment, amount_irr, reference) -> RefundResult:
        return RefundResult(provider_ref=f"fake-refund-{reference}", raw={"amount_irr": str(amount_irr)})
