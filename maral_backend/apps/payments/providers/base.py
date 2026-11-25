"""
The payment provider contract.

Every gateway (Iranian PSP, Stripe-like international processor ...) is an *adapter*: a subclass of
`BasePaymentProvider` that translates between our neutral dataclasses and the provider's API. Adapters never touch the
database and never decide business rules: `payments.services` does that. Two adapter families exist:

* `IranianGatewayProvider`   (providers/iranian.py)       IRR, browser redirect + return, server-side verify call
* `InternationalProvider`    (providers/international.py) multi-currency, hosted checkout session, signed webhooks

Nothing here proves that any real provider accepts a given merchant: eligibility is an administrator-verified fact
stored in `PaymentProviderConfig` (docs/payments/PROVIDER_INTEGRATION_GUIDE.md).
"""

import abc
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

# ------------------------------------------------------------------------------------------------ neutral data
CAPTURED = "captured"
FAILED = "failed"
PENDING = "pending"
REFUNDED = "refunded"  # reported by the provider, e.g. a refund made in the provider's dashboard
UNKNOWN = "unknown"  # provider has no such payment / cannot say


@dataclass(frozen=True)
class ProviderCapabilities:
    kind: str  # "iranian" | "international"
    supported_currencies: tuple[str, ...]
    supports_refund: bool = False
    supports_partial_refund: bool = False
    supports_server_verification: bool = True  # has an API to ask "what is the state of this payment?"
    supports_cancel: bool = False
    is_mock: bool = False  # never usable unless settings.PAYMENTS_ALLOW_FAKE_PROVIDER


@dataclass
class PaymentRequest:
    """Everything an adapter may know about a payment. Built by the service layer, never by a client."""

    merchant_ref: str  # our reference; sent to the provider and echoed back in every callback
    amount: Decimal  # in `currency`
    currency: str
    amount_irr: Decimal
    callback_url: str  # server-built, https in production
    description: str = ""
    customer_email: str = ""
    customer_phone: str = ""
    locale: str = "fa"
    metadata: dict = field(default_factory=dict)


@dataclass
class InitiateResult:
    provider_ref: str
    redirect_url: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class CallbackInput:
    method: str
    headers: dict
    body: bytes
    query: dict


@dataclass
class ProviderEvent:
    """A parsed callback. `trusted` = authenticated by a signature; untrusted events are only a hint to go and verify."""

    event_id: str
    provider_ref: str
    status: str  # captured | failed | pending | refunded
    amount: Decimal | None = None
    currency: str = ""
    merchant_ref: str = ""
    trusted: bool = False
    raw: dict = field(default_factory=dict)


@dataclass
class VerificationResult:
    """The answer of a server-to-server status query: the ONLY thing that may mark an untrusted callback as paid."""

    status: str
    provider_ref: str = ""
    amount: Decimal | None = None
    currency: str = ""
    merchant_ref: str = ""
    failure_code: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class RefundResult:
    provider_ref: str
    raw: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------ errors
class ProviderError(Exception):
    """The provider could not be reached or answered nonsense. The outcome of the call is UNKNOWN."""


class CallbackRejected(Exception):
    """Signature missing/invalid, timestamp outside tolerance, or payload malformed."""


WebhookRejected = CallbackRejected  # Phase 7 name


class RefundRejected(Exception):
    """The provider definitively refused the refund (nothing was sent back)."""

    def __init__(self, message: str = "", code: str = "refund_rejected"):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------------------------------------ the contract
class BasePaymentProvider(abc.ABC):
    key: str = ""
    capabilities: ProviderCapabilities
    default_names = {"fa": "", "en": ""}
    # Verify with the provider even when the callback is signed. Unsigned browser returns are ALWAYS verified.
    always_verify = False

    # ---- credentials: server-side only, from the environment, never from the database or a request
    def credentials(self) -> dict:
        creds = (getattr(settings, "PAYMENT_PROVIDER_CREDENTIALS", {}) or {}).get(self.key)
        if not creds:
            raise ProviderError(f"No credentials configured for payment provider {self.key!r}.")
        return creds

    # ---- required
    @abc.abstractmethod
    def initiate(self, request: PaymentRequest) -> InitiateResult:
        """Create the payment / checkout session at the provider. Must be safe to call once per merchant_ref."""

    @abc.abstractmethod
    def parse_callback(self, data: CallbackInput) -> ProviderEvent:
        """Authenticate (signature, timestamp) and parse a callback. Raise CallbackRejected otherwise."""

    @abc.abstractmethod
    def verify(self, provider_ref: str, request_hint: PaymentRequest | None = None) -> VerificationResult:
        """Ask the provider, server to server, for the true state of a payment."""

    # ---- optional
    def refund(self, provider_ref: str, amount: Decimal, currency: str, idempotency_key: str) -> RefundResult:
        raise RefundRejected(
            "This provider has no automated refunds; refund manually and record it.", "manual_refund_required"
        )

    def cancel(self, provider_ref: str) -> None:  # noqa: B027
        """Best effort: tell the provider the customer abandoned the session. Default: nothing to do."""

    # ---- helpers
    def supported_currencies(self) -> tuple[str, ...]:
        return self.capabilities.supported_currencies
