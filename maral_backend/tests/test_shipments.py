"""Shipments: tracking rules, status machine, history, order synchronisation, carrier sync (mock carrier)."""

import pytest

from apps.common.exceptions import BusinessRuleViolation, Conflict
from apps.orders.models import ActorType, Order
from apps.orders.services import transitions
from apps.shipping.models import Shipment, ShipmentEvent, ShippingMethod
from apps.shipping.providers.mock import MockCarrierRemote
from apps.shipping.services import shipments
from tests.phase8_helpers import API, paid_order, to_ready_to_ship

pytestmark = pytest.mark.django_db
TEMPLATE = "https://track.example/t/{tracking_number}"


@pytest.fixture
def staff(make_staff):
    return make_staff("Order Manager")


@pytest.fixture
def ready(make_user, login, staff):
    client, order, _ = paid_order(make_user, login, "mock_ir")
    ShippingMethod.objects.filter(code="standard").update(requires_tracking=True, tracking_url_template=TEMPLATE)
    return client, to_ready_to_ship(order, staff)


def ship_via_order(order, staff, **meta):
    return transitions.transition(order, "SHIPPED", actor_type=ActorType.STAFF, actor=staff, metadata=meta)


# ------------------------------------------------------------------------------------------------ missing tracking
def test_an_order_cannot_be_shipped_without_tracking_when_the_method_requires_it(ready, staff):
    client, order = ready
    with pytest.raises(BusinessRuleViolation) as exc:
        ship_via_order(order, staff)
    assert exc.value.get_codes() == "tracking_required"
    assert Order.objects.get().status == "READY_TO_SHIP" and not Shipment.objects.exists()


def test_a_shipment_cannot_leave_pending_without_tracking_and_bad_numbers_are_refused(ready, staff):
    client, order = ready
    shipment = shipments.create_shipment(order, actor=staff, carrier="Post")
    with pytest.raises(BusinessRuleViolation) as exc:
        shipments.apply_status(shipment, "in_transit", actor=staff)
    assert exc.value.get_codes() == "tracking_required" and Order.objects.get().status == "READY_TO_SHIP"
    for bad in ("", "x", "a;DROP TABLE", "<script>"):
        with pytest.raises(BusinessRuleViolation):
            shipments.set_tracking(shipment, carrier="Post", tracking_number=bad, actor=staff)
    shipments.set_tracking(shipment, carrier="Post", tracking_number="RR123456789IR", actor=staff)
    shipments.apply_status(shipment, "in_transit", actor=staff)
    order.refresh_from_db()
    assert order.status == "SHIPPED" and order.tracking_number == "RR123456789IR" and order.tracking_carrier == "Post"


def test_tracking_is_optional_for_methods_that_do_not_require_it(ready, staff):
    client, order = ready
    ShippingMethod.objects.filter(code="standard").update(requires_tracking=False)
    assert ship_via_order(order, staff).status == "SHIPPED"  # Phase 7 behaviour is preserved
    assert Shipment.objects.get().status == "in_transit" and Shipment.objects.get().tracking_number == ""


# ------------------------------------------------------------------------------------------------ status machine + history
def test_valid_chain_invalid_moves_and_idempotent_repeats(ready, staff):
    client, order = ready
    shipment = shipments.create_shipment(order, actor=staff, carrier="Post", tracking_number="RR1ABC")
    with pytest.raises(Conflict) as exc:
        shipments.apply_status(shipment, "delivered", actor=staff)  # cannot skip dispatch
    assert exc.value.get_codes() == "invalid_shipment_transition"
    shipments.apply_status(shipment, "in_transit", actor=staff, location="Tehran hub")
    assert shipments.apply_status(shipment, "in_transit", actor=staff) is None  # repeat = no new event
    shipments.apply_status(shipment, "failed_attempt", actor=staff, description="Nobody home")
    shipments.apply_status(shipment, "out_for_delivery", actor=staff)
    shipments.apply_status(shipment, "delivered", actor=staff)
    with pytest.raises(Conflict):
        shipments.apply_status(shipment, "in_transit", actor=staff)  # delivered is final
    shipment.refresh_from_db()
    assert shipment.status == "delivered" and shipment.shipped_at and shipment.delivered_at
    assert list(shipment.events.values_list("status", flat=True)) == [
        "pending",
        "in_transit",
        "failed_attempt",
        "out_for_delivery",
        "delivered",
    ]
    assert shipment.events.filter(location="Tehran hub").exists()


