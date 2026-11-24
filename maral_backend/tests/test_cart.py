"""Guest + customer carts, variant selection, availability, server-side prices, merge after login."""

import hashlib

import pytest
from rest_framework.test import APIClient

from apps.cart.models import Cart
from tests.phase7_helpers import API, make_product, put_in_cart, setup_shop

pytestmark = pytest.mark.django_db


def guest_client():
    client = APIClient()
    token = client.post(f"{API}/cart").json()["guest_token"]
    client.defaults["HTTP_X_GUEST_TOKEN"] = token
    return client, token


@pytest.fixture
def shop():
    setup_shop()


def test_guest_cart_token_is_random_and_stored_hashed():
    client = APIClient()
    r1, r2 = client.post(f"{API}/cart"), client.post(f"{API}/cart")
    t1, t2 = r1.json()["guest_token"], r2.json()["guest_token"]
    assert r1.status_code == 201 and len(t1) >= 43 and t1 != t2
    cart = Cart.objects.get(public_id=r1.json()["id"])
    assert cart.guest_token_hash == hashlib.sha256(t1.encode()).hexdigest() and t1 not in cart.guest_token_hash
    assert "guest_token" not in client.get(f"{API}/cart", HTTP_X_GUEST_TOKEN=t1).json()  # shown once only


def test_guest_needs_the_token_and_a_wrong_one_finds_nothing(shop):
    client, token = guest_client()
    assert APIClient().get(f"{API}/cart").status_code == 422 or APIClient().get(f"{API}/cart").json()["error"]["code"] == "cart_not_found"
    assert APIClient().get(f"{API}/cart", HTTP_X_GUEST_TOKEN="x" * 43).json()["error"]["code"] == "cart_not_found"
    assert client.get(f"{API}/cart").status_code == 200


def test_add_by_variant_and_by_product_size_color_and_prices_come_from_the_server(shop):
    product, (a, b) = make_product({"size": "m", "color": "red", "final": 1_000_000, "list_price": 1_250_000}, {"size": "l", "color": "red", "final": 1_100_000})
    client, _ = guest_client()
    r = client.post(f"{API}/cart/items", {"variant_id": str(a.public_id), "quantity": 2, "unit_price": 1, "total": 1, "price": "1"}, format="json")
    assert r.status_code == 201
    body = r.json()
    assert body["items"][0]["unit_price"] == {"amount": "1000000", "currency": "IRT"}  # client price ignored
    assert body["totals"]["subtotal"]["amount"] == "2000000" and body["totals"]["discount"]["amount"] == "500000"
    r = client.post(f"{API}/cart/items", {"product_id": str(product.public_id), "size": "l", "color": "red"}, format="json")
    assert r.status_code == 201 and len(r.json()["items"]) == 2
    assert {i["size"]["code"] for i in r.json()["items"]} == {"m", "l"}


