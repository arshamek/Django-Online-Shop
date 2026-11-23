"""Part G: price history, non-destructive repricing, manual-price protection, concurrent updates."""

from decimal import Decimal as D

import pytest
from django.db import IntegrityError

from apps.catalog.models import PriceOrigin, VariantPrice
from apps.common.exceptions import Conflict
from apps.currencies.services import manual
from apps.pricing import tasks
from apps.pricing.models import PriceCause, PriceHistory, PriceOutcome
from apps.pricing.services import repricing
from tests.pricing_helpers import SCENARIOS, change_source_price, fx, make_ruleset, priced_variant, rate, route

pytestmark = pytest.mark.django_db


def s1(**extra):
    scenario = SCENARIOS["scenarios"][0]
    return make_ruleset(rules=scenario["rules"], **{**scenario["ruleset"], **extra})


def price_of(variant):
    return VariantPrice.objects.get(variant=variant)


def test_first_reprice_writes_the_storefront_price_and_a_history_row():
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    result = repricing.reprice_variant(v.pk, cause=PriceCause.INITIAL)
    assert result.outcome == "applied" and result.final_irr == D("25700000")
    price = price_of(v)
    assert price.final_irr == D("25700000") and price.origin == PriceOrigin.PRICING_ENGINE
    assert price.original_currency == "TRY" and price.original_amount == D("1000")
    row = PriceHistory.objects.get(variant=v)
    assert (row.sequence, row.outcome, row.cause, row.old_final_irr, row.new_final_irr, row.rule_version) == (
        1,
        "applied",
        "initial",
        None,
        D("25700000"),
        1,
    )
    assert row.calculation["unit_price_irr"] == "25700000" and row.rate_id is not None


def test_rate_change_reprices_and_history_records_old_and_new():
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk, cause=PriceCause.INITIAL)
    rate("TRY", "16000")
    # the manual/automatic rate insert emits rates_changed; drive it explicitly like the scheduler would
    assert tasks.reprice_currency.apply(args=[["TRY"]]).get() == 1
    price = price_of(v)
    assert price.final_irr > D("25700000")
    rows = list(PriceHistory.objects.filter(variant=v).order_by("sequence"))
    assert (
        [r.sequence for r in rows] == [1, 2]
        and rows[1].old_final_irr == D("25700000")
        and rows[1].cause == "rate_change"
    )


def test_unchanged_price_does_not_add_history():
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk)
    assert repricing.reprice_variant(v.pk).outcome == "unchanged"
    assert PriceHistory.objects.filter(variant=v).count() == 1


def test_source_price_change_is_picked_up_by_the_scheduler_task():
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk)
    change_source_price(v, "1200")
    assert tasks.reprice_changed_sources.apply().get() == 1
    assert price_of(v).original_amount == D("1200")


def test_discount_uses_the_source_list_price():
    fx()
    s1()
    v = priced_variant("1250", "TRY", sale="1000")  # list 1250, sale 1000
    repricing.reprice_variant(v.pk)
    price = price_of(v)
    assert price.final_irr == D("25700000") and price.list_irr > price.final_irr
    assert price.discount_percent == ((price.list_irr - price.final_irr) / price.list_irr * 100).quantize(D("0.01"))


def test_missing_rate_leaves_the_existing_price_untouched_and_records_why():
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk)
    route("TRY", max_age=1)
    from apps.currencies.models import ExchangeRate

    ExchangeRate.objects.filter(base_id="TRY").update(
        rate_at=__import__("django").utils.timezone.now() - __import__("datetime").timedelta(hours=5)
    )
    result = repricing.reprice_variant(v.pk, cause=PriceCause.RATE_CHANGE)
    assert result.outcome == "blocked" and "rate_stale" in result.reason
    assert price_of(v).final_irr == D("25700000")  # never half-calculated, never zero
    blocked = PriceHistory.objects.filter(variant=v, outcome=PriceOutcome.BLOCKED).get()
    assert blocked.old_final_irr == D("25700000") and blocked.new_final_irr is None
    repricing.reprice_variant(v.pk)  # same reason again: no flood
    assert PriceHistory.objects.filter(variant=v, outcome=PriceOutcome.BLOCKED).count() == 1