def test_history_is_append_only(ready, staff):
    client, order = ready
    shipment = shipments.create_shipment(order, actor=staff, tracking_number="RR1ABC")
    event = shipment.events.get()
    with pytest.raises(RuntimeError):
        event.save()
    with pytest.raises(RuntimeError):
        event.delete()
    assert ShipmentEvent.objects.count() == 1


def test_pending_shipment_can_be_cancelled_and_then_not_edited(ready, staff):
    client, order = ready
    shipment = shipments.create_shipment(order, actor=staff)
    shipments.apply_status(shipment, "cancelled", actor=staff)
    with pytest.raises(Conflict) as exc:
        shipments.set_tracking(shipment, carrier="Post", tracking_number="RR1ABC", actor=staff)
    assert exc.value.get_codes() == "shipment_cancelled"


def test_only_orders_at_a_shippable_stage_get_shipments(make_user, login, staff):
    client, order, _ = paid_order(make_user, login, "mock_ir")  # still PAID
    with pytest.raises(BusinessRuleViolation) as exc:
        shipments.create_shipment(order, actor=staff)
    assert exc.value.get_codes() == "order_not_shippable"


# ------------------------------------------------------------------------------------------------ order synchronisation
def test_shipment_updates_drive_the_order_both_ways(ready, staff):
    client, order = ready
    shipment = shipments.create_shipment(order, actor=staff, carrier="Post", tracking_number="RR1ABC")
    shipments.apply_status(shipment, "in_transit", actor=staff)
    assert Order.objects.get().status == "SHIPPED"
    shipments.apply_status(shipment, "out_for_delivery", actor=staff)
    shipments.apply_status(shipment, "delivered", actor=staff)
    delivered = Order.objects.get()
    assert delivered.status == "DELIVERED" and delivered.delivered_at
    assert Shipment.objects.count() == 1  # no duplicate shipment created by the order hook


def test_marking_the_order_shipped_the_old_way_creates_the_shipment(ready, staff):
    client, order = ready
    ship_via_order(order, staff, tracking_carrier="Post", tracking_number="RR9XYZ")
    shipment = Shipment.objects.get()
    assert (
        shipment.status == "in_transit"
        and shipment.tracking_number == "RR9XYZ"
        and shipment.tracking_url == "https://track.example/t/RR9XYZ"
    )
    transitions.transition(Order.objects.get(), "DELIVERED", actor_type=ActorType.STAFF, actor=staff)
    shipment.refresh_from_db()
    assert shipment.status == "delivered" and shipment.events.filter(status="delivered").count() == 1


def test_an_order_with_two_parcels_is_delivered_only_when_both_are(ready, staff):
    client, order = ready
    one = shipments.create_shipment(order, actor=staff, tracking_number="RR1AAA")
    two = shipments.create_shipment(order, actor=staff, tracking_number="RR2BBB")
    for shipment in (one, two):
        shipments.apply_status(shipment, "in_transit", actor=staff)
    shipments.apply_status(one, "delivered", actor=staff)
    assert Order.objects.get().status == "SHIPPED"
    shipments.apply_status(two, "delivered", actor=staff)
    assert Order.objects.get().status == "DELIVERED"


# ------------------------------------------------------------------------------------------------ customer + staff API
def test_customer_sees_tracking_and_history_but_nothing_internal(make_user, login, ready, staff):
    client, order = ready
    shipment = shipments.create_shipment(order, actor=staff, carrier="Post", tracking_number="RR1ABC")
    shipments.apply_status(shipment, "in_transit", actor=staff, description="Left the warehouse")
    body = client.get(f"{API}/orders/{order.code}/shipments").json()
    row = body["shipments"][0]
    assert (
        row["tracking_number"] == "RR1ABC"
        and row["tracking_url"] == "https://track.example/t/RR1ABC"
        and row["status"] == "in_transit"
    )
    assert row["events"][-1]["description"] == "Left the warehouse"
    assert all("actor_id" not in e and "source" not in e for e in row["events"]) and "provider_key" not in row
    assert body["delivery"]["estimate"]["status"] == "unknown"  # nothing was configured: nothing is promised
    tracking = client.get(f"{API}/orders/{order.code}/tracking").json()
    assert tracking["tracking_number"] == "RR1ABC" and tracking["shipments"][0]["tracking_url"].endswith("RR1ABC")
    assert login(make_user()).get(f"{API}/orders/{order.code}/shipments").status_code == 404


