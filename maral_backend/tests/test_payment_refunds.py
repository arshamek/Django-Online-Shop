"""Provider refunds: idempotent, partial, capped, with manual fallback. Mock providers only."""

from decimal import Decimal as D

import pytest

from apps.common.exceptions import BusinessRuleViolation, Conflict, UpstreamUnavailable
from apps.orders.models import ActorType, Refund
from apps.orders.services import refunds, transitions
from apps.payments import refunds as payment_refunds
from apps.payments.models import PaymentRefund, PaymentTransaction
from apps.payments.providers.mock import MockRemote
from tests.phase8_helpers import API, paid_order, to_ready_to_ship

pytestmark = pytest.mark.django_db


def delivered(order, staff):
    order = to_ready_to_ship(order, staff)
    for target in ("SHIPPED", "DELIVERED"):
        order = transitions.transition(order, target, actor_type=ActorType.STAFF, actor=staff)
    return order


@pytest.fixture
def staff(make_staff):
    return make_staff("Finance Manager")


@pytest.fixture
def paid(make_user, login, staff):
    client, order, payment = paid_order(make_user, login, "mock_intl")
    return delivered(order, staff), payment


def approved_refund(order, staff, amount):
    refund = refunds.staff_refund(order, staff, D(amount), "test")
    return refunds.approve(refund, staff)


def remote_refunds(payment):
    return MockRemote.get(payment.provider_ref)["refunds"]


def test_provider_refund_completes_the_order_refund_and_books_the_payment(paid, staff):
    order, payment = paid
    refund = approved_refund(order, staff, order.total_irr)
    done = refunds.execute_with_provider(refund, staff)
    payment.refresh_from_db()
    assert done.status == "completed" and done.provider_ref.startswith("re_mock_")
    assert (
        payment.refunded_irr == order.total_irr and payment.refunded_charge_amount == payment.charge_amount
    )  # whole payment, no rounding dust
    row = PaymentRefund.objects.get()
    assert (
        row.status == "succeeded"
        and row.charge_currency == "USD"
        and PaymentTransaction.objects.filter(kind="refund").count() == 1
    )


def test_duplicate_refund_is_refused_and_the_provider_is_asked_once(paid, staff):
    order, payment = paid
    refund = approved_refund(order, staff, order.total_irr)
    refunds.execute_with_provider(refund, staff)
    with pytest.raises(Conflict) as exc:
        refunds.execute_with_provider(refund, staff)
    assert exc.value.get_codes() == "refund_already_completed"
    assert len(remote_refunds(payment)) == 1 and PaymentRefund.objects.count() == 1
    payment.refresh_from_db()
    assert payment.refunded_irr == order.total_irr  # not doubled


def test_crash_between_provider_success_and_completion_is_recovered_without_a_second_refund(paid, staff):
    order, payment = paid
    refund = approved_refund(order, staff, order.total_irr)
    first = payment_refunds.execute(refund, staff)  # the provider paid, then the process "crashed" before complete()
    assert Refund.objects.get(pk=refund.pk).status == "approved"
    done = refunds.execute_with_provider(refund, staff)
    assert done.status == "completed" and done.provider_ref == first.provider_ref
    assert len(remote_refunds(payment)) == 1


def test_partial_refunds_add_up_exactly(paid, staff):
    order, payment = paid
    first_amount = (order.total_irr / 3).to_integral_value()
    refunds.execute_with_provider(approved_refund(order, staff, first_amount), staff)
    payment.refresh_from_db()
    assert payment.refunded_irr == first_amount and 0 < payment.refunded_charge_amount < payment.charge_amount
    order.refresh_from_db()
    assert order.status == "PARTIALLY_REFUNDED"
    refunds.execute_with_provider(approved_refund(order, staff, order.total_irr - first_amount), staff)
    payment.refresh_from_db()
    assert payment.refunded_irr == order.total_irr
    assert payment.refunded_charge_amount == payment.charge_amount  # the last slice takes the remainder
    assert sum(r.charge_amount for r in PaymentRefund.objects.all()) == payment.charge_amount
    order.refresh_from_db()
    assert order.status == "REFUNDED"


