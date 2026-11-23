"""Part E/F: currency, pricing and quote APIs; staff administration; permissions; no leakage of margins."""

import json
import uuid
from decimal import Decimal as D

import pytest

from apps.audit.models import AuditLog
from apps.catalog.models import VariantPrice
from apps.pricing.models import PricingRuleSet
from apps.pricing.services import quotes, repricing
from tests.conftest import API, new_client
from tests.pricing_helpers import SCENARIOS, fx, make_ruleset, priced_variant, rate, route

pytestmark = pytest.mark.django_db
NOPE = uuid.uuid4()


@pytest.fixture
def finance(make_staff, login):
    return login(make_staff(role="Finance Manager"))


@pytest.fixture
def anon():
    client = new_client(csrf=True)
    token = client.get(f"{API}/auth/csrf").json()["csrf_token"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


GUEST = {"HTTP_X_GUEST_TOKEN": "g" * 32}


def s1(**extra):
    scenario = SCENARIOS["scenarios"][0]
    return make_ruleset(rules=scenario["rules"], **{**scenario["ruleset"], **extra})


def post(client, path, body, **extra):
    return client.post(f"{API}{path}", body, format="json", **extra)


# ------------------------------------------------------------------------------ public currencies
def test_currency_list_is_public_and_describes_toman_as_a_unit_of_rial(api_client):
    body = api_client.get(f"{API}/currencies").json()
    codes = {c["code"]: c for c in body["results"]}
    assert set(codes) >= {"IRR", "IRT", "TRY", "AED", "USD", "EUR"}
    assert codes["IRT"]["unit_of"] == {"currency": "IRR", "factor": "10"} and codes["TRY"]["is_source"] is True


def test_public_rates_show_display_routes_only_without_source_details(api_client):
    fx()
    route("EUR", purpose="purchase")  # purchase rates are cost information: never public
    body = api_client.get(f"{API}/exchange-rates").json()
    by = {r["currency"]: r for r in body["rates"]}
    assert set(by) == {"TRY", "AED", "USD", "EUR"}
    assert (
        by["USD"]["irr_per_unit"] == "550000"
        and by["USD"]["irt_per_unit"] == "55000"
        and by["USD"]["indicative"] is True
    )
    text = json.dumps(body)
    assert "bureau" not in text and "source" not in text.lower().replace("unavailable", "")


def test_public_rates_report_stale_or_missing_instead_of_guessing(api_client):
    route("USD", purpose="display", max_age=60)
    rate("USD", "550000", age_seconds=500)
    route("EUR", purpose="display")
    body = api_client.get(f"{API}/exchange-rates").json()
    assert body["rates"] == [] and {u["currency"]: u["reason"] for u in body["unavailable"]} == {
        "USD": "rate_stale",
        "EUR": "rate_missing",
    }


# ------------------------------------------------------------------------------ calculate / product price
def test_calculate_returns_a_customer_safe_price(anon):
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    r = post(anon, "/pricing/calculate", {"variant_id": str(v.public_id), "currency": "IRT"})
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["unit_price"] == "2570000" and body["unit_price_irr"] == "25700000" and body["indicative"] is False
    text = json.dumps(body).lower()
    for secret in ("profit", "margin", "markup", "internal", "realized", "rule"):
        assert secret not in text
    assert body["breakdown"] is None  # customers see nothing unless the owner decides so


def test_calculate_in_a_foreign_currency_is_indicative(anon):
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    body = post(anon, "/pricing/calculate", {"variant_id": str(v.public_id), "currency": "USD"}).json()
    assert body["indicative"] is True and body["unit_price"] == str(
        (D("25700000") / D("550000")).quantize(D("0.01")).normalize()
    )


def test_calculate_when_unavailable_gives_a_generic_error_without_reasons(anon):
    fx()
    make_ruleset(profit_method="pending")
    v = priced_variant("1000", "TRY")
    r = post(anon, "/pricing/calculate", {"variant_id": str(v.public_id)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "price_unavailable"
    assert "profit" not in r.content.decode().lower()


def test_calculate_requires_csrf_and_validates_input(api_client, anon):
    fx()
    s1()
    v = priced_variant()
    assert (
        new_client(csrf=True)
        .post(f"{API}/pricing/calculate", {"variant_id": str(v.public_id)}, format="json")
        .status_code
        == 403
    )
    assert post(anon, "/pricing/calculate", {"variant_id": "nope"}).status_code == 400
    assert post(anon, "/pricing/calculate", {"variant_id": str(NOPE)}).status_code == 404
    assert post(anon, "/pricing/calculate", {"variant_id": str(v.public_id), "currency": "GBP"}).status_code == 422


def test_customer_breakdown_visibility_is_an_owner_setting_and_never_shows_profit(anon):
    fx()
    s1(customer_breakdown="itemized")
    v = priced_variant("1000", "TRY")
    body = post(anon, "/pricing/calculate", {"variant_id": str(v.public_id)}).json()
    lines = body["breakdown"]["lines"]
    assert [ln["kind"] for ln in lines] == ["shipping", "customs", "service_fee"]
    assert "profit" not in json.dumps(body).lower() and body["breakdown"]["rate"]["pair"] == "TRY/IRR"


def test_product_price_details_use_stored_prices(anon):
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk)
    body = anon.get(f"{API}/pricing/products/{v.product.public_id}?currency=IRT").json()
    row = body["variants"][0]
    assert row["available"] and row["final_price"] == "2570000" and row["origin"] == "pricing_engine"
    assert (
        anon.get(f"{API}/pricing/products/{v.product.public_id}?currency=IRR").json()["variants"][0]["final_price"]
        == "25700000"
    )
    assert anon.get(f"{API}/pricing/products/{NOPE}").status_code == 404


# ------------------------------------------------------------------------------ quotes
def test_guest_quote_create_read_expire_and_isolation(anon):
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    body = {"items": [{"variant_id": str(v.public_id), "quantity": 2}], "currency": "IRT"}
    assert post(anon, "/pricing/quotes", body).status_code == 422  # no owner yet
    r = post(anon, "/pricing/quotes", body, **GUEST)
    assert r.status_code == 201, r.content
    q = r.json()
    assert q["status"] == "open" and q["total"] == "5140000" and q["items"][0]["line_price_irr"] == "51400000"
    assert q["items"][0]["rate"]["applied"] == "15000"
    assert "profit" not in json.dumps(q).lower() and "calculation" not in q["items"][0]
    assert anon.get(f"{API}/pricing/quotes/{q['id']}", **GUEST).status_code == 200
    stranger = {"HTTP_X_GUEST_TOKEN": "h" * 32}
    assert anon.get(f"{API}/pricing/quotes/{q['id']}", **stranger).status_code == 404
    assert anon.get(f"{API}/pricing/quotes/{q['id']}").status_code == 404
    assert post(anon, f"/pricing/quotes/{q['id']}/expire", {}, **stranger).status_code == 404
    expired = post(anon, f"/pricing/quotes/{q['id']}/expire", {}, **GUEST).json()
    assert expired["status"] == "expired"


def test_signed_in_user_owns_their_quotes(make_user, login):
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    client = login(make_user(), csrf=True)
    q = post(client, "/pricing/quotes", {"items": [{"variant_id": str(v.public_id), "quantity": 1}]}).json()
    assert client.get(f"{API}/pricing/quotes/{q['id']}").status_code == 200
    other = login(make_user(), csrf=True)
    assert other.get(f"{API}/pricing/quotes/{q['id']}").status_code == 404


def test_locked_quote_cannot_be_expired_through_the_api(anon):
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    q = post(anon, "/pricing/quotes", {"items": [{"variant_id": str(v.public_id), "quantity": 1}]}, **GUEST).json()
    from apps.pricing.models import PriceQuote

    quotes.lock_quote(PriceQuote.objects.get(public_id=q["id"]).pk, reference="ORD-9")
    r = post(anon, f"/pricing/quotes/{q['id']}/expire", {}, **GUEST)
    assert r.status_code == 409 and r.json()["error"]["code"] == "quote_locked"
    assert anon.get(f"{API}/pricing/quotes/{q['id']}", **GUEST).json()["status"] == "locked"


def test_quote_refused_with_generic_message_when_price_blocked(anon):
    s1()  # no rates configured
    v = priced_variant("1000", "TRY")
    r = post(anon, "/pricing/quotes", {"items": [{"variant_id": str(v.public_id), "quantity": 1}]}, **GUEST)
    assert r.status_code == 422 and r.json()["error"]["code"] == "price_unavailable"
    assert "no_rate_route" not in r.content.decode()


def test_quote_rate_limit_is_scoped(anon, settings):
    from rest_framework.settings import api_settings

    rates = dict(api_settings.DEFAULT_THROTTLE_RATES)
    assert "price_quote" in rates and "price_calculate" in rates


# ------------------------------------------------------------------------------ permissions
STAFF_ENDPOINTS = [
    ("get", "/staff/currencies"),
    ("get", "/staff/currencies/sources"),
    ("post", "/staff/currencies/sources"),
    ("get", "/staff/currencies/routes"),
    ("get", "/staff/currencies/rates"),
    ("post", "/staff/currencies/rates"),
    ("get", "/staff/currencies/health"),
    ("get", "/staff/pricing/readiness"),
    ("get", "/staff/pricing/rulesets"),
    ("post", "/staff/pricing/rulesets"),
    ("post", "/staff/pricing/preview"),
    ("post", "/staff/pricing/reprice"),
    ("get", "/staff/pricing/history"),
    ("get", "/staff/pricing/quotes"),
    ("get", f"/staff/pricing/quotes/{NOPE}"),
]


@pytest.mark.parametrize(("method", "path"), STAFF_ENDPOINTS)
def test_staff_endpoints_reject_anonymous_customers_and_unrelated_staff(
    method, path, api_client, make_user, make_staff, login
):
    call = lambda c: getattr(c, method)(f"{API}{path}", {} if method == "post" else None, format="json")  # noqa: E731
    assert call(api_client).status_code in (401, 403)
    assert call(login(make_user())).status_code == 403
    assert call(login(make_staff(role="Product Manager"))).status_code == 403
    assert call(login(make_staff(role="Import Manager"))).status_code == 403


def test_finance_manager_cannot_use_translation_endpoints(finance):
    assert finance.get(f"{API}/staff/translations/jobs").status_code == 403


def test_product_manager_can_use_translation_endpoints_but_not_pricing(make_staff, login):
    client = login(make_staff(role="Product Manager"))
    assert client.get(f"{API}/staff/translations/jobs").status_code == 200
    assert client.get(f"{API}/staff/pricing/rulesets").status_code == 403


# ------------------------------------------------------------------------------ staff: rule sets
def test_ruleset_lifecycle_draft_edit_preview_publish(finance):
    fx()
    created = post(finance, "/staff/pricing/rulesets", {"blank": True, "name": "owner draft"})
    assert created.status_code == 201
    version = created.json()["version"]
    base = f"{API}/staff/pricing/rulesets/{version}"
    pending = {p["key"] for p in created.json()["pending_decisions"]}
    assert {"profit_method", "estimates_policy", "required_shipping", "required_customs"} <= pending

    r = finance.patch(
        base,
        {
            "profit_method": "markup",
            "target_profit_percent": "25",
            "estimates_policy": "allow",
            "required_cost_kinds": [],
        },
        format="json",
    )
    assert (
        r.status_code == 200 and r.json()["decisions"]["target_profit_confirmed"] is False
    )  # figure changed -> unconfirmed
    rule = {
        "code": "round",
        "name": "Round",
        "kind": "rounding",
        "rounding_mode": "nearest",
        "rounding_step_irr": "100000",
    }
    assert post(finance, f"/staff/pricing/rulesets/{version}/rules", rule).status_code == 201

    preview = post(
        finance,
        "/staff/pricing/preview",
        {"original_amount": "1000", "original_currency": "TRY", "rule_version": version, "currency": "IRT"},
    )
    body = preview.json()
    assert preview.status_code == 200 and body["publishable"] is True and body["unit_price_irr"] == "18800000"
    assert (
        body["profit"]["profit_irr"] == "3750000" and body["rule_set_status"] == "draft"
    )  # the draft is previewable before publication

    assert post(finance, f"/staff/pricing/rulesets/{version}/publish", {}).status_code == 200
    assert finance.patch(base, {"profit_method": "margin"}, format="json").status_code == 422  # immutable now
    assert post(finance, f"/staff/pricing/rulesets/{version}/rules", {**rule, "code": "x"}).status_code == 422
    assert finance.delete(f"{base}/rules/round").status_code == 422
    assert AuditLog.objects.filter(action="pricing.ruleset_published").exists()


def test_clone_edit_and_publish_keeps_old_versions_intact(finance):
    fx()
    s1()
    r = post(finance, "/staff/pricing/rulesets", {})
    draft = r.json()["version"]
    assert draft == 2 and len(r.json()["rules"]) == 4  # cloned rules
    finance.patch(
        f"{API}/staff/pricing/rulesets/{draft}",
        {"target_profit_percent": "30", "target_profit_confirmed": True},
        format="json",
    )
    finance.patch(f"{API}/staff/pricing/rulesets/{draft}/rules/ship", {"amount": "4000000"}, format="json")
    post(finance, f"/staff/pricing/rulesets/{draft}/publish", {})
    old = finance.get(f"{API}/staff/pricing/rulesets/1").json()
    new = finance.get(f"{API}/staff/pricing/rulesets/{draft}").json()
    assert old["status"] == "archived" and old["decisions"]["target_profit_percent"] == "25"
    assert new["status"] == "published" and {r["code"]: r["amount"] for r in new["rules"]}["ship"] == "4000000"


def test_rule_validation_errors_are_reported(finance):
    draft = post(finance, "/staff/pricing/rulesets", {"blank": True}).json()["version"]
    path = f"/staff/pricing/rulesets/{draft}/rules"
    confirmed_without_source = {
        "code": "ship",
        "name": "Ship",
        "kind": "shipping",
        "calc_method": "fixed",
        "amount": "100",
        "currency": "IRR",
        "value_status": "confirmed",
        "charge_mode": "included",
    }
    r = post(finance, path, confirmed_without_source)
    assert r.status_code == 422 and "source_note" in r.json()["error"]["fields"]
    assert (
        post(finance, path, {**confirmed_without_source, "source_note": "tariff sheet", "code": "ok"}).status_code
        == 201
    )
    assert (
        post(finance, path, {**confirmed_without_source, "source_note": "x", "code": "ok"}).status_code == 422
    )  # duplicate code
    bad_rounding = {"code": "r", "name": "r", "kind": "rounding", "rounding_mode": "nearest", "rounding_step_irr": "15"}
    assert post(finance, path, bad_rounding).status_code in (400, 422)
    unknown_cost = {
        "code": "cust",
        "name": "Customs",
        "kind": "customs",
        "calc_method": "fixed",
        "value_status": "unknown",
    }
    assert (
        post(finance, path, unknown_cost).status_code == 201
    )  # unknown is allowed to be recorded; it just blocks pricing


def test_margin_target_must_stay_below_100(finance):
    draft = post(finance, "/staff/pricing/rulesets", {"blank": True}).json()["version"]
    r = finance.patch(
        f"{API}/staff/pricing/rulesets/{draft}",
        {"profit_method": "margin", "target_profit_percent": "100"},
        format="json",
    )
    assert r.status_code == 422


def test_readiness_lists_what_the_owner_still_has_to_decide(finance):
    from django.core.management import call_command

    call_command("seed_pricing_defaults")
    call_command("seed_pricing_defaults")  # idempotent
    assert PricingRuleSet.objects.count() == 1
    body = finance.get(f"{API}/staff/pricing/readiness").json()
    keys = {p["key"] for p in body["pending_decisions"]}
    assert body["ready"] is False
    assert {
        "profit_method",
        "target_profit_unconfirmed",
        "estimates_policy",
        "required_shipping",
        "required_customs",
        "rounding",
    } <= keys
    assert {"rate_route_TRY", "rate_route_AED", "rate_route_USD", "rate_route_EUR"} <= keys
    assert all(p["consequence"] for p in body["pending_decisions"])
    seeded = PricingRuleSet.objects.get()
    assert (
        seeded.profit_method == "pending"
        and seeded.target_profit_percent == D("25")
        and seeded.target_profit_confirmed is False
    )


def test_fresh_ruleset_with_pending_decisions_prices_nothing_but_previews_cost(finance):
    from django.core.management import call_command

    fx()
    call_command("seed_pricing_defaults")
    body = post(
        finance,
        "/staff/pricing/preview",
        {"original_amount": "1000", "original_currency": "TRY", "fulfillment_type": "PROXY_PURCHASE"},
    ).json()
    codes = {b["code"] for b in body["blockers"]}
    assert body["publishable"] is False and {"profit_method_pending", "missing_required_cost"} <= codes
    assert body["item_irr"] == "15000000"


def test_preview_for_a_variant_and_for_an_unknown_one(finance):
    fx()
    s1()
    v = priced_variant("1000", "TRY", weight=500)
    body = post(finance, "/staff/pricing/preview", {"variant_id": str(v.public_id)}).json()
    assert body["unit_price_irr"] == "25700000" and body["profit"]["method"] == "markup"
    assert [c["kind"] for c in body["components"]][:2] == ["item", "shipping"]
    assert post(finance, "/staff/pricing/preview", {"variant_id": str(NOPE)}).status_code == 404
    assert post(finance, "/staff/pricing/preview", {}).status_code == 422


def test_reprice_endpoint_and_price_history_api(finance):
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    r = post(finance, "/staff/pricing/reprice", {"variant_ids": [str(v.public_id)]})
    assert r.status_code == 200 and r.json()["applied"] == 1
    rate("TRY", "16000")
    post(finance, "/staff/pricing/reprice", {"variant_ids": [str(v.public_id)]})
    hist = finance.get(f"{API}/staff/pricing/history?variant={v.public_id}").json()
    assert hist["count"] == 2 and [h["sequence"] for h in hist["results"]] == [2, 1]
    assert (
        hist["results"][0]["old_final_irr"] == "25700000"
        and hist["results"][0]["calculation"]["profit"]["method"] == "markup"
    )
    assert finance.get(f"{API}/staff/pricing/history?outcome=blocked").json()["count"] == 0
    assert post(finance, "/staff/pricing/reprice", {"all": True, "force": True}).status_code == 422
    assert post(finance, "/staff/pricing/reprice", {"all": True}).json() == {"queued": True}
    assert VariantPrice.objects.filter(variant=v).exists()


def test_staff_quote_detail_has_the_full_calculation(finance):
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    from apps.pricing.models import PriceQuote

    quote = quotes.create_quote(items=[(v, 1)], currency="IRT")
    body = finance.get(f"{API}/staff/pricing/quotes/{quote.public_id}").json()
    item = body["items"][0]
    assert item["unit_realized_profit_irr"] == "5150000" and item["calculation"]["profit"]["profit_irr"] == "5137500"
    assert [c["kind"] for c in item["components"]] == [
        "item",
        "shipping",
        "customs",
        "service_fee",
        "profit",
        "rounding",
    ]
    assert finance.get(f"{API}/staff/pricing/quotes?status=open").json()["count"] == 1
    assert post(finance, f"/staff/pricing/quotes/{quote.public_id}/expire", {}).json()["status"] == "expired"
    assert PriceQuote.objects.get(pk=quote.pk).status == "expired"


# ------------------------------------------------------------------------------ staff: currencies
def test_configure_source_route_and_manual_rate_end_to_end(finance):
    s1()
    v = priced_variant("1000", "TRY")
    src = post(
        finance,
        "/staff/currencies/sources",
        {
            "code": "agent",
            "name": "Payment agent",
            "provider": "manual",
            "kind": "purchase",
            "description": "rate the agent really gives us",
        },
    )
    assert src.status_code == 201 and src.json()["live_verified"] is False
    assert (
        post(
            finance,
            "/staff/currencies/sources",
            {
                "code": "bad",
                "name": "b",
                "provider": "http_json",
                "kind": "market",
                "config": {"url": "https://x", "api_key": "leak"},
            },
        ).status_code
        == 422
    )
    route_r = post(
        finance,
        "/staff/currencies/routes",
        {"base": "TRY", "purpose": "purchase", "max_age_seconds": 3600, "sources": [{"code": "agent", "priority": 1}]},
    )
    assert route_r.status_code == 201
    assert repricing.reprice_variant(v.pk).outcome == "blocked"  # route exists, no rate yet
    manual_r = post(
        finance, "/staff/currencies/rates", {"base": "TRY", "rate": "15000", "note": "agent quote", "source": "agent"}
    )
    assert manual_r.status_code == 201 and manual_r.json()["is_manual"] is True
    assert VariantPrice.objects.get(variant=v).final_irr == D("25700000")  # the new rate flowed into the price
    assert (
        post(
            finance, "/staff/currencies/rates", {"base": "TRY", "rate": "0", "note": "x", "source": "agent"}
        ).status_code
        == 422
    )
    assert (
        post(
            finance, "/staff/currencies/rates", {"base": "TRY", "rate": "1", "note": "", "source": "agent"}
        ).status_code
        == 400
    )
    hist = finance.get(f"{API}/staff/currencies/rates?base=TRY&source=agent").json()
    assert hist["count"] == 1 and hist["results"][0]["rate"] == "15000.0000000000"
    assert AuditLog.objects.filter(action="currencies.manual_rate_set").exists()


def test_disable_and_reorder_sources_changes_which_rate_is_used(finance):
    fx()
    from tests.pricing_helpers import source

    source("bank")
    rate("USD", "500000", src="bank")
    r = finance.patch(
        f"{API}/staff/currencies/routes/{__import__('apps.currencies.models', fromlist=['x']).RateRoute.objects.get(base='USD', purpose='purchase').pk}",
        {"sources": [{"code": "bank", "priority": 1}, {"code": "bureau", "priority": 2}]},
        format="json",
    )
    assert r.status_code == 200 and [s["code"] for s in r.json()["sources"]] == ["bank", "bureau"]
    health = finance.get(f"{API}/staff/currencies/health").json()
    usd = next(x for x in health["routes"] if x["pair"] == "USD/IRR" and x["purpose"] == "purchase")
    assert usd["source"] == "bank" and usd["state"] == "ok"
    assert (
        finance.patch(f"{API}/staff/currencies/sources/bank", {"is_enabled": False}, format="json").status_code == 200
    )
    health = finance.get(f"{API}/staff/currencies/health").json()
    assert (
        next(x for x in health["routes"] if x["pair"] == "USD/IRR" and x["purpose"] == "purchase")["source"] == "bureau"
    )
    assert {s["code"]: s["health"] for s in health["sources"]}["bank"] == "disabled"


def test_health_reports_missing_and_stale_routes(finance):
    route("TRY", max_age=60)
    rate("TRY", "15000", age_seconds=600)
    route("EUR")
    body = finance.get(f"{API}/staff/currencies/health").json()
    states = {r["pair"]: r["state"] for r in body["routes"]}
    assert states["TRY/IRR"] == "rate_stale" and states["EUR/IRR"] == "rate_missing"
    assert set(body["source_currencies_without_purchase_route"]) == {"AED", "USD"}


def test_refresh_a_source_now_and_review_a_quarantined_rate(finance):
    from apps.currencies.models import ExchangeRate

    created = post(
        finance,
        "/staff/currencies/sources",
        {
            "code": "feed",
            "name": "feed",
            "provider": "fixture",
            "kind": "market",
            "max_jump_percent": "10",
            "config": {"rates": [{"base": "USD", "quote": "IRR", "rate": "550000", "as_of": "now"}]},
        },
    )
    assert created.status_code == 201
    r = post(finance, "/staff/currencies/sources/feed/refresh", {})
    assert r.status_code == 200 and r.json()["accepted"] == 1
    finance.patch(
        f"{API}/staff/currencies/sources/feed",
        {"config": {"rates": [{"base": "USD", "quote": "IRR", "rate": "900000", "as_of": "now"}]}},
        format="json",
    )
    r = post(finance, "/staff/currencies/sources/feed/refresh", {}).json()
    assert r["quarantined"] == 1
    row = ExchangeRate.objects.get(status="quarantined")
    assert (
        post(
            finance,
            f"/staff/currencies/rates/{row.public_id}/review",
            {"decision": "accept", "reason": "market really moved"},
        ).json()["status"]
        == "accepted"
    )
    assert (
        post(
            finance, f"/staff/currencies/rates/{row.public_id}/review", {"decision": "accept", "reason": "again"}
        ).status_code
        == 422
    )
    assert post(finance, "/staff/currencies/sources/manual/refresh", {}).status_code == 422


def test_accounting_currency_cannot_be_disabled_but_others_can(finance):
    assert finance.patch(f"{API}/staff/currencies/IRR", {"is_active": False}, format="json").status_code == 422
    assert finance.patch(f"{API}/staff/currencies/AED", {"is_active": False}, format="json").status_code == 200
    codes = {c["code"] for c in new_client().get(f"{API}/currencies").json()["results"]}
    assert "AED" not in codes


# ------------------------------------------------------------------------------ translations API
def test_translation_staff_api_flow(make_staff, login):
    from apps.translations.providers.fake import FakeProvider
    from apps.translations.providers.registry import use_provider
    from tests import catalog_factories as f

    client = login(make_staff(role="Product Manager"))
    product = f.product("tr-api", names={"fa": "کفش Nike مدل X-90"}, brand_obj=f.brand("Nike"))
    r = post(client, "/staff/translations/jobs", {"product": str(product.public_id), "languages": ["en"]})
    assert r.status_code == 202 and len(r.json()["queued"]) == 1
    job_id = r.json()["queued"][0]["id"]
    from apps.translations.services import jobs

    with use_provider(FakeProvider()):
        jobs.run_job(job_id)
    status = client.get(f"{API}/staff/translations/products/{product.public_id}").json()
    assert status["source_language"] == "fa" and status["languages"]["en"]["groups"]["row"] == {
        "origin": "machine",
        "status": "machine",
    }
    assert status["languages"]["fa"] == {"role": "source"} and status["languages"]["en"]["provider"] == "fake"
    fixed = client.put(
        f"{API}/staff/translations/products/{product.public_id}/en",
        {"group": "row", "fields": {"name": "Nike X-90 shoes"}},
        format="json",
    )
    assert fixed.status_code == 200
    assert (
        client.get(f"{API}/staff/translations/products/{product.public_id}").json()["languages"]["en"]["groups"]["row"][
            "origin"
        ]
        == "human"
    )
    assert (
        client.put(
            f"{API}/staff/translations/products/{product.public_id}/en",
            {"group": "row", "fields": {"name": ""}},
            format="json",
        ).status_code
        == 422
    )
    assert post(client, f"/staff/translations/products/{product.public_id}/en/approve", {}).status_code == 200
    summary = client.get(f"{API}/staff/translations/summary").json()
    assert (
        summary["jobs"]["succeeded"] == 1
        and summary["provider"]["live_verified"] is False
        and "key" in summary["provider"]
    )
    assert client.get(f"{API}/staff/translations/jobs?status=succeeded").json()["count"] == 1
    assert post(client, f"/staff/translations/jobs/{job_id}/retry", {}).status_code == 202


def test_reviews_cannot_be_queued_through_the_api(make_staff, login):
    client = login(make_staff(role="Product Manager"))
    r = post(client, "/staff/translations/jobs", {"entity_type": "review", "entity_id": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "reviews_not_translatable"
