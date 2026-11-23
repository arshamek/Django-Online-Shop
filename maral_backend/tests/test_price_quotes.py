"""Part D/G: quotes keep the exact accepted numbers; expiry; locking; never silently changed by rates or rules."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.common.exceptions import Conflict
from apps.pricing.errors import PriceUnavailable
from apps.pricing.models import PriceQuote, PriceQuoteComponent, QuoteStatus
from apps.pricing.services import quotes, rulesets
from tests.pricing_helpers import SCENARIOS, fx, make_ruleset, priced_variant, rate, route

pytestmark = pytest.mark.django_db


def s1_ruleset(**extra):
    scenario = SCENARIOS["scenarios"][0]
    return make_ruleset(rules=scenario["rules"], **{**scenario["ruleset"], **extra})


def new_quote(variant=None, qty=2, currency="IRT", **kw):
    variant = variant or priced_variant("1000", "TRY")
    return quotes.create_quote(items=[(variant, qty)], currency=currency, **kw)


def test_quote_records_everything_the_brief_asks_for():
    fx()
    s1_ruleset()
    before = timezone.now()
    quote = new_quote()
    item = quote.items.get()
    assert (item.original_amount, item.original_currency) == (D("1000"), "TRY")
    assert item.rate_applied == D("15000") and item.rate_source == "bureau" and item.rate_at is not None
    assert [c.kind for c in item.components.all()] == [
        "item",
        "shipping",
        "customs",
        "service_fee",
        "profit",
        "rounding",
    ]
    assert item.profit_irr == D("5137500") and item.unit_price_irr == D("25700000")
    assert (
        quote.display_currency_id == "IRT" and item.unit_display == D("2570000") and quote.total_display == D("5140000")
    )
    assert quote.rule_version == 1 and quote.status == QuoteStatus.OPEN
    assert quote.created_at >= before and quote.expires_at > quote.created_at
    assert item.line_price_irr == item.unit_price_irr * 2 == D("51400000") and quote.total_irr == D("51400000")
    quotes.verify_quote(quote)


def test_quote_in_rial_and_in_a_foreign_display_currency():
    fx()
    s1_ruleset()
    v = priced_variant("1000", "TRY")
    irr = quotes.create_quote(items=[(v, 1)], currency="IRR")
    usd = quotes.create_quote(items=[(v, 1)], currency="USD")
    assert irr.total_display == irr.total_irr == D("25700000")
    assert usd.display_rate == D("550000") and usd.total_display == (D("25700000") / D("550000")).quantize(D("0.01"))


def test_separate_charges_appear_on_the_quote_total_but_not_in_the_product_price():
    fx()
    scenario = SCENARIOS["scenarios"][2]
    make_ruleset(rules=scenario["rules"], **scenario["ruleset"])
    quote = new_quote(qty=3, currency="IRR")
    assert (
        quote.subtotal_irr == D("65700000") and quote.separate_irr == D("9000000") and quote.total_irr == D("74700000")
    )
    quotes.verify_quote(quote)


def test_missing_rate_refuses_the_quote_with_blockers():
    s1_ruleset()  # no routes, no rates
    with pytest.raises(PriceUnavailable) as e:
        new_quote()
    assert e.value.blockers[0]["code"] == "no_rate_route"


def test_stale_rate_refuses_the_quote():
    route("TRY", max_age=600)
    rate("TRY", "15000", age_seconds=1200)
    s1_ruleset()
    with pytest.raises(PriceUnavailable) as e:
        new_quote()
    assert "rate_stale" in {b["code"] for b in e.value.blockers}


def test_pending_business_decision_refuses_the_quote():
    fx()
    make_ruleset(profit_method="pending")
    with pytest.raises(PriceUnavailable) as e:
        new_quote()
    assert "profit_method_pending" in {b["code"] for b in e.value.blockers}


def test_item_without_source_price_cannot_be_quoted():
    fx()
    s1_ruleset()
    from tests import catalog_factories as f

    bare = f.product("bare", variants=[{"price": False}]).variants.get()
    with pytest.raises(PriceUnavailable) as e:
        new_quote(variant=bare)
    assert e.value.blockers[0]["code"] == "no_source_price"


def test_no_published_ruleset_refuses():
    fx()
    with pytest.raises(PriceUnavailable) as e:
        new_quote()
    assert e.value.get_codes() == "no_published_ruleset"


def test_input_validation():
    fx()
    s1_ruleset()
    v = priced_variant()
    from apps.common.exceptions import BusinessRuleViolation

    for items, currency in [
        ([], "IRT"),
        ([(v, 0)], "IRT"),
        ([(v, 100)], "IRT"),
        ([(v, 1)], "XXX"),
        ([(v, 1)], "IRT") and ([(v, 1)], "GBP"),
    ]:
        with pytest.raises(BusinessRuleViolation):
            quotes.create_quote(items=items, currency=currency)


# ------------------------------------------------------------------------------ expiry
def test_quote_expires_after_the_configured_validity():
    fx()
    s1_ruleset(quote_validity_minutes=15)
    now = timezone.now()
    quote = new_quote(now=now)
    assert quote.expires_at == now + timedelta(minutes=15)


def test_quote_never_outlives_its_exchange_rate():
    route("TRY", max_age=600)
    rate("TRY", "15000", age_seconds=500)  # only 100 s of validity left
    s1_ruleset(quote_validity_minutes=60)
    now = timezone.now()
    quote = new_quote(now=now)
    assert quote.expires_at <= now + timedelta(seconds=101)


def test_expired_quote_cannot_be_locked_and_sweep_expires_open_quotes():
    fx()
    s1_ruleset(quote_validity_minutes=5)
    quote = new_quote()
    later = timezone.now() + timedelta(minutes=6)
    assert quote.is_expired is False
    with pytest.raises(Conflict) as e:
        quotes.lock_quote(quote.pk, reference="ORD-1", now=later)
    assert e.value.get_codes() == "quote_expired"
    assert quotes.expire_due(now=later) == 1
    quote.refresh_from_db()
    assert quote.status == QuoteStatus.EXPIRED and quote.expired_at is not None


def test_explicit_expiration_is_idempotent():
    fx()
    s1_ruleset()
    quote = new_quote()
    assert quotes.expire_quote(quote.pk).status == QuoteStatus.EXPIRED
    assert quotes.expire_quote(quote.pk).status == QuoteStatus.EXPIRED
    with pytest.raises(Conflict):
        quotes.lock_quote(quote.pk, reference="ORD-1")


def test_expire_task_sweeps_due_quotes():
    from apps.pricing import tasks

    fx()
    s1_ruleset(quote_validity_minutes=1)
    quote = new_quote()
    past = timezone.now() - timedelta(minutes=5)
    PriceQuote.objects.filter(pk=quote.pk).update(created_at=past - timedelta(minutes=1), expires_at=past)
    assert tasks.expire_quotes.apply().get() == 1


# ------------------------------------------------------------------------------ a confirmed order keeps its quote
def snapshot(quote):
    item = quote.items.get()
    return (
        quote.total_irr,
        quote.total_display,
        quote.calculation_hash,
        item.unit_price_irr,
        item.rate_applied,
        item.rate_at,
        item.profit_irr,
        item.rule_version,
        [(c.kind, c.amount_irr) for c in item.components.all()],
    )


def test_locked_quote_is_untouched_by_new_rates_and_new_rules():
    fx()
    s1_ruleset()
    quote = new_quote()
    quotes.lock_quote(quote.pk, reference="ORD-100")
    frozen = snapshot(quote)

    rate("TRY", "30000")  # the lira doubles
    scenario = SCENARIOS["scenarios"][1]
    make_ruleset(
        rules=scenario["rules"], **{**scenario["ruleset"], "profit_method": "margin", "target_profit_percent": "40"}
    )
    quotes.expire_due(now=timezone.now() + timedelta(days=30))
    from apps.pricing import tasks

    tasks.reprice_all.apply()

    quote = PriceQuote.objects.get(pk=quote.pk)
    assert quote.status == QuoteStatus.LOCKED and quote.rule_version == 1
    assert snapshot(quote) == frozen
    assert quote.locked_reference == "ORD-100"


def test_locking_is_idempotent_for_the_same_order_and_exclusive_otherwise():
    fx()
    s1_ruleset()
    quote = new_quote()
    quotes.lock_quote(quote.pk, reference="ORD-1")
    assert quotes.lock_quote(quote.pk, reference="ORD-1").status == QuoteStatus.LOCKED
    with pytest.raises(Conflict) as e:
        quotes.lock_quote(quote.pk, reference="ORD-2")
    assert e.value.get_codes() == "quote_already_locked"
    with pytest.raises(Conflict) as e:
        quotes.expire_quote(quote.pk)
    assert e.value.get_codes() == "quote_locked"


def test_one_quote_cannot_back_two_orders_even_directly_in_the_database():
    fx()
    s1_ruleset()
    a, b = new_quote(), new_quote()
    quotes.lock_quote(a.pk, reference="ORD-1")
    from django.db import IntegrityError, transaction

    b.status, b.locked_reference = QuoteStatus.LOCKED, "ORD-1"
    with pytest.raises(IntegrityError), transaction.atomic():
        b.save(update_fields=["status", "locked_reference"])


def test_quote_rows_are_immutable_snapshots():
    fx()
    s1_ruleset()
    quote = new_quote()
    item = quote.items.get()
    quote.total_irr = D("1")
    with pytest.raises(RuntimeError):
        quote.save()
    with pytest.raises(RuntimeError):
        quote.save(update_fields=["total_irr"])
    item.unit_price_irr = D("1")
    with pytest.raises(RuntimeError):
        item.save()
    with pytest.raises(RuntimeError):
        item.delete()
    comp = PriceQuoteComponent.objects.filter(item=item).first()
    with pytest.raises(RuntimeError):
        comp.delete()
    with pytest.raises(RuntimeError):
        PriceQuote.objects.get(pk=quote.pk).delete()


def test_published_rule_set_is_immutable_and_quote_keeps_its_version():
    fx()
    rs = s1_ruleset()
    quote = new_quote()
    from django.core.exceptions import ValidationError

    rs.profit_method = "margin"
    with pytest.raises(ValidationError):
        rs.save()
    with pytest.raises(ValidationError):
        rs.rules.first().delete()
    draft = rulesets.create_draft(from_set=rs)
    draft.target_profit_percent = D("40")
    draft.save()
    rulesets.publish(draft)
    assert rulesets.active_ruleset().version == 2
    quote.refresh_from_db()
    assert quote.rule_set_id == rs.pk and quote.rule_version == 1
    rs.refresh_from_db()
    assert rs.status == "archived"
