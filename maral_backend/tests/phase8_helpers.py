"""Builders for the Phase 8 tests (payment providers, refunds, shipping, international). All numbers are SYNTHETIC."""

import json
import time
from decimal import Decimal as D

from django.conf import settings

from apps.orders.models import ActorType, Order
from apps.orders.services import transitions
from apps.payments.models import Payment
from apps.payments.providers.base import (
    BasePaymentProvider,
    InitiateResult,
    ProviderCapabilities,
    VerificationResult,
)
from apps.payments.providers.mock import MockInternationalProvider, MockRemote, signature_header, webhook_body
from tests.phase7_helpers import API, make_address, make_product, new_checkout, place_order, put_in_cart, setup_shop

INTL_SECRET = "test-intl-secret"


class DummyRealProvider(BasePaymentProvider):
    """Stands in for a REAL adapter in tests (not a mock): usable only after an administrator verified eligibility."""

    key = "dummy_real"
    capabilities = ProviderCapabilities(kind="international", supported_currencies=("USD", "EUR"), is_mock=False)

    def initiate(self, request):
        return InitiateResult(provider_ref="real-1", redirect_url="https://pay.example/real-1")

    def parse_callback(self, data):
        raise NotImplementedError

    def verify(self, provider_ref, request_hint=None):
        return VerificationResult(status="pending")


def shop_checkout(make_user, login, *, stock=3, key="o-1", quantity=1):
    """A logged-in customer with an unpaid order (payment NOT started). Returns (client, user, checkout_json, order_json)."""
    setup_shop()
    user = make_user()
    client = login(user)
    _, (variant,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": stock})
    put_in_cart(client, variant, quantity)
    checkout = new_checkout(client, make_address(user))
    response = place_order(client, checkout, key=key, initiate_payment=False)
    assert response.status_code == 201, response.content
    return client, user, checkout, response.json()


def start_payment(client, checkout, provider, key="pay-1"):
    return client.post(
        f"{API}/checkout/{checkout['id']}/payment", {"provider": provider}, format="json", HTTP_IDEMPOTENCY_KEY=key
    )


def paid_order(make_user, login, provider="mock_intl", **kw):
    """Order fully paid through `provider`'s own mock flow. Returns (client, order, payment)."""
    client, user, checkout, order_json = shop_checkout(make_user, login, **kw)
    response = start_payment(client, checkout, provider)
    assert response.status_code == 201, response.content
    payment = Payment.objects.get(public_id=response.json()["id"])
    capture_at_provider(payment)
    return (
        client,
        Order.objects.get(
            public_id=order_json["order"]["id"] if "order" in order_json else order_json["orders"][0]["id"]
        ),
        Payment.objects.get(pk=payment.pk),
    )


def capture_at_provider(payment):
    """The customer pays at the (mock) provider and the provider notifies us with a correctly signed webhook."""
    from rest_framework.test import APIClient

    MockRemote.simulate(payment.provider_ref, "captured")
    if payment.provider == "mock_intl":
        body = webhook_body(
            f"evt-{payment.provider_ref}",
            "payment.succeeded",
            payment.provider_ref,
            payment.charge_amount,
            payment.charge_currency,
            payment.merchant_ref,
        )
        response = intl_post(APIClient(), body)
        assert response.status_code == 200 and response.json()["status"] == "applied", response.content
    else:
        response = APIClient().get(
            f"{API}/payments/callback/{payment.provider}",
            {"ref": payment.provider_ref, "status": "OK", "m": payment.merchant_ref},
        )
        assert response.status_code == 302


def intl_post(client, body: bytes, *, secret=INTL_SECRET, timestamp=None, header=None):
    headers = {} if header == "" else {"HTTP_X_MOCK_SIGNATURE": header or signature_header(secret, body, timestamp)}
    return client.post(f"{API}/webhooks/payments/mock_intl", data=body, content_type="application/json", **headers)


def intl_event(payment, event_type="payment.succeeded", event_id="evt-1", **over):
    data = {
        "event_id": event_id, "event_type": event_type, "session": payment.provider_ref, "amount": payment.charge_amount,
        "currency": payment.charge_currency, "merchant_ref": payment.merchant_ref,
    }  # fmt: skip
    data.update(over)
    return webhook_body(
        data["event_id"], data["event_type"], data["session"], data["amount"], data["currency"], data["merchant_ref"]
    )


def to_ready_to_ship(order, staff=None):
    """PAID -> PROCESSING -> READY_TO_SHIP through the real state machine."""
    for target in ("PROCESSING", "READY_TO_SHIP"):
        order = transitions.transition(order, target, actor_type=ActorType.STAFF, actor=staff)
    return order


def make_zone_method(code="intl", zone_code="eu", countries=("DE",), *, base=D("20000000"), per_kg=D("0"), **method_kw):
    from apps.shipping.models import ShippingMethod, ShippingRate, ShippingZone

    zone, _ = ShippingZone.objects.get_or_create(
        code=zone_code, defaults={"names": {"en": zone_code}, "countries": list(countries)}
    )
    method = ShippingMethod.objects.create(
        code=code, names={"fa": code, "en": code}, fee_irr=D("0"), kind="international", **method_kw
    )
    rate = ShippingRate.objects.create(
        method=method, zone=zone, base_fee_irr=base, per_kg_irr=per_kg, value_status="estimated"
    )
    return method, zone, rate


__all__ = ["API", "MockInternationalProvider", "D", "json", "settings", "time"]
