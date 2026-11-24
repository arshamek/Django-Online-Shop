"""State machine, staff transitions, cancellation rules and partial refunds."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.exceptions import Conflict
from apps.inventory.models import InventoryItem
from apps.orders import state
from apps.orders.models import ActorType, Order, Refund
from apps.orders.services import lifecycle, refunds, transitions
from apps.orders.state import OrderStatus as S
from apps.orders.state import OrderType
from tests.phase7_helpers import API, make_address, make_product, new_checkout, pay, place_order, put_in_cart, setup_shop

pytestmark = pytest.mark.django_db
ALL = [s.value for s in S]


@pytest.fixture
def shop():
    setup_shop()


@pytest.fixture
def buyer(make_user, login):
    user = make_user()
    return user, login(user), make_address(user)


@pytest.fixture
def order_manager(make_staff, login):
    return login(make_staff(role="Order Manager"))


@pytest.fixture
def finance(make_staff, login):
    return login(make_staff(role="Finance Manager"))


def buy(client, address, variants, qty=1, pay_now=True):
    """-> (order, response json). `variants` may be one variant or a list."""
    for v in variants if isinstance(variants, (list, tuple)) else [variants]:
        put_in_cart(client, v, qty)
    r = place_order(client, new_checkout(client, address), key=f"k-{timezone.now().timestamp()}")
    assert r.status_code == 201, r.content
    if pay_now:
        pay(r.json())
    return Order.objects.get(public_id=r.json()["orders"][0]["id"]), r.json()


def go(order, *path, actor=ActorType.SYSTEM, **meta):
    for status in path:
        order = transitions.transition(order, status, actor_type=actor, metadata=meta if status in (S.PURCHASED, S.SHIPPED) else None)
    return order


def deliver(order):
    if order.order_type == OrderType.DIRECT_INVENTORY:
        return go(order, S.PROCESSING, S.READY_TO_SHIP, S.SHIPPED, S.DELIVERED)
    return go(order, S.PROCESSING, S.PURCHASE_PENDING, S.PURCHASED, S.IN_TRANSIT, S.READY_TO_SHIP, S.SHIPPED, S.DELIVERED, source_order_ref="TY-1")


# ------------------------------------------------------------------------------------------ the table itself
def test_table_is_closed_and_refunded_is_terminal():
    assert set(state.TRANSITIONS) == set(ALL)
    assert all(t in ALL for targets in state.TRANSITIONS.values() for t in targets)
    assert state.TERMINAL == {S.REFUNDED}


@pytest.mark.parametrize("order_type", [OrderType.DIRECT_INVENTORY, OrderType.PROXY_PURCHASE])
def test_every_status_is_reachable_for_each_order_type(order_type):
    seen, frontier = {S.PENDING_PAYMENT}, [S.PENDING_PAYMENT]
    while frontier:
        for nxt in state.allowed_next(order_type, frontier.pop()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    expected = set(ALL) - (state.PROXY_ONLY if order_type == OrderType.DIRECT_INVENTORY else set())
    assert seen == expected


def test_business_model_specific_edges():
    assert not state.can_transition(OrderType.DIRECT_INVENTORY, S.PROCESSING, S.PURCHASE_PENDING)
    assert state.can_transition(OrderType.DIRECT_INVENTORY, S.PROCESSING, S.READY_TO_SHIP)
    assert not state.can_transition(OrderType.PROXY_PURCHASE, S.PROCESSING, S.READY_TO_SHIP)  # must be bought first
    assert state.can_transition(OrderType.PROXY_PURCHASE, S.IN_TRANSIT, S.READY_TO_SHIP)  # customs step may be skipped
    for bad in ((S.PAID, S.SHIPPED), (S.PENDING_PAYMENT, S.DELIVERED), (S.SHIPPED, S.PAID), (S.DELIVERED, S.PROCESSING), (S.REFUNDED, S.PAID), (S.CANCELLED, S.PAID), (S.PURCHASED, S.CANCELLED)):
        for t in (OrderType.DIRECT_INVENTORY, OrderType.PROXY_PURCHASE):
            assert not state.can_transition(t, *bad), bad


def test_invalid_change_is_refused_and_leaves_no_trace(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({})
    order, _ = buy(client, address, v)
    with pytest.raises(Conflict) as e:
        transitions.transition(order, S.SHIPPED, actor_type=ActorType.SYSTEM)
    assert e.value.get_codes() == "invalid_transition"
    order.refresh_from_db()
    assert order.status == S.PAID and order.history.count() == 2


# ------------------------------------------------------------------------------------------ staff API
def test_full_proxy_lifecycle_through_the_staff_api_records_every_step(shop, buyer, order_manager):
    user, client, address = buyer
    _, (v,) = make_product({})
    order, _ = buy(client, address, v)
    url = f"{API}/staff/orders/{order.public_id}/transition"
    step = lambda status, **kw: order_manager.post(url, {"to_status": status, **kw}, format="json")  # noqa: E731
    assert step("PROCESSING").status_code == 200
    assert step("PURCHASE_PENDING").status_code == 200
    r = step("PURCHASED")  # buying is not complete without the source store's order number
    assert r.status_code == 422 and r.json()["error"]["code"] == "source_order_ref_required"
    assert step("PURCHASED", source_order_ref="TY-998877").status_code == 200
    for status in ("IN_TRANSIT", "CUSTOMS_PROCESSING", "READY_TO_SHIP"):
        assert step(status).status_code == 200
    r = step("SHIPPED", tracking_carrier="Post", tracking_number="RX123")
    assert r.status_code == 200 and step("DELIVERED").status_code == 200
    order.refresh_from_db()
    assert order.status == S.DELIVERED and order.tracking_number == "RX123" and order.delivered_at and order.shipped_at
    assert [h.to_status for h in order.history.all()] == [
        "PENDING_PAYMENT", "PAID", "PROCESSING", "PURCHASE_PENDING", "PURCHASED", "IN_TRANSIT", "CUSTOMS_PROCESSING", "READY_TO_SHIP", "SHIPPED", "DELIVERED",
    ]  # fmt: skip
    assert order.items.get().procurement.source_order_ref == "TY-998877" and order.items.get().procurement.status == "purchased"
    assert {h.actor_type for h in order.history.all()} == {"customer", "payment", "staff"}
    assert client.get(f"{API}/orders/{order.public_id}/tracking").json()["steps"][-1]["current"] is True


def test_staff_cannot_jump_or_forge_payment_or_refund_states(shop, buyer, order_manager):
    user, client, address = buyer
    _, (v,) = make_product({})
    order, _ = buy(client, address, v)
    url = f"{API}/staff/orders/{order.public_id}/transition"
    r = order_manager.post(url, {"to_status": "SHIPPED"}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"
    assert order_manager.post(url, {"to_status": "REFUNDED"}, format="json").json()["error"]["code"] == "use_refund_workflow"
    unpaid, _ = buy(client, address, make_product({})[1][0], pay_now=False)
    r = order_manager.post(f"{API}/staff/orders/{unpaid.public_id}/transition", {"to_status": "PAID"}, format="json")
    assert r.json()["error"]["code"] == "use_payment_flow"
    unpaid.refresh_from_db()
    assert unpaid.status == S.PENDING_PAYMENT


def test_customers_cannot_touch_staff_endpoints_or_set_status(shop, buyer, make_staff, login):
    user, client, address = buyer
    _, (v,) = make_product({})
    order, _ = buy(client, address, v)
    assert client.post(f"{API}/staff/orders/{order.public_id}/transition", {"to_status": "SHIPPED"}, format="json").status_code == 403
    assert client.patch(f"{API}/orders/{order.public_id}", {"status": "DELIVERED"}, format="json").status_code == 405
    support = login(make_staff(role="Customer Support"))
    assert support.get(f"{API}/staff/orders/{order.public_id}").status_code == 200  # may look
    assert support.post(f"{API}/staff/orders/{order.public_id}/transition", {"to_status": "PROCESSING"}, format="json").status_code == 403


# ------------------------------------------------------------------------------------------ cancellation
def test_cancel_before_payment_releases_the_stock(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 3})
    order, _ = buy(client, address, v, 2, pay_now=False)
    assert InventoryItem.objects.get(variant=v).reserved == 2
    r = client.post(f"{API}/orders/{order.public_id}/cancel", {"reason": "changed my mind"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED" and r.json()["actions"]["can_cancel"] is False
    assert (InventoryItem.objects.get(variant=v).reserved, InventoryItem.objects.get(variant=v).sellable) == (0, 3)
    assert not Refund.objects.exists()  # nothing was paid, nothing to give back


def test_cancel_after_payment_returns_stock_and_opens_a_full_refund(shop, buyer, finance):
    user, client, address = buyer
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 3})
    order, _ = buy(client, address, v, 2)
    assert InventoryItem.objects.get(variant=v).on_hand == 1
    r = client.post(f"{API}/orders/{order.public_id}/cancel", {}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "REFUND_PENDING"
    assert InventoryItem.objects.get(variant=v).on_hand == 3  # back on the shelf
    refund = Refund.objects.get()
    assert refund.kind == "cancellation" and refund.amount_irr == order.total_irr and refund.status == "requested"
    order.refresh_from_db()
    assert [h.to_status for h in order.history.all()][-2:] == ["CANCELLED", "REFUND_PENDING"]
    rid = refund.public_id
    assert finance.post(f"{API}/staff/refunds/{rid}/reject", {"note": "no"}, format="json").json()["error"]["code"] == "cannot_reject"
    assert finance.post(f"{API}/staff/refunds/{rid}/complete", {}, format="json").json()["error"]["code"] == "invalid_refund_state"  # approve first
    finance.post(f"{API}/staff/refunds/{rid}/approve", {}, format="json")
    assert finance.post(f"{API}/staff/refunds/{rid}/execute", {}, format="json").status_code == 200  # fake provider supports refunds
    order.refresh_from_db()
    assert order.status == S.REFUNDED and order.refunded_irr == order.total_irr


@pytest.mark.parametrize("late", [S.PURCHASE_PENDING, S.SHIPPED])
def test_customer_cannot_cancel_once_buying_or_packing_started(shop, buyer, late):
    user, client, address = buyer
    _, (v,) = make_product({})
    order, _ = buy(client, address, v)
    go(order, S.PROCESSING, S.PURCHASE_PENDING)
    if late == S.SHIPPED:
        go(Order.objects.get(pk=order.pk), S.PURCHASED, S.IN_TRANSIT, S.READY_TO_SHIP, S.SHIPPED, source_order_ref="TY-1")
    r = client.post(f"{API}/orders/{order.public_id}/cancel", {}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "cancellation_not_allowed"
    assert Order.objects.get(pk=order.pk).status == late


def test_cancelling_a_paid_proxy_order_before_purchase_cancels_procurement_and_refunds(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({})
    order, _ = buy(client, address, v)
    assert client.post(f"{API}/orders/{order.public_id}/cancel", {}, format="json").json()["status"] == "REFUND_PENDING"
    assert order.items.get().procurement.status == "cancelled" and Refund.objects.get().amount_irr == order.total_irr


def test_unpaid_orders_expire_and_free_the_stock(shop, buyer):
    user, client, address = buyer
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 2})
    order, body = buy(client, address, v, 2, pay_now=False)
    assert lifecycle.expire_unpaid(now=timezone.now() + timedelta(minutes=10)) == 0  # window still open
    assert lifecycle.expire_unpaid(now=timezone.now() + timedelta(hours=1)) == 1
    order.refresh_from_db()
    assert order.status == S.CANCELLED and order.cancel_reason == "payment_timeout"
    assert InventoryItem.objects.get(variant=v).sellable == 2
    assert order.history.last().actor_type == "system"
    assert lifecycle.expire_unpaid(now=timezone.now() + timedelta(hours=2)) == 0  # idempotent


# ------------------------------------------------------------------------------------------ returns and partial refunds
def make_delivered(client, address, qty=3, stock=10):
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": stock, "size": "m"})
    order, _ = buy(client, address, v, qty)
    return deliver(order)


def test_partial_refunds_add_up_to_exactly_the_order_total(shop, buyer, finance):
    user, client, address = buyer
    order = make_delivered(client, address, qty=3)
    item = order.items.get()
    assert order.shipping_irr > 0
    assert client.get(f"{API}/orders/{order.public_id}").json()["actions"]["can_request_refund"] is True

    def request(qty):
        return client.post(
            f"{API}/orders/{order.public_id}/refunds",
            {"items": [{"order_item_id": str(item.public_id), "quantity": qty}], "reason": "wrong size", "amount_irr": "999999999"},
            format="json",
        )

    r = request(1)
    assert r.status_code == 201, r.content
    first = Refund.objects.get(public_id=r.json()["id"])
    assert first.amount_irr != D("999999999") and first.amount_irr >= item.unit_price_irr + item.unit_separate_irr  # amount is computed, never accepted
    assert Order.objects.get(pk=order.pk).status == S.REFUND_PENDING
    assert request(1).json()["error"]["code"] == "not_delivered"  # one open refund at a time
    finance.post(f"{API}/staff/refunds/{first.public_id}/approve", {}, format="json")
    assert finance.post(f"{API}/staff/refunds/{first.public_id}/complete", {"provider_ref": "BANK-1"}, format="json").status_code == 200
    order.refresh_from_db()
    assert order.status == S.PARTIALLY_REFUNDED and order.refunded_irr == first.amount_irr

    assert request(3).json()["error"]["code"] == "invalid_quantity"  # only 2 left to return
    second = Refund.objects.get(public_id=request(2).json()["id"])
    finance.post(f"{API}/staff/refunds/{second.public_id}/approve", {}, format="json")
    finance.post(f"{API}/staff/refunds/{second.public_id}/complete", {}, format="json")
    order.refresh_from_db()
    assert order.status == S.REFUNDED
    assert first.amount_irr + second.amount_irr == order.total_irr == order.refunded_irr  # not one rial lost or invented
    assert request(1).json()["error"]["code"] in ("not_delivered", "nothing_left_to_return")


def test_refund_rejection_returns_the_order_to_where_it_was(shop, buyer, finance):
    user, client, address = buyer
    order = make_delivered(client, address, qty=1)
    item = order.items.get()
    rid = client.post(f"{API}/orders/{order.public_id}/refunds", {"items": [{"order_item_id": str(item.public_id), "quantity": 1}], "reason": "x"}, format="json").json()["id"]
    assert finance.post(f"{API}/staff/refunds/{rid}/reject", {}, format="json").json()["error"]["code"] == "reason_required"
    assert finance.post(f"{API}/staff/refunds/{rid}/reject", {"note": "used item"}, format="json").status_code == 200
    order.refresh_from_db()
    assert order.status == S.DELIVERED and order.refunded_irr == 0 and Refund.objects.get().status == "rejected"
    assert client.get(f"{API}/orders/{order.public_id}").json()["actions"]["can_request_refund"] is True  # may ask again


def test_return_eligibility_rules(shop, buyer, settings):
    user, client, address = buyer
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 5})
    order, _ = buy(client, address, v, 1)
    item = order.items.get()
    body = {"items": [{"order_item_id": str(item.public_id), "quantity": 1}], "reason": "x"}
    url = f"{API}/orders/{order.public_id}/refunds"
    assert client.post(url, body, format="json").json()["error"]["code"] == "not_delivered"  # not delivered yet
    order = deliver(order)
    settings.ORDER_RETURN_WINDOW_DAYS = 0
    assert client.post(url, body, format="json").json()["error"]["code"] == "returns_disabled"
    settings.ORDER_RETURN_WINDOW_DAYS = 14
    Order.objects.filter(pk=order.pk).update(delivered_at=timezone.now() - timedelta(days=15))
    r = client.post(url, body, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "return_window_closed"
    Order.objects.filter(pk=order.pk).update(delivered_at=timezone.now() - timedelta(days=13))
    settings.ORDER_NON_RETURNABLE_CATEGORY_SLUGS = [item.category_slug]
    assert client.post(url, body, format="json").json()["error"]["code"] == "item_not_returnable"
    settings.ORDER_NON_RETURNABLE_CATEGORY_SLUGS = []
    assert client.post(url, {**body, "reason": " "}, format="json").status_code in (400, 422)
    assert client.post(url, {"items": [{"order_item_id": "00000000-0000-0000-0000-000000000000", "quantity": 1}], "reason": "x"}, format="json").json()["error"]["code"] == "invalid_item"
    assert client.post(url, body, format="json").status_code == 201  # inside the window


def test_staff_refund_cannot_exceed_what_is_left_and_needs_the_finance_role(shop, buyer, finance, order_manager):
    user, client, address = buyer
    order = make_delivered(client, address, qty=1)
    url = f"{API}/staff/orders/{order.public_id}/refunds"
    assert order_manager.post(url, {"amount_irr": "1000", "reason": "goodwill"}, format="json").status_code == 403  # separation of duties
    over = finance.post(url, {"amount_irr": str(order.total_irr + 1), "reason": "oops"}, format="json")
    assert over.status_code == 422 and over.json()["error"]["code"] == "refund_exceeds_order"
    ok = finance.post(url, {"amount_irr": str(order.total_irr // 2), "reason": "goodwill"}, format="json")
    assert ok.status_code == 201
    rid = ok.json()["id"]
    finance.post(f"{API}/staff/refunds/{rid}/approve", {}, format="json")
    finance.post(f"{API}/staff/refunds/{rid}/complete", {}, format="json")
    order.refresh_from_db()
    assert order.status == S.PARTIALLY_REFUNDED
    again = finance.post(url, {"amount_irr": str(order.total_irr), "reason": "too much"}, format="json")
    assert again.json()["error"]["code"] == "refund_exceeds_order"  # already refunded part counts


def test_shipping_share_of_a_return_is_exact_for_awkward_numbers():
    from apps.shipping.services.methods import allocate

    assert allocate(D("4500001"), [D("1"), D("1"), D("1")]) == [D("1500001"), D("1500000"), D("1500000")] or sum(allocate(D("4500001"), [D(1)] * 3)) == D("4500001")
    for total, weights in ((D("7"), [D("5"), D("3")]), (D("100"), [D("1"), D("1"), D("1")]), (D("0"), [D("2")]), (D("9"), [D("0"), D("0")])):
        assert sum(allocate(total, weights)) == total
