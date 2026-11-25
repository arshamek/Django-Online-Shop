"""Payment administration: provider configuration rules, review of failed payments, safety properties."""

import io
import json
import urllib.error

import pytest

from apps.audit.models import AuditLog
from apps.payments import services
from apps.payments.models import Payment, PaymentProviderConfig
from apps.payments.providers import client as http_client
from apps.payments.providers import registry
from apps.payments.providers.base import ProviderError
from tests.phase8_helpers import API, intl_event, intl_post, shop_checkout, start_payment

pytestmark = pytest.mark.django_db
URL = f"{API}/staff/payment-providers"


@pytest.fixture
def finance(make_staff, login):
    return login(make_staff("Finance Manager"))


@pytest.fixture
def real_adapter(settings):
    settings.PAYMENT_PROVIDER_ADAPTERS = ["tests.phase8_helpers.DummyRealProvider"]


# ------------------------------------------------------------------------------------------------ provider configuration
def test_a_real_provider_cannot_be_enabled_before_eligibility_is_verified(finance, real_adapter):
    base = {"key": "dummy_real", "names": {"en": "Real"}, "merchant_country": "DE", "supported_currencies": ["USD"]}
    assert finance.post(URL, {**base, "is_enabled": True}, format="json").status_code == 400
    created = finance.post(URL, base, format="json")
    assert (
        created.status_code == 201
        and created.json()["is_enabled"] is False
        and created.json()["adapter_registered"] is True
    )
    assert finance.patch(f"{URL}/dummy_real", {"is_enabled": True}, format="json").status_code == 400
    assert (
        finance.patch(f"{URL}/dummy_real", {"eligibility_status": "verified"}, format="json").status_code == 400
    )  # evidence is mandatory
    ok = finance.patch(
        f"{URL}/dummy_real",
        {
            "eligibility_status": "verified",
            "eligibility_notes": "Provider support ticket #42: merchants from DE accepted",
        },
        format="json",
    )
    assert ok.status_code == 200
    assert (
        finance.patch(f"{URL}/dummy_real", {"is_enabled": True, "mode": "live"}, format="json").status_code == 400
    )  # live needs a sandbox run
    assert finance.patch(f"{URL}/dummy_real", {"is_enabled": True}, format="json").status_code == 200
    assert AuditLog.objects.filter(action="payment_provider.updated").count() == 2


def test_configuration_cannot_widen_what_the_adapter_supports_or_enable_an_unknown_adapter(finance, real_adapter):
    assert finance.post(URL, {"key": "dummy_real", "supported_currencies": ["JPY"]}, format="json").status_code == 400
    assert finance.post(URL, {"key": "ghost", "is_enabled": True}, format="json").status_code == 400


