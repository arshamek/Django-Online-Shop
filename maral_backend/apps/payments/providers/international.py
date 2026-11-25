"""
Adapter family for international payment providers (hosted checkout sessions + signed webhooks).

Typical shape: the adapter creates a hosted checkout *session* in a supported currency and returns its URL; the
provider later POSTs signed events (signature over timestamp + body, replay-protected) to our webhook; the adapter
verifies the signature and tolerance, then parses the event. Merchant eligibility (which countries a merchant account
may be registered in, which currencies it may settle) is provider- and account-specific and is NEVER assumed here: it
is recorded by an administrator in `PaymentProviderConfig` after checking the provider's own onboarding rules. For a
merchant based in Iran, many international processors are simply not available; docs/payments/PROVIDER_INTEGRATION_GUIDE.md
lists what must be established before an adapter is enabled.
"""

import hashlib
import hmac
import time

from .base import BasePaymentProvider, CallbackRejected, ProviderCapabilities

SIGNATURE_TOLERANCE_SECONDS = 300


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    """HMAC-SHA256 over `<timestamp>.<body>` (the scheme the mock provider and most processors use)."""
    return hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str, header: str, body: bytes, *, now: float | None = None, tolerance: int = SIGNATURE_TOLERANCE_SECONDS
) -> None:
    """`header` = "t=<unix>,v1=<hex>". Raises CallbackRejected when absent, malformed, forged or too old / too new."""
    if not secret:
        raise CallbackRejected("webhook secret not configured")
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        timestamp, given = int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        raise CallbackRejected("malformed signature header") from None
    if abs((now if now is not None else time.time()) - timestamp) > tolerance:
        raise CallbackRejected("signature timestamp outside tolerance")
    if not hmac.compare_digest(given, sign_payload(secret, timestamp, body)):
        raise CallbackRejected("bad signature")


class InternationalProvider(BasePaymentProvider):
    capabilities = ProviderCapabilities(
        kind="international",
        supported_currencies=(),  # an adapter must declare what its provider really supports
        supports_refund=True,
        supports_partial_refund=True,
        supports_server_verification=True,
    )
    always_verify = False
