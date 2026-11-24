"""Checkout and order creation: server-side prices, direct vs proxy, snapshots, idempotency, races, expiry."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.cart.models import Cart
from apps.catalog.models import VariantAvailability, VariantPrice
from apps.inventory.models import InventoryItem, InventoryReservation
from apps.orders.models import Checkout, Order, OrderItem, OrderStatusHistory, ProcurementRecord
from apps.pricing.models import PriceQuote
from tests.phase7_helpers import API, checkout_body, make_address, make_product, new_checkout, pay, place_order, put_in_cart, setup_shop
from tests.pricing_helpers import rate

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop():
    setup_shop()


@pytest.fixture
def buyer(make_user, login):
    user = make_user()
    return user, login(user), make_address(user)


# ------------------------------------------------------------------------------------------ checkout
def test_preview_matches_checkout_exactly_and_saves_nothing(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({}, )
    put_in_cart(client, v, 2)
    quotes_before = PriceQuote.objects.count()
    preview = client.post(f"{API}/checkout/preview", checkout_body(address), format="json").json()
    assert preview["preview"] is True and "id" not in preview
    assert PriceQuote.objects.count() == quotes_before and Checkout.objects.count() == 0
    real = new_checkout(client, address)
    assert real["totals"] == preview["totals"] and real["lines"] == preview["lines"]
    t = real["totals"]
    assert D(t["total_irr"]) == D(t["merchandise"]["amount"]) * 10 + D(t["separate_charges"]["amount"]) * 10 + D(t["shipping"]["amount"]) * 10
    assert real["taxes"] == {"status": "not_modeled"} and "taxes_not_modeled" in real["warnings"]


def test_client_cannot_inject_prices_or_shipping(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({})
    put_in_cart(client, v)
    honest = new_checkout(client, address)
    body = {**checkout_body(address), "total": 1, "shipping_fee": 0, "unit_price": 1, "discount": 99999999, "status": "PAID"}
    r = client.post(f"{API}/checkout", body, format="json")
    assert r.status_code == 201 and r.json()["totals"] == honest["totals"]
    wrong = place_order(client, r.json(), accepted_total_irr="1")
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "total_mismatch"
    assert Order.objects.count() == 0


def test_address_and_method_validation(shop, buyer, make_user):
    user, client, address = buyer
    _, (v,) = make_product({})
    put_in_cart(client, v)
    other = make_address(make_user())
    assert client.post(f"{API}/checkout", checkout_body(other), format="json").status_code == 404  # not yours = not found
    incomplete = make_address(user, postal_code="")
    r = client.post(f"{API}/checkout", checkout_body(incomplete), format="json")
    assert r.json()["error"]["code"] == "address_incomplete"
    assert client.post(f"{API}/checkout", checkout_body(address, method="drone"), format="json").json()["error"]["code"] == "shipping_method_unavailable"
    abroad = make_address(user, country="DE", postal_code="10115")
    assert client.post(f"{API}/checkout", checkout_body(abroad), format="json").json()["error"]["code"] == "shipping_method_unavailable"


def test_empty_cart_and_unavailable_items_stop_checkout(shop, buyer):
    user, client, address = buyer
    assert client.post(f"{API}/checkout", checkout_body(address), format="json").json()["error"]["code"] == "cart_empty"
    _, (v,) = make_product({})
    put_in_cart(client, v)
    VariantAvailability.objects.filter(variant=v).update(status="out_of_stock")
    r = client.post(f"{API}/checkout", checkout_body(address), format="json")
    assert r.json()["error"]["code"] == "cart_invalid"
    assert Checkout.objects.count() == 0


def test_unauthenticated_cannot_checkout(shop):
    assert APIClient().post(f"{API}/checkout", {}, format="json").status_code in (401, 403)
    assert APIClient().post(f"{API}/orders", {}, format="json").status_code in (401, 403)


# ------------------------------------------------------------------------------------------ direct inventory
def test_direct_inventory_purchase_reserves_then_commits_stock(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 5})
    put_in_cart(client, v, 2)
    r = place_order(client, new_checkout(client, address))
    assert r.status_code == 201, r.content
    body = r.json()
    assert [o["order_type"] for o in body["orders"]] == ["DIRECT_INVENTORY"] and body["orders"][0]["status"] == "PENDING_PAYMENT"
    item = InventoryItem.objects.get(variant=v)
    assert (item.on_hand, item.reserved, item.sellable) == (5, 2, 3)  # held, not yet sold
    assert InventoryReservation.objects.get().status == "active"
    assert pay(body) == "applied"
    item.refresh_from_db()
    assert (item.on_hand, item.reserved) == (3, 0)  # sold
    order = Order.objects.get()
    assert order.status == "PAID" and order.paid_at is not None
    assert [h.to_status for h in order.history.all()] == ["PENDING_PAYMENT", "PAID"]
    assert Cart.objects.get(user=user, status="converted") and client.get(f"{API}/cart").json()["items"] == []  # cart spent


def test_direct_order_refuses_without_internal_stock(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 1})
    put_in_cart(client, v, 1)
    checkout = new_checkout(client, address)
    InventoryItem.objects.filter(variant=v).update(on_hand=0)  # sold elsewhere meanwhile
    VariantAvailability.objects.filter(variant=v).update(status="out_of_stock")
    r = place_order(client, checkout)
    assert r.status_code == 409 and r.json()["error"]["code"] == "items_unavailable"
    assert Order.objects.count() == 0 and InventoryReservation.objects.count() == 0
    assert Checkout.objects.get().status == "open"  # nothing consumed; the customer can adjust and retry


def test_last_unit_goes_to_one_buyer_only(shop, make_user, login):
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 1})
    users = [make_user(), make_user()]
    clients = [login(u) for u in users]
    checkouts = []
    for u, c in zip(users, clients):
        put_in_cart(c, v, 1)
        checkouts.append(new_checkout(c, make_address(u)))
    first = place_order(clients[0], checkouts[0])
    second = place_order(clients[1], checkouts[1])
    assert first.status_code == 201
    assert second.status_code == 409 and second.json()["error"]["code"] == "items_unavailable"
    assert InventoryItem.objects.get(variant=v).reserved == 1


# ------------------------------------------------------------------------------------------ proxy purchase
def test_proxy_purchase_preserves_source_quote_requirements_and_delivery(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({"size": "m", "color": "red", "amount": "1000"})
    put_in_cart(client, v, 2)
    checkout = new_checkout(client, address)
    r = place_order(client, checkout)
    assert r.status_code == 201
    order = Order.objects.get()
    item = order.items.get()
    assert order.order_type == "PROXY_PURCHASE"
    assert item.source_code == "trendyol" and item.source_product_id.startswith("E") and item.source_variant_id.startswith("EV")
    assert item.source_product_url.startswith("https://src.example/")
    assert (item.original_amount, item.original_currency) == (D("1000"), "TRY") and item.exchange_rate == D("15000")
    assert item.exchange_rate_source and item.pricing_rule_version >= 1
    assert item.purchase_requirements["max_source_amount"] == "1000.0000" and item.purchase_requirements["size"] == "m"
    assert item.purchase_requirements["quantity"] == 2 and item.purchase_requirements["on_price_increase"] == "hold_and_contact_customer"
    assert item.quote_item.quote_id == order.quote_id and order.quote.status == "locked"  # the accepted quote is frozen
    assert item.unit_price_irr == item.quote_item.unit_price_irr and item.line_price_irr == item.unit_price_irr * 2
    assert ProcurementRecord.objects.get(order_item=item).status == "awaiting_payment"
    assert not InventoryItem.objects.exists() and not InventoryReservation.objects.exists()  # proxy never touches stock


def test_mixed_cart_splits_into_two_orders_and_every_rial_is_accounted_for(shop, buyer):
    user, client, address = buyer
    _, (direct,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 4})
    _, (proxy,) = make_product({})
    put_in_cart(client, direct, 1)
    put_in_cart(client, proxy, 1)
    checkout_json = new_checkout(client, address)
    r = place_order(client, checkout_json)
    assert r.status_code == 201 and len(r.json()["orders"]) == 2
    orders = list(Order.objects.order_by("id"))
    assert [o.order_type for o in orders] == ["DIRECT_INVENTORY", "PROXY_PURCHASE"]
    checkout = Checkout.objects.get()
    assert sum(o.total_irr for o in orders) == checkout.total_irr
    assert sum(o.shipping_irr for o in orders) == checkout.shipping_fee_irr  # delivery fee split, never doubled
    assert len({o.code for o in orders}) == 2 and all(o.code.startswith("MRL-") for o in orders)
    assert pay(r.json()) == "applied" and {o.status for o in Order.objects.all()} == {"PAID"}  # one payment covers both


# ------------------------------------------------------------------------------------------ idempotency & duplicates
def test_same_key_replays_without_a_second_order(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 5})
    put_in_cart(client, v)
    checkout = new_checkout(client, address)
    a = place_order(client, checkout, key="same")
    b = place_order(client, checkout, key="same")
    assert a.status_code == 201 and b.status_code == 200 and b["Idempotent-Replay"] == "true"
    assert a.json()["orders"] == b.json()["orders"] and Order.objects.count() == 1
    assert InventoryItem.objects.get(variant=v).reserved == 1  # reserved once
    assert a.json()["payment"]["id"] == b.json()["payment"]["id"]


def test_same_key_different_request_is_rejected(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({})
    put_in_cart(client, v)
    checkout = new_checkout(client, address)
    place_order(client, checkout, key="k")
    r = place_order(client, checkout, key="k", accepted_total_irr=str(Checkout.objects.get().total_irr))
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"


def test_double_submission_with_different_keys_creates_one_order(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({})
    put_in_cart(client, v)
    checkout = new_checkout(client, address)
    assert place_order(client, checkout, key="k1").status_code == 201
    r = place_order(client, checkout, key="k2")
    assert r.status_code == 409 and r.json()["error"]["code"] == "checkout_already_completed"
    assert Order.objects.count() == 1


def test_idempotency_key_is_required_and_scoped_to_the_user(shop, buyer, make_user, login):
    user, client, address = buyer
    _, (v,) = make_product({})
    put_in_cart(client, v)
    checkout = new_checkout(client, address)
    r = client.post(f"{API}/orders", {"checkout_id": checkout["id"]}, format="json")
    assert r.json()["error"]["code"] == "idempotency_key_required"
    stranger = login(make_user())
    assert place_order(stranger, checkout).status_code == 404  # someone else's checkout does not exist for you


def test_cart_changed_after_checkout_is_refused(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({})
    body = put_in_cart(client, v)
    checkout = new_checkout(client, address)
    client.patch(f"{API}/cart/items/{body['items'][0]['line_id']}", {"quantity": 3}, format="json")
    r = place_order(client, checkout)
    assert r.status_code == 409 and r.json()["error"]["code"] == "cart_changed" and Order.objects.count() == 0


# ------------------------------------------------------------------------------------------ prices over time
def test_exchange_rate_change_during_checkout_does_not_change_the_accepted_price(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({"amount": "1000"})
    put_in_cart(client, v)
    checkout = new_checkout(client, address)
    accepted = D(checkout["totals"]["total_irr"])
    rate("TRY", 30000)  # the lira doubles while the customer is on the payment page
    r = place_order(client, checkout)
    assert r.status_code == 201
    order = Order.objects.get()
    assert order.total_irr == accepted and order.items.get().exchange_rate == D("15000")
    from apps.cart.services import identity

    put_in_cart(client, v)
    fresh = new_checkout(client, address)  # a NEW checkout prices at the new rate
    assert D(fresh["totals"]["total_irr"]) > accepted


def test_expired_quote_cannot_become_an_order(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 2})
    put_in_cart(client, v)
    checkout = new_checkout(client, address)
    past = timezone.now() - timedelta(minutes=1)
    Checkout.objects.update(expires_at=past)
    PriceQuote.objects.update(created_at=past - timedelta(minutes=5), expires_at=past)
    r = place_order(client, checkout)
    assert r.status_code == 409 and r.json()["error"]["code"] == "quote_expired"
    assert Order.objects.count() == 0 and InventoryReservation.objects.count() == 0
    from apps.orders.services import lifecycle

    assert lifecycle.expire_checkouts() == 1 and Checkout.objects.get().status == "expired"


def test_order_snapshot_is_immune_to_later_catalog_changes(shop, buyer):
    user, client, address = buyer
    product, (v,) = make_product({"size": "m", "amount": "1000", "list_price": 1_500_000})
    put_in_cart(client, v, 2)
    place_order(client, new_checkout(client, address))
    order = Order.objects.get()
    before = client.get(f"{API}/orders/{order.public_id}").json()
    VariantPrice.objects.filter(variant=v).update(final_irr=D("1"), list_irr=D("2"))
    from apps.product_sources.models import SourcePrice

    SourcePrice.objects.update(list_amount=D("1"))
    rate("TRY", 99999)
    product.translations.update(name="نام تازه")
    after = client.get(f"{API}/orders/{order.public_id}").json()
    assert before["items"] == after["items"] and before["totals"] == after["totals"]
    assert after["items"][0]["product_name"] != "نام تازه"
    item = order.items.get()
    assert item.discount_unit_irr == max(item.list_unit_irr - item.unit_price_irr, 0)


def test_snapshot_rows_are_immutable(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({})
    put_in_cart(client, v)
    place_order(client, new_checkout(client, address))
    order, item, checkout = Order.objects.get(), OrderItem.objects.get(), Checkout.objects.get()
    for obj, field in ((order, "total_irr"), (item, "unit_price_irr"), (checkout, "total_irr")):
        setattr(obj, field, D("1"))
        with pytest.raises(RuntimeError):
            obj.save()
    order.refresh_from_db()
    order.status = "DELIVERED"
    with pytest.raises(RuntimeError):
        order.save(update_fields=["status"])  # status has one door: services.transitions
    for obj in (order, item, checkout, order.history.first()):
        with pytest.raises(RuntimeError):
            obj.delete()
    with pytest.raises(RuntimeError):
        h = order.history.first()
        h.reason = "x"
        h.save()


def test_customer_view_hides_internal_cost_breakdown_unless_the_owner_allows_it(shop, buyer):
    from apps.pricing.models import PricingRuleSet

    user, client, address = buyer
    _, (v,) = make_product({})
    put_in_cart(client, v)
    r = place_order(client, new_checkout(client, address))
    oid = r.json()["orders"][0]["id"]
    item = client.get(f"{API}/orders/{oid}").json()["items"][0]
    assert "source_price" not in item and "fees" not in item and "customs" not in item and "profit" not in str(item)
    PricingRuleSet.objects.update(customer_breakdown="summary")
    item = client.get(f"{API}/orders/{oid}").json()["items"][0]
    assert item["source_price"]["currency"] == "TRY" and "fees" not in item


# ------------------------------------------------------------------------------------------ ownership
def test_orders_are_only_visible_to_their_owner(shop, buyer, make_user, login):
    user, client, address = buyer
    _, (v,) = make_product({})
    put_in_cart(client, v)
    r = place_order(client, new_checkout(client, address))
    oid, code = r.json()["orders"][0]["id"], r.json()["orders"][0]["code"]
    stranger = login(make_user())
    for path in ("", "/tracking", "/history", "/refunds"):
        assert stranger.get(f"{API}/orders/{oid}{path}").status_code == 404
    assert stranger.get(f"{API}/orders/{code}").status_code == 404
    assert stranger.post(f"{API}/orders/{oid}/cancel", {}, format="json").status_code == 404
    assert stranger.post(f"{API}/orders/{oid}/refunds", {"items": [], "reason": "x"}, format="json").status_code in (404, 400, 422)
    assert stranger.get(f"{API}/orders").json()["results"] == []
    assert client.get(f"{API}/orders/{code}").status_code == 200  # the tracking code works for the owner
    assert [o["id"] for o in client.get(f"{API}/orders").json()["results"]] == [oid]


def test_tracking_and_history_are_customer_safe(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({})
    put_in_cart(client, v)
    r = place_order(client, new_checkout(client, address))
    oid = r.json()["orders"][0]["id"]
    pay(r.json())
    tracking = client.get(f"{API}/orders/{oid}/tracking").json()
    assert tracking["status"] == "PAID" and [s["status"] for s in tracking["steps"]][:3] == ["PENDING_PAYMENT", "PAID", "PROCESSING"]
    assert tracking["steps"][0]["done"] and tracking["steps"][1]["current"] and tracking["estimated_delivery"] is None or tracking["estimated_delivery"]
    history = client.get(f"{API}/orders/{oid}/history").json()["results"]
    assert [h["status"] for h in history] == ["PENDING_PAYMENT", "PAID"]
    assert set(history[0]) == {"status", "label", "at"}  # no staff ids, no internal reasons
