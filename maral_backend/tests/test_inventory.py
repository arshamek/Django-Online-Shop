"""Stock reservation rules at the service level: no oversell, all-or-nothing, expiry, idempotent movements."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import VariantAvailability
from apps.inventory.models import InventoryItem, InventoryMovement, InventoryReservation
from apps.inventory.services import stock
from tests.phase7_helpers import make_product, setup_shop

pytestmark = pytest.mark.django_db
SOON = lambda: timezone.now() + timedelta(minutes=30)  # noqa: E731


@pytest.fixture
def two_variants():
    setup_shop()
    _, (a, b) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 2, "size": "m"}, {"fulfillment": "DIRECT_INVENTORY", "stock": 5, "size": "l"})
    return a, b


def test_reserve_never_exceeds_sellable_and_respects_safety_stock(two_variants):
    a, _ = two_variants
    InventoryItem.objects.filter(variant=a).update(safety_stock=1)
    assert stock.sellable_quantities([a.pk]) == {a.pk: 1}
    stock.reserve([(a, 1)], holder_type="order", holder_id="o1", expires_at=SOON())
    with pytest.raises(stock.InsufficientStock):
        stock.reserve([(a, 1)], holder_type="order", holder_id="o2", expires_at=SOON())
    item = InventoryItem.objects.get(variant=a)
    assert (item.on_hand, item.reserved) == (2, 1)


def test_reserve_is_idempotent_per_holder(two_variants):
    a, _ = two_variants
    stock.reserve([(a, 1)], holder_type="order", holder_id="o1", expires_at=SOON())
    stock.reserve([(a, 1)], holder_type="order", holder_id="o1", expires_at=SOON())
    assert InventoryItem.objects.get(variant=a).reserved == 1 and InventoryReservation.objects.count() == 1


def test_all_or_nothing_when_one_line_is_short(two_variants):
    a, b = two_variants
    from django.db import transaction

    with pytest.raises(stock.InsufficientStock) as e:
        with transaction.atomic():
            stock.reserve([(b, 2), (a, 3)], holder_type="order", holder_id="o1", expires_at=SOON())
    assert e.value.variant_ids == [a.pk]
    assert InventoryItem.objects.get(variant=b).reserved == 0  # rolled back with the caller's transaction


def test_release_commit_return_cycle_and_movement_ledger(two_variants):
    a, _ = two_variants
    stock.reserve([(a, 2)], holder_type="order", holder_id="o1", expires_at=SOON())
    assert stock.commit("order", "o1") == 1 and stock.commit("order", "o1") == 0  # second call finds nothing active
    item = InventoryItem.objects.get(variant=a)
    assert (item.on_hand, item.reserved) == (0, 0)
    assert VariantAvailability.objects.get(variant=a).status == "out_of_stock"  # catalogue follows stock
    assert stock.return_committed("order", "o1") == 1
    assert InventoryItem.objects.get(variant=a).on_hand == 2 and VariantAvailability.objects.get(variant=a).status == "in_stock"
    kinds = list(InventoryMovement.objects.filter(item=item).order_by("id").values_list("movement_type", flat=True))
    assert kinds == ["receipt", "reserve", "commit", "return"]
    with pytest.raises(RuntimeError):
        InventoryMovement.objects.first().delete()


def test_expired_reservations_are_released(two_variants):
    a, _ = two_variants
    stock.reserve([(a, 2)], holder_type="order", holder_id="o1", expires_at=timezone.now() - timedelta(seconds=1))
    assert stock.expire_due() == 1
    assert InventoryItem.objects.get(variant=a).reserved == 0 and InventoryReservation.objects.get().status == "expired"
    assert stock.expire_due() == 0


def test_adjustment_needs_a_reason_and_cannot_undercut_reservations(two_variants):
    from apps.common.exceptions import BusinessRuleViolation

    a, _ = two_variants
    item = InventoryItem.objects.get(variant=a)
    stock.reserve([(a, 2)], holder_type="order", holder_id="o1", expires_at=SOON())
    with pytest.raises(BusinessRuleViolation):
        stock.adjust(item, 5, reason=" ")
    with pytest.raises(BusinessRuleViolation):
        stock.adjust(item, 1, reason="recount")
    assert stock.adjust(item, 6, reason="recount").on_hand == 6
