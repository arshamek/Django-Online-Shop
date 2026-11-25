"""Phase 8 payment workflow with MOCK providers: success, failure, expiry, duplicates, signatures, verification, eligibility."""

import time
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.orders.models import Order
from apps.orders.services import lifecycle
from apps.payments import reconciliation, services
from apps.payments.models import Payment, PaymentProviderConfig, PaymentTransaction, ProviderCallback
from apps.payments.providers.mock import MockRemote, signature_header
from tests.phase8_helpers import API, INTL_SECRET, intl_event, intl_post, shop_checkout, start_payment

pytestmark = pytest.mark.django_db


@pytest.fixture
def intl(make_user, login):
    client, user, checkout, order_json = shop_checkout(make_user, login)
    response = start_payment(client, checkout, "mock_intl")
    assert response.status_code == 201, response.content
    return client, checkout, Payment.objects.get(public_id=response.json()["id"])


@pytest.fixture
def iranian(make_user, login):
    client, user, checkout, order_json = shop_checkout(make_user, login)
    response = start_payment(client, checkout, "mock_ir")
    assert response.status_code == 201, response.content
    return client, checkout, Payment.objects.get(public_id=response.json()["id"])


# ------------------------------------------------------------------------------------------------ methods + charge
def test_methods_list_shows_what_each_provider_would_charge(make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    methods = {
        m["key"]: m for m in client.get(f"{API}/payments/methods", {"checkout_id": checkout["id"]}).json()["methods"]
    }
    assert methods["mock_ir"]["charge"]["currency"] == "IRR" and not methods["mock_ir"]["is_foreign_currency"]
    assert (
        methods["mock_intl"]["charge"]["currency"] == "USD" and methods["mock_intl"]["exchange_rate"]["quote"] == "IRR"
    )
    assert all(m["is_test_method"] for m in methods.values())


def test_foreign_charge_is_computed_server_side_and_frozen(intl):
    client, checkout, payment = intl
    assert payment.charge_currency == "USD" and payment.fx_rate and payment.charge_amount > 0
    assert payment.charge_amount == (payment.amount_irr / payment.fx_rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )  # USD has 2 decimals
    body = client.get(f"{API}/payments/{payment.public_id}").json()
    assert body["charge"] == {"amount": str(payment.charge_amount), "currency": "USD"} and "provider_ref" not in body


def test_unknown_or_disabled_method_is_refused(make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    assert start_payment(client, checkout, "nope").json()["error"]["code"] == "payment_provider_unavailable"
    PaymentProviderConfig.objects.create(key="mock_ir", is_enabled=False)
    assert start_payment(client, checkout, "mock_ir", key="k2").json()["error"]["code"] == "payment_method_disabled"


# ------------------------------------------------------------------------------------------------ success / failure / expiry
def test_successful_payment_through_a_signed_webhook(intl):
    client, checkout, payment = intl
    MockRemote.simulate(payment.provider_ref, "captured")
    response = intl_post(APIClient(), intl_event(payment))
    assert response.json()["status"] == "applied"
    payment.refresh_from_db()
    assert (
        payment.status == "captured"
        and payment.captured_amount_irr == payment.amount_irr
        and Order.objects.get().status == "PAID"
    )
    assert PaymentTransaction.objects.filter(payment=payment, kind="capture", currency="USD").exists()


def test_failed_payment_leaves_the_order_payable(intl):
    client, checkout, payment = intl
    assert intl_post(APIClient(), intl_event(payment, "payment.failed", "evt-f")).json()["status"] == "applied"
    payment.refresh_from_db()
    assert payment.status == "failed" and Order.objects.get().status == "PENDING_PAYMENT"
    retry = start_payment(client, checkout, "mock_intl", key="retry-1")
    assert retry.status_code == 201 and retry.json()["id"] != str(payment.public_id)


def test_expired_payment_and_order(intl):
    client, checkout, payment = intl
    later = timezone.now() + timedelta(hours=2)
    assert lifecycle.expire_unpaid(now=later) == 1
    payment.refresh_from_db()
    assert payment.status == "expired" and Order.objects.get().status == "CANCELLED"
    assert PaymentTransaction.objects.filter(payment=payment, kind="expire").count() == 1


def test_a_payment_the_provider_reports_as_paid_is_captured_not_expired(iranian):
    client, checkout, payment = iranian
    MockRemote.simulate(payment.provider_ref, "captured")  # paid at the gateway, our callback never arrived
    lifecycle.expire_unpaid(now=timezone.now() + timedelta(hours=2))
    payment.refresh_from_db()
    assert payment.status == "captured" and Order.objects.get().status == "PAID"


# ------------------------------------------------------------------------------------------------ duplicates and authenticity
def test_duplicate_callback_is_applied_once(intl):
    client, checkout, payment = intl
    anon = APIClient()
    body = intl_event(payment)
    assert intl_post(anon, body).json()["status"] == "applied"
    assert intl_post(anon, body).json()["status"] == "duplicate"
    assert intl_post(anon, intl_event(payment, event_id="evt-other")).json()["status"] == "duplicate"
    assert Order.objects.get().history.filter(to_status="PAID").count() == 1
    assert PaymentTransaction.objects.filter(payment=payment, kind="capture").count() == 1
    assert ProviderCallback.objects.filter(payment=payment, outcome="duplicate").count() == 2


@pytest.mark.parametrize("variant", ["bad", "missing", "wrong_secret", "stale_timestamp", "malformed"])
def test_invalid_signature_is_rejected_and_changes_nothing(intl, variant):
    client, checkout, payment = intl
    body = intl_event(payment)
    kwargs = {
        "bad": {"header": f"t={int(time.time())},v1={'0' * 64}"},
        "missing": {"header": ""},
        "wrong_secret": {"secret": "not-the-secret"},
        "stale_timestamp": {"timestamp": int(time.time()) - 3600},  # replay of an old, correctly signed message
        "malformed": {"header": "garbage"},
    }[variant]
    assert intl_post(APIClient(), body, **kwargs).status_code == 400
    payment.refresh_from_db()
    assert payment.status == "pending" and Order.objects.get().status == "PENDING_PAYMENT"
    assert ProviderCallback.objects.filter(provider="mock_intl", outcome="rejected").exists()


def test_callback_for_another_payments_reference_is_rejected(intl):
    client, checkout, payment = intl
    response = intl_post(APIClient(), intl_event(payment, merchant_ref="0" * 32))
    assert response.status_code == 400
    payment.refresh_from_db()
    assert (
        payment.status == "pending" and payment.needs_review and payment.review_reason == "callback_reference_mismatch"
    )


def test_amount_or_currency_mismatch_is_kept_for_review_not_applied(intl):
    client, checkout, payment = intl
    assert intl_post(APIClient(), intl_event(payment, amount=payment.charge_amount - 1)).json()["status"] == "ignored"
    assert intl_post(APIClient(), intl_event(payment, event_id="e2", currency="EUR")).json()["status"] == "ignored"
    payment.refresh_from_db()
    assert (
        payment.status == "pending"
        and payment.needs_review
        and PaymentTransaction.objects.filter(kind="mismatch").count() == 2
    )


def test_unknown_reference_is_ignored(intl):
    client, checkout, payment = intl
    assert intl_post(APIClient(), intl_event(payment, session="cs_unknown")).json()["status"] == "ignored"


# ------------------------------------------------------------------------------------------------ browser return (Iranian style)
def test_forged_browser_return_cannot_mark_a_payment_paid(iranian):
    client, checkout, payment = iranian
    response = APIClient().get(
        f"{API}/payments/callback/mock_ir", {"ref": payment.provider_ref, "status": "OK", "m": payment.merchant_ref}
    )
    assert (
        response.status_code == 302
        and "status" not in response["Location"]
        and str(payment.public_id) in response["Location"]
    )
    payment.refresh_from_db()
    assert (
        payment.status == "pending" and Order.objects.get().status == "PENDING_PAYMENT"
    )  # provider says: still pending


def test_verified_browser_return_captures(iranian):
    client, checkout, payment = iranian
    MockRemote.simulate(payment.provider_ref, "captured")
    APIClient().get(
        f"{API}/payments/callback/mock_ir", {"ref": payment.provider_ref, "status": "OK", "m": payment.merchant_ref}
    )
    payment.refresh_from_db()
    assert payment.status == "captured" and payment.verified_at is not None


def test_provider_amount_disagreeing_with_ours_is_not_captured(iranian):
    client, checkout, payment = iranian
    MockRemote.simulate(payment.provider_ref, "captured", amount=payment.charge_amount - 1)
    APIClient().get(
        f"{API}/payments/callback/mock_ir", {"ref": payment.provider_ref, "status": "OK", "m": payment.merchant_ref}
    )
    payment.refresh_from_db()
    assert payment.status == "pending" and payment.needs_review


def test_return_with_unknown_reference_redirects_with_an_error(iranian):
    response = APIClient().get(f"{API}/payments/callback/mock_ir", {"ref": "nope", "status": "OK"})
    assert response.status_code == 302 and "error=unknown_payment" in response["Location"]


# ------------------------------------------------------------------------------------------------ provider outage / idempotency / cancel
def test_provider_outage_at_initiation_fails_cleanly(make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    MockRemote.set_outage("mock_intl")
    response = start_payment(client, checkout, "mock_intl")
    assert response.status_code >= 500 and response.json()["error"]["code"] == "payment_provider_error"
    payment = Payment.objects.get()
    assert payment.status == "failed" and payment.failure_code == "provider_unavailable"
    MockRemote.set_outage("mock_intl", False)
    assert start_payment(client, checkout, "mock_intl", key="after-outage").status_code == 201


def test_idempotency_key_replays_and_rejects_reuse_for_another_method(intl):
    client, checkout, payment = intl
    again = start_payment(client, checkout, "mock_intl")
    assert again.json()["id"] == str(payment.public_id) and Payment.objects.count() == 1
    assert start_payment(client, checkout, "mock_ir").json()["error"]["code"] == "idempotency_conflict"


def test_only_one_payment_in_progress_but_it_can_be_cancelled_and_replaced(intl):
    client, checkout, payment = intl
    assert start_payment(client, checkout, "mock_ir", key="k2").json()["error"]["code"] == "payment_in_progress"
    cancelled = client.post(f"{API}/payments/{payment.public_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert start_payment(client, checkout, "mock_ir", key="k3").status_code == 201


def test_cancel_after_the_customer_actually_paid_is_refused(iranian):
    client, checkout, payment = iranian
    MockRemote.simulate(payment.provider_ref, "captured")
    response = client.post(f"{API}/payments/{payment.public_id}/cancel")
    assert response.status_code == 409 and response.json()["error"]["code"] == "payment_already_captured"
    assert Order.objects.get().status == "PAID"


def test_payment_history_of_an_order_is_visible_to_its_owner_only(make_user, login, intl):
    client, checkout, payment = intl
    intl_post(APIClient(), intl_event(payment, "payment.failed", "evt-f"))
    start_payment(client, checkout, "mock_intl", key="second")
    order = Order.objects.get()
    body = client.get(f"{API}/orders/{order.code}/payments").json()
    assert [p["status"] for p in body["payments"]] == ["pending", "failed"]
    assert all("provider_ref" not in p for p in body["payments"])
    assert login(make_user()).get(f"{API}/orders/{order.code}/payments").status_code == 404


# ------------------------------------------------------------------------------------------------ eligibility of real providers
@pytest.fixture
def real_adapter(settings):
    settings.PAYMENT_PROVIDER_ADAPTERS = ["tests.phase8_helpers.DummyRealProvider"]


def test_a_real_adapter_is_unusable_until_eligibility_is_verified(real_adapter, make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    assert (
        start_payment(client, checkout, "dummy_real").json()["error"]["code"] == "payment_method_disabled"
    )  # no config
    config = PaymentProviderConfig.objects.create(key="dummy_real", is_enabled=True)
    assert (
        start_payment(client, checkout, "dummy_real", key="k2").json()["error"]["code"]
        == "payment_method_eligibility_not_verified"
    )
    config.eligibility_status, config.eligibility_notes = "verified", "Contract #1 confirmed by provider support"
    config.mode = "live"
    config.save()
    assert (
        start_payment(client, checkout, "dummy_real", key="k3").json()["error"]["code"]
        == "payment_method_sandbox_not_verified"
    )
    config.sandbox_verified_at = timezone.now()
    config.save()
    assert start_payment(client, checkout, "dummy_real", key="k4").status_code == 201


def test_customer_country_restriction_hides_a_method(make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    PaymentProviderConfig.objects.create(key="mock_ir", is_enabled=True, customer_countries=["DE"])
    keys = [m["key"] for m in client.get(f"{API}/payments/methods", {"checkout_id": checkout["id"]}).json()["methods"]]
    assert "mock_ir" not in keys and "mock_intl" in keys


# ------------------------------------------------------------------------------------------------ verification and reconciliation
def test_staff_verification_applies_only_the_providers_answer(make_user, login, make_staff, intl):
    client, checkout, payment = intl
    staff = login(make_staff("Finance Manager"))
    assert staff.post(f"{API}/staff/payments/{payment.public_id}/verify").json()["provider_status"] == "pending"
    MockRemote.simulate(payment.provider_ref, "captured")
    body = staff.post(f"{API}/staff/payments/{payment.public_id}/verify").json()
    assert body["outcome"] == "applied" and body["payment"]["status"] == "captured"
    assert staff.post(f"{API}/staff/payments/{payment.public_id}/verify").json()["outcome"] == "duplicate"
    assert (
        login(make_staff("Customer Support")).post(f"{API}/staff/payments/{payment.public_id}/verify").status_code
        == 403
    )


def test_reconciliation_recovers_a_lost_webhook(intl):
    _, _, lost = intl
    MockRemote.simulate(lost.provider_ref, "captured")  # paid at the provider; our webhook never arrived
    Payment.objects.filter(pk=lost.pk).update(created_at=timezone.now() - timedelta(minutes=30))
    run = reconciliation.reconcile()
    lost.refresh_from_db()
    assert lost.status == "captured" and Order.objects.get().status == "PAID"
    assert {d["kind"] for d in run.discrepancies} == {"missed_capture"} and run.applied == 1 and run.finished_at


def test_reconciliation_flags_but_never_reverses_a_capture_the_provider_does_not_confirm(iranian):
    _, _, phantom = iranian
    services.capture(phantom, amount=phantom.charge_amount, currency="IRR", event_id="manual-1")
    MockRemote.simulate(phantom.provider_ref, "failed")
    run = reconciliation.reconcile()
    phantom.refresh_from_db()
    assert phantom.status == "captured" and phantom.needs_review
    assert {d["kind"] for d in run.discrepancies} == {"captured_not_confirmed"} and run.applied == 0


def test_a_payment_captured_after_it_was_given_up_on_is_recovered(intl):
    client, checkout, payment = intl
    lifecycle.expire_unpaid(now=timezone.now() + timedelta(hours=2))
    assert Order.objects.get().status == "CANCELLED"
    MockRemote.simulate(payment.provider_ref, "captured")  # money arrived after expiry
    reconciliation.reconcile()
    payment.refresh_from_db()
    assert payment.status == "captured"
    assert Order.objects.get().refunds.filter(kind="late_payment").exists()  # never kept silently: a refund is opened


def test_signature_helper_rejects_wrong_secret():
    from apps.payments.providers.base import CallbackRejected
    from apps.payments.providers.international import verify_signature

    body = b"{}"
    verify_signature(INTL_SECRET, signature_header(INTL_SECRET, body), body)
    with pytest.raises(CallbackRejected):
        verify_signature("other", signature_header(INTL_SECRET, body), body)