def test_selection_errors(shop):
    product, _ = make_product({"size": "m", "color": "red"}, {"size": "l", "color": "red"})
    client, _ = guest_client()
    r = client.post(f"{API}/cart/items", {"product_id": str(product.public_id), "color": "red"}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "selection_incomplete"
    r = client.post(f"{API}/cart/items", {"product_id": str(product.public_id), "size": "xl", "color": "red"}, format="json")
    assert r.json()["error"]["code"] == "variant_not_found"


def test_quantity_update_remove_and_variant_switch_merges(shop):
    _, (m, l) = make_product({"size": "m", "color": "red"}, {"size": "l", "color": "red"})
    client, _ = guest_client()
    put_in_cart(client, m, 1)
    body = put_in_cart(client, l, 2)
    line_m = next(i for i in body["items"] if i["size"]["code"] == "m")["line_id"]
    body = client.patch(f"{API}/cart/items/{line_m}", {"quantity": 3}, format="json").json()
    assert next(i for i in body["items"] if i["size"]["code"] == "m")["quantity"] == 3
    body = client.patch(f"{API}/cart/items/{line_m}", {"size": "l"}, format="json").json()  # select another size
    assert len(body["items"]) == 1 and body["items"][0]["quantity"] == 5 and body["items"][0]["size"]["code"] == "l"
    line = body["items"][0]["line_id"]
    assert client.delete(f"{API}/cart/items/{line}").json()["items"] == []
    assert client.patch(f"{API}/cart/items/{line}", {"quantity": 1}, format="json").json()["error"]["code"] == "line_not_found"


def test_availability_is_validated_for_proxy_and_direct(shop):
    _, (gone,) = make_product({"available": False})
    _, (direct,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 3})
    client, _ = guest_client()
    r = client.post(f"{API}/cart/items", {"variant_id": str(gone.public_id)}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    assert client.post(f"{API}/cart/items", {"variant_id": str(direct.public_id), "quantity": 4}, format="json").json()["error"]["code"] == "insufficient_stock"
    assert client.post(f"{API}/cart/items", {"variant_id": str(direct.public_id), "quantity": 3}, format="json").status_code == 201


def test_cart_shows_when_an_item_becomes_unavailable_and_blocks_checkout_readiness(shop):
    from apps.catalog.models import VariantAvailability

    _, (v,) = make_product({})
    client, _ = guest_client()
    assert put_in_cart(client, v)["checkout_ready"] is True
    VariantAvailability.objects.filter(variant=v).update(status="out_of_stock")
    body = client.get(f"{API}/cart").json()
    assert body["checkout_ready"] is False and body["items"][0]["issues"][0]["code"] == "out_of_stock"
    assert client.post(f"{API}/cart/validate").json()["valid"] is False


def test_shipping_estimate_never_zero_when_unconfigured_and_free_over_threshold():
    from tests.phase7_helpers import setup_shop as s

    s(shipping=False)
    _, (v,) = make_product({})
    client, _ = guest_client()
    body = put_in_cart(client, v)
    assert body["totals"]["shipping_estimate"] is None and "shipping_not_configured" in body["warnings"] and body["totals"]["total_is_partial"]
    from decimal import Decimal

    from apps.shipping.models import ShippingMethod

    ShippingMethod.objects.create(code="standard", names={}, fee_irr=Decimal("4500000"), free_over_irr=Decimal("15000000"), countries=["IR"])
    est = client.get(f"{API}/cart").json()["totals"]["shipping_estimate"]
    assert est["amount"]["amount"] == "450000" and not est["is_free"]
    client.patch(f"{API}/cart/items/{client.get(f'{API}/cart').json()['items'][0]['line_id']}", {"quantity": 2}, format="json")
    # 2 x 1,000,000 Toman = 2,000,000 Toman = 20,000,000 IRR >= 15,000,000 IRR threshold
    assert client.get(f"{API}/cart").json()["totals"]["shipping_estimate"]["is_free"] is True


def test_customer_cart_is_private_per_user(shop, make_user, login):
    _, (v,) = make_product({})
    a, b = login(make_user()), login(make_user())
    put_in_cart(a, v)
    assert len(a.get(f"{API}/cart").json()["items"]) == 1
    assert b.get(f"{API}/cart").json()["items"] == []  # b never sees a's cart, whatever ids they guess


def test_merge_guest_cart_into_customer_cart_sums_and_is_idempotent(shop, make_user, login):
    _, (m, l) = make_product({"size": "m", "color": "red"}, {"size": "l", "color": "red"})
    guest, token = guest_client()
    put_in_cart(guest, m, 2)
    put_in_cart(guest, l, 1)
    customer = login(make_user())
    put_in_cart(customer, m, 1)
    r = customer.post(f"{API}/cart/merge", HTTP_X_GUEST_TOKEN=token)
    assert r.status_code == 200
    by_size = {i["size"]["code"]: i["quantity"] for i in r.json()["items"]}
    assert by_size == {"m": 3, "l": 1}
    again = customer.post(f"{API}/cart/merge", HTTP_X_GUEST_TOKEN=token)
    assert again.status_code == 200 and {i["size"]["code"]: i["quantity"] for i in again.json()["items"]} == by_size  # no double count
    assert APIClient().get(f"{API}/cart", HTTP_X_GUEST_TOKEN=token).json()["error"]["code"] == "cart_not_found"  # token is dead


def test_merge_requires_login_and_token(shop, make_user, login):
    _, token = guest_client()
    assert APIClient().post(f"{API}/cart/merge", HTTP_X_GUEST_TOKEN=token).status_code in (401, 403, 422)
    customer = login(make_user())
    assert customer.post(f"{API}/cart/merge").json()["error"]["code"] == "guest_token_required"
    assert customer.post(f"{API}/cart/merge", HTTP_X_GUEST_TOKEN="y" * 43).json()["error"]["code"] == "cart_not_found"


def test_merge_caps_quantity(shop, make_user, login, settings):
    settings.CART_MAX_QUANTITY_PER_LINE = 4
    _, (v,) = make_product({})
    guest, token = guest_client()
    put_in_cart(guest, v, 3)
    customer = login(make_user())
    put_in_cart(customer, v, 3)
    r = customer.post(f"{API}/cart/merge", HTTP_X_GUEST_TOKEN=token).json()
    assert r["items"][0]["quantity"] == 4 and r["merge"]["adjusted"][0]["reason"] == "max_quantity"
