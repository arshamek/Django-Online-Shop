"""Payments: verified capture only, replay safety, amount integrity, late payment, provider availability."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.orders import state
from apps.orders.models import Order, Refund
from apps.orders.services import lifecycle
from apps.payments.models import Payment, PaymentTransaction
from apps.payments.providers.fake import sign
from tests.phase7_helpers import API, make_address, make_product, new_checkout, place_order, put_in_cart, setup_shop, webhook

pytestmark = pytest.mark.django_db


@pytest.fixture
def placed(make_user, login):
    setup_shop()
    user = make_user()
    client = login(user)
    _, (v,) = make_product({"fulfillment": "DIRECT_INVENTORY", "stock": 3})
    put_in_cart(client, v, 1)
    r = place_order(client, new_checkout(client, make_address(user)), key="pay-1")
    assert r.status_code == 201
    return client, r.json(), Payment.objects.get(public_id=r.json()["payment"]["id"])


def test_payment_amount_is_computed_by_the_server(placed):
    client, body, payment = placed
    order = Order.objects.get()
    assert payment.amount_irr == order.total_irr and payment.status == "pending" and body["payment"]["redirect_url"].startswith("https://")


def test_browser_cannot_mark_an_order_paid(placed):
    client, body, payment = placed
    order = Order.objects.get()
    assert client.get(f"{API}/payments/{payment.public_id}").json()["status"] == "pending"
    assert client.post(f"{API}/payments/{payment.public_id}", {"status": "captured"}, format="json").status_code == 405
    assert Order.objects.get(pk=order.pk).status == "PENDING_PAYMENT"


def test_webhook_needs_a_valid_signature(placed):
    client, body, payment = placed
    payload = json.dumps({"event_id": "e1", "provider_ref": payment.provider_ref, "status": "captured", "amount_irr": str(payment.amount_irr)}).encode()
    anon = APIClient()
    assert anon.post(f"{API}/webhooks/payments/fake", data=payload, content_type="application/json").status_code == 400
    assert anon.post(f"{API}/webhooks/payments/fake", data=payload, content_type="application/json", HTTP_X_SIGNATURE="00" * 32).status_code == 400
    assert anon.post(f"{API}/webhooks/payments/nope", data=payload, content_type="application/json", HTTP_X_SIGNATURE=sign(payload)).status_code == 400
    assert Order.objects.get().status == "PENDING_PAYMENT" and Payment.objects.get().status == "pending"


def test_replayed_webhook_is_applied_once(placed):
    client, body, payment = placed
    anon = APIClient()
    assert webhook(anon, payment, event_id="same").json()["status"] == "applied"
    assert webhook(anon, payment, event_id="same").json()["status"] == "duplicate"
    assert webhook(anon, payment, event_id="other-event").json()["status"] == "duplicate"  # already captured
    order = Order.objects.get()
    assert order.status == "PAID" and order.history.filter(to_status="PAID").count() == 1
    assert PaymentTransaction.objects.filter(kind="capture", event_id="same").count() == 1


def test_wrong_amount_is_recorded_but_never_applied(placed):
    client, body, payment = placed
    assert webhook(APIClient(), payment, amount=payment.amount_irr - 1).json()["status"] == "ignored"
    assert Order.objects.get().status == "PENDING_PAYMENT" and Payment.objects.get().status == "pending"
    assert PaymentTransaction.objects.filter(kind="mismatch").exists()


def test_failed_payment_keeps_the_order_payable_and_a_retry_resumes_it(placed):
    client, body, payment = placed
    assert webhook(APIClient(), payment, status="failed").json()["status"] == "applied"
    assert Payment.objects.get().status == "failed" and Order.objects.get().status == "PENDING_PAYMENT"
    r = client.post(f"{API}/checkout/{body['checkout_id']}/payment", format="json", HTTP_IDEMPOTENCY_KEY="retry-1")
    assert r.status_code == 201 and r.json()["id"] != str(payment.public_id)
    again = client.post(f"{API}/checkout/{body['checkout_id']}/payment", format="json", HTTP_IDEMPOTENCY_KEY="retry-2")
    assert again.json()["id"] == r.json()["id"]  # one live payment per checkout


def test_payment_initiation_is_idempotent_and_owner_only(placed, make_user, login):
    client, body, payment = placed
    a = client.post(f"{API}/checkout/{body['checkout_id']}/payment", format="json", HTTP_IDEMPOTENCY_KEY="order:pay-1")
    assert a.json()["id"] == str(payment.public_id)
    assert client.post(f"{API}/checkout/{body['checkout_id']}/payment", format="json").json()["error"]["code"] == "idempotency_key_required"
    stranger = login(make_user())
    assert stranger.post(f"{API}/checkout/{body['checkout_id']}/payment", format="json", HTTP_IDEMPOTENCY_KEY="z").status_code == 404
    assert stranger.get(f"{API}/payments/{payment.public_id}").status_code == 404


def test_money_arriving_after_cancellation_is_refunded_not_ignored(placed):
    client, body, payment = placed
    order = Order.objects.get()
    assert lifecycle.expire_unpaid(now=timezone.now() + timedelta(hours=2)) == 1  # window closed, stock released
    assert webhook(APIClient(), payment).json()["status"] == "applied"
    order.refresh_from_db()
    assert order.status == "REFUND_PENDING" and order.paid_at is None  # never resurrected as PAID
    refund = Refund.objects.get()
    assert refund.kind == "late_payment" and refund.amount_irr == order.total_irr


def test_without_a_configured_provider_orders_still_exist_and_say_so(make_user, login, settings):
    settings.PAYMENTS_ALLOW_FAKE_PROVIDER = False
    setup_shop()
    user = make_user()
    client = login(user)
    _, (v,) = make_product({})
    put_in_cart(client, v)
    r = place_order(client, new_checkout(client, make_address(user)))
    assert r.status_code == 201 and r.json()["payment"] is None and r.json()["payment_error"] == "payment_provider_unavailable"
    assert Order.objects.get().status == "PENDING_PAYMENT"