def test_pending_decision_blocks_the_first_price():
    fx()
    make_ruleset(profit_method="pending")
    v = priced_variant("1000", "TRY")
    assert repricing.reprice_variant(v.pk).outcome == "blocked"
    assert not VariantPrice.objects.filter(variant=v).exists()


def test_manual_prices_are_never_overwritten_unless_forced():
    fx()
    s1()
    v = priced_variant("1000", "TRY", price=True)  # factory price is origin=manual
    assert repricing.reprice_variant(v.pk).outcome == "skipped"
    assert repricing.reprice_variant(v.pk, force=True).outcome == "applied"
    assert price_of(v).origin == PriceOrigin.PRICING_ENGINE


def test_inactive_or_unknown_variants_are_skipped():
    fx()
    s1()
    assert repricing.reprice_variant(999999).outcome == "skipped"


def test_publishing_a_new_rule_set_reprices_variants():
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk)
    s1(target_profit_percent="30")  # publishes v2 -> on_commit -> reprice_all (eager in tests)
    assert price_of(v).final_irr > D("25700000")
    last = PriceHistory.objects.filter(variant=v).order_by("-sequence").first()
    assert last.cause == "rule_change" and last.rule_version == 2


def test_manual_rate_change_flows_through_to_prices(make_staff):
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk)
    route("TRY", sources=("manual", "bureau"))
    manual.set_manual_rate(base="TRY", quote="IRR", rate="18000", actor=make_staff(superuser=True), note="agent rate")
    assert price_of(v).final_irr > D("25700000")


# ------------------------------------------------------------------------------ concurrency
def test_concurrent_writer_collision_is_detected_and_retried(monkeypatch):
    """Another process takes the next history sequence between our read and our write: we retry, no duplicate/lost row."""
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk)
    rate("TRY", "16000")
    real_create = PriceHistory.objects.create
    state = {"first": True}

    def racing_create(**kwargs):
        if state["first"]:
            state["first"] = False
            real_create(
                variant=kwargs["variant"],
                sequence=kwargs["sequence"],
                outcome="applied",
                cause="manual",
                new_final_irr=D("1"),
                rule_version=1,
            )  # the "other process" wins the sequence number
        return real_create(**kwargs)

    monkeypatch.setattr(PriceHistory.objects, "create", racing_create)
    result = repricing.reprice_variant(v.pk, cause=PriceCause.RATE_CHANGE)
    assert result.outcome == "applied"
    seqs = list(PriceHistory.objects.filter(variant=v).order_by("sequence").values_list("sequence", flat=True))
    assert seqs == sorted(set(seqs))  # unique and ordered


def test_persistent_collision_surfaces_as_a_conflict(monkeypatch):
    fx()
    s1()
    v = priced_variant("1000", "TRY")

    def always_fail(**kwargs):
        raise IntegrityError("simulated sequence collision")

    monkeypatch.setattr(PriceHistory.objects, "create", always_fail)
    with pytest.raises(Conflict) as e:
        repricing.reprice_variant(v.pk, retries=1)
    assert e.value.get_codes() == "price_update_conflict"
    assert not VariantPrice.objects.filter(variant=v).exists()  # the failed attempt rolled back completely


def test_history_sequence_is_unique_per_variant():
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk)
    with pytest.raises(IntegrityError):
        PriceHistory.objects.create(variant=v, sequence=1, outcome="applied", cause="manual")


def test_history_rows_are_append_only():
    fx()
    s1()
    v = priced_variant("1000", "TRY")
    repricing.reprice_variant(v.pk)
    row = PriceHistory.objects.get(variant=v)
    with pytest.raises(RuntimeError):
        row.new_final_irr = D("1")
        row.save()
    with pytest.raises(RuntimeError):
        row.delete()


def test_batch_reprice_contains_failures():
    fx()
    s1()
    ok = priced_variant("1000", "TRY")
    counts = repricing.reprice_variants([ok.pk, 424242])
    assert counts["applied"] == 1 and counts["skipped"] == 1
