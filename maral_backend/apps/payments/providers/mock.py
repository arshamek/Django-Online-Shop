"""
MOCK providers for development and automated tests ONLY (settings.PAYMENTS_ALLOW_FAKE_PROVIDER). They move no money and
prove nothing about any real gateway: a real adapter is "working" only after an authorised sandbox account completed the
flow (docs/payments/PROVIDER_INTEGRATION_GUIDE.md).

Provider-side state lives in the Django cache so that every worker sees the same "remote" payment; tests and the
`simulate_payment` command drive it with `MockRemote`.
"""

import json
import time
import uuid
from decimal import Decimal

from django.core.cache import cache

from .base import (
    CAPTURED,
    FAILED,
    PENDING,
    REFUNDED,
    UNKNOWN,
    CallbackInput,
    CallbackRejected,
    InitiateResult,
    PaymentRequest,
    ProviderCapabilities,
    ProviderError,
    ProviderEvent,
    RefundRejected,
    RefundResult,
    VerificationResult,
)
from .international import InternationalProvider, sign_payload, verify_signature
from .iranian import IranianGatewayProvider


class MockRemote:
    """What the (pretend) provider knows. Keyed by the provider's own payment reference."""

    TTL = 60 * 60 * 24

    @staticmethod
    def _k(ref: str) -> str:
        return f"mockpay:{ref}"

    @classmethod
    def get(cls, ref: str) -> dict | None:
        return cache.get(cls._k(ref))

    @classmethod
    def put(cls, ref: str, state: dict) -> None:
        cache.set(cls._k(ref), state, cls.TTL)

    @classmethod
    def simulate(cls, ref: str, status: str, *, amount=None, currency=None) -> dict:
        """The customer paid / failed at the provider (optionally with a different amount, to test mismatches)."""
        state = cls.get(ref)
        if state is None:
            raise KeyError(ref)
        state["status"] = status
        if amount is not None:
            state["amount"] = str(amount)
        if currency is not None:
            state["currency"] = currency
        cls.put(ref, state)
        return state

    @classmethod
    def set_outage(cls, key: str, on: bool = True) -> None:
        cache.set(f"mockpay:outage:{key}", on, cls.TTL)

    @classmethod
    def outage(cls, key: str) -> bool:
        return bool(cache.get(f"mockpay:outage:{key}"))


def _new_state(request: PaymentRequest) -> dict:
    return {
        "status": PENDING,
        "amount": str(request.amount),
        "currency": request.currency,
        "merchant_ref": request.merchant_ref,
        "refunds": {},
    }


def _verification(ref: str, state: dict | None) -> VerificationResult:
    if state is None:
        return VerificationResult(status=UNKNOWN, provider_ref=ref)
    return VerificationResult(
        status=state["status"],
        provider_ref=ref,
        amount=Decimal(state["amount"]),
        currency=state["currency"],
        merchant_ref=state["merchant_ref"],
        raw={"mock": True, "status": state["status"]},
    )


# ------------------------------------------------------------------------------------------------ Iranian-style
class MockIranianGateway(IranianGatewayProvider):
    """Redirect gateway whose browser return is forgeable and must be verified server-side. No automated refunds."""

    key = "mock_ir"
    default_names = {"fa": "درگاه آزمایشی ایرانی", "en": "Mock Iranian gateway"}
    capabilities = ProviderCapabilities(
        kind="iranian",
        supported_currencies=("IRR",),
        supports_refund=False,
        supports_server_verification=True,
        is_mock=True,
    )

    def initiate(self, request: PaymentRequest) -> InitiateResult:
        if MockRemote.outage(self.key):
            raise ProviderError("mock outage")
        if request.currency != "IRR":
            raise ProviderError("this gateway charges IRR only")
        ref = f"MIR{uuid.uuid4().hex[:20]}"
        MockRemote.put(ref, _new_state(request))
        return InitiateResult(
            provider_ref=ref, redirect_url=f"https://pay.mock-ir.test/start/{ref}", raw={"mock": True}
        )

    def parse_callback(self, data: CallbackInput) -> ProviderEvent:
        params = dict(data.query)
        if data.body:
            try:
                body = json.loads(data.body)
                if isinstance(body, dict):
                    params = {**body, **params}
            except json.JSONDecodeError:
                pass
        ref = str(params.get("ref", "")).strip()
        if not ref:
            raise CallbackRejected("missing provider reference")
        claimed = CAPTURED if str(params.get("status", "")).upper() == "OK" else FAILED
        return ProviderEvent(
            event_id=f"{ref}:{claimed}",  # these gateways send no event id: derive a deterministic one
            provider_ref=ref,
            status=claimed,
            merchant_ref=str(params.get("m", "")),
            trusted=False,  # a browser return: only a hint to go and verify
        )

    def verify(self, provider_ref: str, request_hint=None) -> VerificationResult:
        if MockRemote.outage(self.key):
            raise ProviderError("mock outage")
        return _verification(provider_ref, MockRemote.get(provider_ref))


