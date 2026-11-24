"""Builders for the Phase 7 tests (cart, wishlist, checkout, orders, refunds). All numbers are SYNTHETIC."""

import itertools
import json
from decimal import Decimal as D

from apps.accounts.models import Address
from apps.cart.models import Cart
from apps.inventory.services import stock
from apps.payments.providers.fake import sign
from apps.pricing.services import quotes  # noqa: F401
from apps.shipping.models import ShippingMethod
from tests import catalog_factories as f
from tests.pricing_helpers import SCENARIOS, fx, make_ruleset

API = "/api/v1"
_n = itertools.count(1)


def setup_shop(*, shipping=True):
    """Rates + a published ruleset + standard/express delivery (Toman-sized demo fees)."""
    fx()
    scenario = SCENARIOS["scenarios"][0]
    make_ruleset(rules=scenario["rules"], **scenario["ruleset"])
    if shipping:
        ShippingMethod.objects.create(
            code="standard", names={"fa": "استاندارد", "en": "Standard"}, fee_irr=D("4500000"), free_over_irr=D("200000000"),
            countries=["IR"], sort_order=10, value_status="confirmed",
        )
        ShippingMethod.objects.create(
            code="express", names={"fa": "سریع", "en": "Express"}, fee_irr=D("9500000"), countries=["IR"], sort_order=20,
            value_status="confirmed",
        )


def attach_source_price(variant, amount="1000", currency="TRY"):
    from apps.product_sources.models import ExternalProduct, ExternalProductVariant, SourcePrice

    n = next(_n)
    ext = ExternalProduct.objects.create(source=variant.source, external_id=f"E{n}", external_url=f"https://src.example/p/{n}", raw_title="x")
    ev = ExternalProductVariant.objects.create(
        external_product=ext, external_variant_id=f"EV{n}", sku=f"SRC-{n}", canonical_variant=variant, source_url=f"https://src.example/v/{n}"
    )
    sp = SourcePrice.objects.create(variant=ev, list_amount=D(str(amount)), currency_code=currency, value_hash=f"h{n}")
    ev.current_price = sp
    ev.save(update_fields=["current_price"])
    return ev


def make_product(*variants, **product_kw):
    """variants = dicts for catalog_factories.add_variant plus `amount`, `stock`. Returns (product, [variants])."""
    specs = [dict(v) for v in variants] or [{}]
    prepared = []
    for spec in specs:
        spec.setdefault("final", 1_000_000)
        fulfillment = spec.get("fulfillment", "PROXY_PURCHASE")
        spec.setdefault("source_code", "maral" if fulfillment == "DIRECT_INVENTORY" else "trendyol")
        prepared.append(spec)
    product = f.product(variants=[], **product_kw)
    out = []
    for spec in prepared:
        amount, qty = spec.pop("amount", "1000"), spec.pop("stock", None)
        f.add_variant(product, **spec)
        variant = product.variants.order_by("-id").first()
        attach_source_price(variant, amount)
        if qty is not None:
            stock.receive(variant, qty, reason="test")
        out.append(variant)
    from apps.catalog.services import projection

    projection.refresh_projection(product.pk)
    return product, out


def make_address(user, **kw):
    data = {
        "recipient_name": "سارا محمدی", "phone": "+989121234567", "country": "IR", "province": "تهران", "city": "تهران",
        "address_line1": "ولیعصر، پلاک ۱۲", "postal_code": "1234567890",
    }  # fmt: skip
    return Address.objects.create(user=user, **{**data, **kw})


def put_in_cart(client, variant, quantity=1):
    response = client.post(f"{API}/cart/items", {"variant_id": str(variant.public_id), "quantity": quantity}, format="json")
    assert response.status_code == 201, response.content
    return response.json()


def checkout_body(address, method="standard", currency="IRT"):
    return {"address_id": str(address.public_id), "shipping_method": method, "currency": currency}


def new_checkout(client, address, **kw):
    response = client.post(f"{API}/checkout", checkout_body(address, **kw), format="json")
    assert response.status_code == 201, response.content
    return response.json()


def place_order(client, checkout, key="key-1", **extra):
    return client.post(
        f"{API}/orders", {"checkout_id": checkout["id"], **extra}, format="json", HTTP_IDEMPOTENCY_KEY=key
    )


def webhook(client, payment, status="captured", amount=None, event_id=None):
    body = json.dumps(
        {
            "event_id": event_id or f"evt-{next(_n)}",
            "provider_ref": payment.provider_ref,
            "status": status,
            "amount_irr": str(amount if amount is not None else payment.amount_irr),
        }
    ).encode()
    return client.post(f"{API}/webhooks/payments/fake", data=body, content_type="application/json", HTTP_X_SIGNATURE=sign(body))


def pay(order_response_json, client=None):
    """Capture the payment of a created order through a signed webhook (what the provider would do)."""
    from rest_framework.test import APIClient

    from apps.payments.models import Payment

    payment = Payment.objects.get(public_id=order_response_json["payment"]["id"])
    response = webhook(client or APIClient(), payment, amount=payment.amount_irr)
    assert response.status_code == 200, response.content
    return response.json()["status"]