def test_a_refund_can_never_exceed_what_was_captured(paid, staff):
    order, payment = paid
    refund = approved_refund(order, staff, D(1000))
    PaymentRefund.objects.create(
        payment=payment,
        idempotency_key="other",
        status="succeeded",
        amount_irr=payment.captured_amount_irr,
        charge_amount=payment.charge_amount,
        charge_currency="USD",
    )
    with pytest.raises(BusinessRuleViolation) as exc:
        payment_refunds.execute(refund, staff)
    assert exc.value.get_codes() == "refund_exceeds_payment" and len(remote_refunds(payment)) == 0


def test_provider_refusal_is_recorded_and_the_refund_stays_retryable(paid, staff):
    order, payment = paid
    refund = approved_refund(order, staff, order.total_irr)
    MockRemote.simulate(payment.provider_ref, "pending")  # the provider no longer considers it captured
    with pytest.raises(BusinessRuleViolation) as exc:
        refunds.execute_with_provider(refund, staff)
    assert exc.value.get_codes() == "provider_refund_failed"
    assert Refund.objects.get(pk=refund.pk).status == "approved" and PaymentRefund.objects.get().status == "failed"
    payment.refresh_from_db()
    assert payment.refunded_irr == 0 and PaymentTransaction.objects.filter(kind="refund_failed").exists()
    MockRemote.simulate(payment.provider_ref, "captured")
    assert refunds.execute_with_provider(refund, staff).status == "completed" and PaymentRefund.objects.count() == 1


def test_provider_outage_leaves_the_outcome_unknown_and_retry_is_safe(paid, staff):
    order, payment = paid
    refund = approved_refund(order, staff, order.total_irr)
    MockRemote.set_outage("mock_intl")
    with pytest.raises(UpstreamUnavailable):
        refunds.execute_with_provider(refund, staff)
    assert PaymentRefund.objects.get().status == "pending" and Refund.objects.get(pk=refund.pk).status == "approved"
    MockRemote.set_outage("mock_intl", False)
    assert refunds.execute_with_provider(refund, staff).status == "completed"
    assert len(remote_refunds(payment)) == 1 and PaymentRefund.objects.count() == 1


def test_a_provider_without_automated_refunds_needs_a_manual_completion_that_is_still_booked(make_user, login, staff):
    client, order, payment = paid_order(make_user, login, "mock_ir")
    order = delivered(order, staff)
    refund = approved_refund(order, staff, D(10_000))
    with pytest.raises(BusinessRuleViolation) as exc:
        refunds.execute_with_provider(refund, staff)
    assert exc.value.get_codes() == "manual_refund_required"
    refunds.complete(refund, staff, provider_ref="bank-transfer-123")
    payment.refresh_from_db()
    row = PaymentRefund.objects.get()
    assert row.status == "manual" and payment.refunded_irr == D(10_000)


def test_partial_refund_is_refused_by_a_provider_that_only_refunds_in_full(paid, staff, monkeypatch):
    import dataclasses

    from apps.payments.providers.mock import MockInternationalProvider

    order, payment = paid
    monkeypatch.setattr(
        MockInternationalProvider,
        "capabilities",
        dataclasses.replace(MockInternationalProvider.capabilities, supports_partial_refund=False),
    )
    refund = approved_refund(order, staff, D(1000))
    with pytest.raises(BusinessRuleViolation) as exc:
        refunds.execute_with_provider(refund, staff)
    assert exc.value.get_codes() == "partial_refund_not_supported" and not remote_refunds(payment)


def test_refund_execution_over_the_api_is_idempotent_and_permissioned(make_user, login, make_staff, staff):
    client, order, payment = paid_order(make_user, login, "mock_intl")
    order = delivered(order, staff)
    refund = approved_refund(order, staff, order.total_irr)
    finance = login(staff)
    assert (
        login(make_staff("Customer Support")).post(f"{API}/staff/refunds/{refund.public_id}/execute").status_code == 403
    )
    assert finance.post(f"{API}/staff/refunds/{refund.public_id}/execute").status_code == 200
    again = finance.post(f"{API}/staff/refunds/{refund.public_id}/execute")
    assert again.status_code == 409 and again.json()["error"]["code"] == "refund_already_completed"
    assert len(remote_refunds(payment)) == 1