# ------------------------------------------------------------------------------------------------ international-style
class MockInternationalProvider(InternationalProvider):
    """Hosted-session provider with signed, replay-protected webhooks, partial refunds and provider-side idempotency."""

    key = "mock_intl"
    default_names = {"fa": "پرداخت بین‌المللی آزمایشی", "en": "Mock international checkout"}
    capabilities = ProviderCapabilities(
        kind="international",
        supported_currencies=("USD", "EUR", "AED", "TRY"),
        supports_refund=True,
        supports_partial_refund=True,
        supports_server_verification=True,
        supports_cancel=True,
        is_mock=True,
    )
    SIGNATURE_HEADER = "X-Mock-Signature"

    def _secret(self) -> str:
        return self.credentials().get("webhook_secret", "")

    def initiate(self, request: PaymentRequest) -> InitiateResult:
        if MockRemote.outage(self.key):
            raise ProviderError("mock outage")
        if request.currency not in self.capabilities.supported_currencies:
            raise ProviderError("currency not supported")
        ref = f"cs_mock_{uuid.uuid4().hex[:20]}"
        MockRemote.put(ref, _new_state(request))
        return InitiateResult(
            provider_ref=ref,
            redirect_url=f"https://checkout.mock-intl.test/pay/{ref}",
            raw={"mock": True, "session": ref},
        )

    def parse_callback(self, data: CallbackInput) -> ProviderEvent:
        headers = {k.lower(): v for k, v in data.headers.items()}
        verify_signature(self._secret(), headers.get(self.SIGNATURE_HEADER.lower(), ""), data.body)
        try:
            payload = json.loads(data.body)
            event_type = payload["type"]
            obj = payload["data"]
            status = {"payment.succeeded": CAPTURED, "payment.failed": FAILED, "refund.succeeded": REFUNDED}.get(
                event_type, PENDING
            )
            return ProviderEvent(
                event_id=str(payload["id"]),
                provider_ref=str(obj["session"]),
                status=status,
                amount=Decimal(str(obj["amount"])) if obj.get("amount") is not None else None,
                currency=str(obj.get("currency", "")),
                merchant_ref=str(obj.get("merchant_ref", "")),
                trusted=True,
                raw=payload,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ArithmeticError):
            raise CallbackRejected("malformed payload") from None

    def verify(self, provider_ref: str, request_hint=None) -> VerificationResult:
        if MockRemote.outage(self.key):
            raise ProviderError("mock outage")
        return _verification(provider_ref, MockRemote.get(provider_ref))

    def refund(self, provider_ref: str, amount: Decimal, currency: str, idempotency_key: str) -> RefundResult:
        if MockRemote.outage(self.key):
            raise ProviderError("mock outage")
        state = MockRemote.get(provider_ref)
        if state is None or state["status"] != CAPTURED:
            raise RefundRejected("payment is not captured at the provider", "payment_not_captured")
        done = state["refunds"].get(idempotency_key)
        if done is not None:  # provider-side idempotency: the same key never refunds twice
            return RefundResult(provider_ref=done["ref"], raw={"mock": True, "replayed": True})
        if currency != state["currency"]:
            raise RefundRejected("currency mismatch", "currency_mismatch")
        already = sum((Decimal(r["amount"]) for r in state["refunds"].values()), Decimal(0))
        if amount <= 0 or already + amount > Decimal(state["amount"]):
            raise RefundRejected("refund exceeds the captured amount", "refund_exceeds_payment")
        ref = f"re_mock_{uuid.uuid4().hex[:16]}"
        state["refunds"][idempotency_key] = {"ref": ref, "amount": str(amount)}
        MockRemote.put(provider_ref, state)
        return RefundResult(provider_ref=ref, raw={"mock": True})

    def cancel(self, provider_ref: str) -> None:
        state = MockRemote.get(provider_ref)
        if state and state["status"] == PENDING:
            state["status"] = FAILED
            MockRemote.put(provider_ref, state)


# ------------------------------------------------------------------------------------------------ test helpers
def signature_header(secret: str, body: bytes, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    return f"t={timestamp},v1={sign_payload(secret, timestamp, body)}"


def webhook_body(event_id: str, event_type: str, session: str, amount, currency: str, merchant_ref: str = "") -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "data": {"session": session, "amount": str(amount), "currency": currency, "merchant_ref": merchant_ref},
        }
    ).encode()
