"""International orders: destination countries, customs status (estimated vs confirmed vs unknown), delivery wording."""

from decimal import Decimal as D

import pytest

from apps.orders.models import Checkout, Order
from apps.pricing.models import PriceQuoteComponent
from apps.shipping.models import CountryShippingPolicy
from tests.phase7_helpers import API, make_address, make_product, new_checkout, place_order, put_in_cart, setup_shop
from tests.phase8_helpers import make_zone_method

pytestmark = pytest.mark.django_db

GERMAN = {
    "country": "DE",
    "province": "Berlin",
    "city": "Berlin",
    "postal_code": "10115",
    "phone": "+4915112345678",
    "address_line1": "Unter den Linden 1",
}


def start(make_user, login, *, fulfillment="DIRECT_INVENTORY", method_kw=None):
    setup_shop()
    make_zone_method("intl", base=D("20000000"), **(method_kw or {}))
    user = make_user()
    client = login(user)
    spec = {"fulfillment": fulfillment, **({"stock": 5} if fulfillment == "DIRECT_INVENTORY" else {})}
    _, (variant,) = make_product(spec)
    put_in_cart(client, variant, 1)
    return client, user


def preview(client, address, method="intl", currency="IRT"):
    return client.post(
        f"{API}/checkout/preview",
        {"address_id": str(address.public_id), "shipping_method": method, "currency": currency},
        format="json",
    )


def test_domestic_checkout_reports_the_pricing_rules_customs_and_adds_no_new_warning(make_user, login):
    setup_shop()
    user = make_user()
    client = login(user)
    _, (variant,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 5})
    put_in_cart(client, variant, 1)
    body = preview(client, make_address(user), method="standard").json()
    # the shared fixture's published rule set charges a confirmed customs amount on every item
    assert (
        body["customs"]["status"] == "confirmed"
        and body["customs"]["basis"] == "pricing_rules"
        and body["customs"]["is_estimate"] is False
    )
    assert body["customs"]["amount"] == body["totals"]["customs_estimate"]
    assert body["delivery"] == {"status": "unknown", "min_days": None, "max_days": None, "unit": "business_days"}
    assert not [w for w in body["warnings"] if w.startswith("customs_")]


def test_customs_status_rules(settings):
    from apps.shipping.services.international import customs_info

    zero = D(0)
    assert customs_info("IR")["status"] == "not_applicable"  # domestic delivery, nothing configured
    assert (
        customs_info("IR", estimated_irr=D(5), confirmed_irr=D(7))["status"] == "estimated"
        and customs_info("IR", estimated_irr=D(5))["amount_irr"] == "5"
    )
    assert customs_info("IR", confirmed_irr=D(7))["status"] == "confirmed"
    for amount in (D(5), zero):
        info = customs_info(
            "DE", estimated_irr=amount, confirmed_irr=amount
        )  # Iranian import customs never masquerade as German duties
        assert info["status"] == "unknown" and info["amount_irr"] is None


def test_prepaid_duties_without_a_duty_source_stay_unknown(make_user, login):
    client, user = start(make_user, login)
    CountryShippingPolicy.objects.create(country="DE", customs_mode="prepaid")
    body = preview(client, make_address(user, **GERMAN)).json()
    assert (
        body["customs"]["status"] == "unknown"
        and body["customs"]["amount"] is None
        and "customs_unknown" in body["warnings"]
    )


def test_abroad_without_a_country_policy_is_unknown_never_zero(make_user, login):
    client, user = start(make_user, login)
    body = preview(client, make_address(user, **GERMAN)).json()
    assert (
        body["customs"]["status"] == "unknown"
        and body["customs"]["amount"] is None
        and "customs_unknown" in body["warnings"]
    )
    assert body["shipping"]["status"] == "estimated" and "shipping_fee_estimated" in body["warnings"]
    assert body["totals"]["shipping"]["amount"] != "0"  # the configured tariff, not an assumption
    assert body["taxes"] == {"status": "not_modeled"}