def test_staff_shipment_api_and_permissions(ready, staff, make_staff, login):
    client, order = ready
    manager, support = login(staff), login(make_staff("Customer Support"))
    url = f"{API}/staff/orders/{order.public_id}/shipments"
    assert support.post(url, {"carrier": "Post", "tracking_number": "RR1ABC"}, format="json").status_code == 403
    created = manager.post(url, {"carrier": "Post", "tracking_number": "RR1ABC"}, format="json")
    assert created.status_code == 201, created.content
    sid = created.json()["id"]
    assert (
        manager.post(f"{API}/staff/shipments/{sid}/status", {"status": "delivered"}, format="json").status_code == 409
    )
    assert (
        manager.post(
            f"{API}/staff/shipments/{sid}/status", {"status": "in_transit", "location": "Tehran"}, format="json"
        ).status_code
        == 200
    )
    fixed = manager.post(
        f"{API}/staff/shipments/{sid}/tracking", {"carrier": "Post", "tracking_number": "RR1ABD"}, format="json"
    )
    assert fixed.status_code == 200 and fixed.json()["tracking_number"] == "RR1ABD"
    assert Order.objects.get().tracking_number == "RR1ABD"
    listing = support.get(f"{API}/staff/shipments", {"open": "1"}).json()
    assert [s["id"] for s in listing["results"]] == [sid] and listing["results"][0]["events"][-1]["source"] == "staff"
    assert manager.get(f"{API}/staff/shipments/{sid}").json()["order_code"] == order.code


# ------------------------------------------------------------------------------------------------ carrier adapter (mock)
@pytest.fixture
def carrier_order(ready):
    client, order = ready
    ShippingMethod.objects.filter(code="standard").update(provider_key="mock_carrier", carrier_code="mockcarrier")
    return client, order


def test_the_carrier_creates_the_shipment_and_its_scans_are_synced_idempotently(carrier_order, staff):
    client, order = carrier_order
    shipment = shipments.create_shipment(order, actor=staff, use_provider=True)
    assert (
        shipment.tracking_number.startswith("MOCK")
        and shipment.carrier == "mockcarrier"
        and shipment.carrier_estimated_delivery_at is None
    )
    MockCarrierRemote.scan(shipment.tracking_number, "in_transit", "Departed hub", "Tehran")
    MockCarrierRemote.scan(shipment.tracking_number, "out_for_delivery")
    assert (
        shipments.sync_from_provider(shipment) == 3
    )  # label created + in transit + out for delivery (same-status scans are history too)
    assert shipments.sync_from_provider(shipment) == 0  # replay: nothing new
    assert Order.objects.get().status == "SHIPPED"
    MockCarrierRemote.scan(shipment.tracking_number, "delivered")
    MockCarrierRemote.scan(
        shipment.tracking_number, "in_transit", "Late duplicate scan"
    )  # out of order: kept as history only
    shipments.sync_from_provider(shipment)
    shipment.refresh_from_db()
    assert shipment.status == "delivered" and Order.objects.get().status == "DELIVERED" and shipment.last_synced_at
    assert shipment.events.filter(description="Late duplicate scan", source="carrier").exists()


def test_carrier_outage_is_reported_not_swallowed(carrier_order, staff):
    client, order = carrier_order
    shipment = shipments.create_shipment(order, actor=staff, use_provider=True)
    MockCarrierRemote.set_outage()
    with pytest.raises(Conflict) as exc:
        shipments.sync_from_provider(shipment)
    assert exc.value.get_codes() == "carrier_unreachable"
    MockCarrierRemote.set_outage(False)


def test_syncing_a_shipment_without_a_tracking_api_is_refused(ready, staff):
    client, order = ready
    shipment = shipments.create_shipment(order, actor=staff, tracking_number="RR1ABC")
    with pytest.raises(BusinessRuleViolation) as exc:
        shipments.sync_from_provider(shipment)
    assert exc.value.get_codes() == "tracking_sync_not_supported"
    assert shipments.sync_all() == {"synced": 0, "events": 0, "errors": 0}