def test_mock_providers_can_be_toggled_and_disabling_hides_them_from_customers(finance, make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    assert finance.post(URL, {"key": "mock_ir", "is_enabled": False}, format="json").status_code == 201
    keys = [m["key"] for m in client.get(f"{API}/payments/methods", {"checkout_id": checkout["id"]}).json()["methods"]]
    assert "mock_ir" not in keys
    assert finance.patch(f"{URL}/mock_ir", {"is_enabled": True}, format="json").status_code == 200


def test_provider_configuration_never_contains_or_accepts_credentials(finance, real_adapter, settings):
    settings.PAYMENT_PROVIDER_CREDENTIALS = {"dummy_real": {"api_key": "SECRET-VALUE-123"}}
    created = finance.post(
        URL, {"key": "dummy_real", "api_key": "SECRET-VALUE-123", "credentials": {"x": "y"}}, format="json"
    )
    assert created.status_code == 201 and "SECRET-VALUE-123" not in json.dumps(created.json())
    assert "SECRET-VALUE-123" not in finance.get(URL).content.decode()
    assert not any("key" in f.name and "api" in f.name for f in PaymentProviderConfig._meta.fields)


def test_configuration_permissions(make_staff, login, finance):
    support, finance_view = login(make_staff("Customer Support")), finance
    assert support.get(URL).status_code == 403
    assert support.post(URL, {"key": "mock_ir"}, format="json").status_code == 403
    assert finance_view.get(URL).status_code == 200
    assert finance_view.delete(f"{URL}/mock_ir").status_code in (403, 404, 405)  # providers are disabled, never deleted


def test_mocks_and_credentials_are_unavailable_without_the_explicit_flags(settings):
    assert {"fake", "mock_ir", "mock_intl"} <= set(registry.adapters())
    settings.PAYMENTS_ALLOW_FAKE_PROVIDER = False
    assert registry.adapters() == {}
    settings.PAYMENT_PROVIDER_CREDENTIALS = {}
    from apps.payments.providers.mock import MockInternationalProvider

    with pytest.raises(ProviderError):
        MockInternationalProvider().credentials()


# ------------------------------------------------------------------------------------------------ review of failed payments
def test_failed_and_flagged_payments_can_be_reviewed_and_resolved(finance, make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    payment = Payment.objects.get(public_id=start_payment(client, checkout, "mock_intl").json()["id"])
    from rest_framework.test import APIClient

    intl_post(APIClient(), intl_event(payment, amount=payment.charge_amount - 1))  # mismatch -> flagged
    listing = finance.get(f"{API}/staff/payments", {"problem": "1"}).json()["results"]
    assert [p["id"] for p in listing] == [str(payment.public_id)] and listing[0][
        "review_reason"
    ] == "amount_or_currency_mismatch"
    detail = finance.get(f"{API}/staff/payments/{payment.public_id}").json()
    assert [t["kind"] for t in detail["transactions"]] == ["initiate", "mismatch"] and detail["callbacks"][0][
        "outcome"
    ] == "ignored"
    assert (
        finance.post(f"{API}/staff/payments/{payment.public_id}/resolve-review", {}, format="json").status_code == 400
    )  # a note is required
    resolved = finance.post(
        f"{API}/staff/payments/{payment.public_id}/resolve-review",
        {"note": "Checked with provider: partial capture"},
        format="json",
    )
    assert resolved.status_code == 200 and resolved.json()["needs_review"] is False
    assert finance.get(f"{API}/staff/payments", {"problem": "1"}).json()["results"] == []
    assert AuditLog.objects.filter(action="payment.review_resolved").exists()


def test_reconciliation_endpoint_runs_and_lists(finance, make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    start_payment(client, checkout, "mock_intl")
    run = finance.post(f"{API}/staff/payments/reconciliation", {}, format="json")
    assert run.status_code == 201 and run.json()["finished_at"]
    assert len(finance.get(f"{API}/staff/payments/reconciliation").json()) == 1


def test_audit_trail_covers_the_payment_lifecycle(make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    payment = Payment.objects.get(public_id=start_payment(client, checkout, "mock_intl").json()["id"])
    from apps.payments.providers.mock import MockRemote

    MockRemote.simulate(payment.provider_ref, "captured")
    services.verify_payment(payment)
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"payment.initiated", "payment.verified", "payment.captured"} <= actions


# ------------------------------------------------------------------------------------------------ secure HTTP client for real adapters
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_opener(monkeypatch, behaviour):
    class Opener:
        def open(self, request, timeout=None):
            return behaviour(request, timeout)

    monkeypatch.setattr(http_client.urllib.request, "build_opener", lambda *handlers: Opener())


def test_http_client_refuses_plain_http_and_maps_failures(monkeypatch):
    with pytest.raises(ProviderError):
        http_client.request_json("GET", "http://gateway.example/api")
    fake_opener(monkeypatch, lambda req, timeout: FakeResponse(b'{"ok": true}'))
    assert http_client.request_json(
        "POST", "https://gateway.example/api", payload={"a": 1}, headers={"Authorization": "Bearer x"}
    ) == {"ok": True}
    fake_opener(monkeypatch, lambda req, timeout: FakeResponse(b"<html>"))
    with pytest.raises(ProviderError, match="malformed"):
        http_client.request_json("GET", "https://gateway.example/api")
    fake_opener(monkeypatch, lambda req, timeout: FakeResponse(b"x" * (http_client.MAX_RESPONSE_BYTES + 5)))
    with pytest.raises(ProviderError, match="too large"):
        http_client.request_json("GET", "https://gateway.example/api")

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "err", {}, None)

    fake_opener(monkeypatch, boom)
    with pytest.raises(ProviderError, match="HTTP 500"):
        http_client.request_json("GET", "https://gateway.example/api")

    def timeout_(req, timeout):
        assert timeout == http_client.DEFAULT_TIMEOUT  # a hard timeout is always set
        raise TimeoutError

    fake_opener(monkeypatch, timeout_)
    with pytest.raises(ProviderError, match="unreachable"):
        http_client.request_json("GET", "https://gateway.example/api")


def test_provider_payloads_are_stored_without_secrets_or_card_data_even_when_nested(make_user, login):
    client, user, checkout, _ = shop_checkout(make_user, login)
    payment = Payment.objects.get(public_id=start_payment(client, checkout, "mock_intl").json()["id"])
    services._ledger(
        payment,
        "verify",
        raw={
            "api_key": "SECRET-VALUE-123",
            "status": "pending",
            "signature": "SIG-1",
            "data": {"card": {"number": "4242424242424242"}, "items": [{"token": "TOK-9", "ok": 1}]},
        },
    )
    stored = json.dumps(list(payment.transactions.values_list("raw", flat=True)))
    for leaked in ("SECRET-VALUE-123", "SIG-1", "4242424242424242", "TOK-9"):
        assert leaked not in stored
    assert '"status": "pending"' in stored and '"ok": 1' in stored  # harmless fields are kept for audit