def test_duties_collected_on_delivery_are_flagged_as_not_included_and_not_estimated(make_user, login):
    client, user = start(make_user, login)
    CountryShippingPolicy.objects.create(
        country="DE",
        customs_mode="on_delivery",
        customs_notice={
            "en": "Import duties and VAT are collected by the carrier.",
            "fa": "حقوق گمرکی هنگام تحویل دریافت می‌شود.",
        },
        taxes_notice={"en": "VAT may apply on import."},
        policy_notes={"en": "Ships from Tehran with a signature on delivery."},
    )
    body = preview(client, make_address(user, **GERMAN)).json()
    customs = body["customs"]
    assert customs["status"] == "payable_on_delivery" and customs["amount"] is None and customs["is_estimate"] is False
    assert customs["notice"] == "Import duties and VAT are collected by the carrier." or customs["notice"].startswith(
        "حقوق"
    )
    assert customs["taxes_status"] == "see_notice" and "customs_payable_on_delivery" in body["warnings"]


def test_the_delivery_range_is_shown_only_when_configured_and_never_as_a_date(make_user, login):
    client, user = start(
        make_user,
        login,
        method_kw={"delivery_min_days": 7, "delivery_max_days": 14, "delivery_estimate_status": "estimated"},
    )
    body = preview(client, make_address(user, **GERMAN)).json()
    assert body["delivery"] == {"status": "estimated", "min_days": 7, "max_days": 14, "unit": "business_days"}
    assert "delivery_date" not in body and "delivered_by" not in body["delivery"]


def test_unsupported_destination_cannot_be_checked_out(make_user, login):
    client, user = start(make_user, login)
    response = preview(
        client, make_address(user, **{**GERMAN, "country": "JP", "postal_code": "1000001", "phone": "+81312345678"})
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "shipping_method_unavailable"


def test_blocked_country_is_refused_with_its_own_reason(make_user, login):
    client, user = start(make_user, login)
    CountryShippingPolicy.objects.create(country="DE", is_shippable=False)
    assert preview(client, make_address(user, **GERMAN)).json()["error"]["code"] == "shipping_country_not_shippable"


def test_incomplete_foreign_address_follows_the_configured_requirements(make_user, login):
    client, user = start(make_user, login)
    CountryShippingPolicy.objects.create(country="DE", address_requirements={"required": ["province"]})
    response = preview(client, make_address(user, **{**GERMAN, "province": ""}))
    assert response.status_code == 422 and response.json()["error"]["code"] == "address_incomplete"


def test_order_keeps_the_delivery_and_customs_wording_it_was_shown(make_user, login):
    client, user = start(
        make_user,
        login,
        method_kw={"delivery_min_days": 7, "delivery_max_days": 14, "delivery_estimate_status": "estimated"},
    )
    CountryShippingPolicy.objects.create(
        country="DE", customs_mode="on_delivery", customs_notice={"en": "Duties payable on delivery."}
    )
    checkout = new_checkout(client, make_address(user, **GERMAN), method="intl")
    created = place_order(client, checkout, key="intl-1", initiate_payment=False).json()
    order = Order.objects.get()
    assert order.shipping_snapshot["delivery"]["max_days"] == 14 and order.shipping_snapshot["zone"] == "eu"
    CountryShippingPolicy.objects.filter(country="DE").update(
        customs_mode="prepaid", customs_notice={"en": "changed later"}
    )
    detail = client.get(f"{API}/orders/{order.code}").json()
    assert (
        detail["delivery"]["estimate"]["min_days"] == 7
        and detail["delivery"]["customs"]["status"] == "payable_on_delivery"
    )
    assert "amount_irr" not in detail["delivery"]["customs"] and created


def test_customs_from_pricing_rules_keeps_estimates_and_confirmed_values_apart(make_user, login):
    setup_shop()
    user = make_user()
    client = login(user)
    _, (variant,) = make_product({"fulfillment": "PROXY_PURCHASE"})
    put_in_cart(client, variant, 1)
    checkout = new_checkout(client, make_address(user), method="standard")
    rows = PriceQuoteComponent.objects.filter(item__quote=Checkout.objects.get().quote, kind="customs")
    if not rows.exists():
        pytest.skip("the shared pricing fixture has no customs rule for this product")
    expected = "estimated" if rows.filter(value_status="estimated").exists() else "confirmed"
    snapshot = Checkout.objects.get().shipping_snapshot["customs"]
    assert snapshot["status"] == expected and D(snapshot["amount_irr"]) == Checkout.objects.get().customs_estimate_irr
    assert client.get(f"{API}/checkout/{checkout['id']}").json()["customs"]["is_estimate"] is (expected == "estimated")
