"""Phase 7 deterministic provider (IRR, signed webhook, refunds) for development and tests ONLY. Never real money."""

import hashlib
import hmac
import json
import uuid
from decimal import Decimal

from django.conf import settings

from .base import (
    BasePaymentProvider,
    CallbackInput,
    CallbackRejected,
    InitiateResult,
    PaymentRequest,
    ProviderCapabilities,
    ProviderEvent,
    RefundResult,
    VerificationResult,
)


def sign(body: bytes, secret: str | None = None) -> str:
    return hmac.new((secret or settings.PAYMENTS_FAKE_WEBHOOK_SECRET).encode(), body, hashlib.sha256).hexdigest()


class FakeProvider(BasePaymentProvider):
    key = "fake"
    default_names = {"fa": "درگاه نمایشی", "en": "Demo gateway"}
    capabilities = ProviderCapabilities(
        kind="iranian",
        supported_currencies=("IRR",),
        supports_refund=True,
        supports_partial_refund=True,
        supports_server_verification=False,  # it has no remote state to ask
        is_mock=True,
    )

    def initiate(self, request: PaymentRequest) -> InitiateResult:
        ref = f"fake-{uuid.uuid4().hex[:16]}"
        return InitiateResult(provider_ref=ref, redirect_url=f"https://pay.fake.test/{ref}", raw={"fake": True})

    def parse_callback(self, data: CallbackInput) -> ProviderEvent:
        secret = settings.PAYMENTS_FAKE_WEBHOOK_SECRET
        if not secret:
            raise CallbackRejected("webhook secret not configured")
        given = {k.lower(): v for k, v in data.headers.items()}.get("x-signature", "")
        if not hmac.compare_digest(given, sign(data.body, secret)):
            raise CallbackRejected("bad signature")
        try:
            payload = json.loads(data.body)
            return ProviderEvent(
                event_id=str(payload["event_id"]),
                provider_ref=str(payload["provider_ref"]),
                status=str(payload["status"]),
                amount=Decimal(str(payload["amount_irr"])),
                currency="IRR",
                merchant_ref=str(payload.get("merchant_ref", "")),
                trusted=True,
                raw=payload,
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError, ArithmeticError):
            raise CallbackRejected("malformed payload") from None

    def verify(self, provider_ref: str, request_hint=None) -> VerificationResult:
        raise NotImplementedError("The demo provider has no server-side verification.")

    def refund(self, provider_ref: str, amount: Decimal, currency: str, idempotency_key: str) -> RefundResult:
        return RefundResult(provider_ref=f"fake-refund-{idempotency_key}", raw={"amount": str(amount)})
