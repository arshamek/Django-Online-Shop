"""
Payment gateway interface. A real gateway (Iranian PSP, Stripe ...) is added by implementing this class and
registering it; nothing else changes. No real gateway is implemented in Phase 7 (credentials / contract needed).
"""

import abc
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class InitiateResult:
    provider_ref: str
    redirect_url: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class WebhookEvent:
    event_id: str
    provider_ref: str
    status: str  # "captured" | "failed"
    amount_irr: Decimal
    raw: dict = field(default_factory=dict)


@dataclass
class RefundResult:
    provider_ref: str
    raw: dict = field(default_factory=dict)


class WebhookRejected(Exception):
    """Signature missing/invalid or payload malformed."""


class PaymentProvider(abc.ABC):
    key: str = ""
    supports_refund = False

    @abc.abstractmethod
    def initiate(self, payment) -> InitiateResult: ...

    @abc.abstractmethod
    def verify_webhook(self, headers, body: bytes) -> WebhookEvent:
        """Authenticate the callback (signature) and parse it. Raise WebhookRejected otherwise."""

    def refund(self, payment, amount_irr: Decimal, reference: str) -> RefundResult:
        raise NotImplementedError("This provider has no automated refunds; complete the refund manually.")
