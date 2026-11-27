"""Phase 9: product discovery - sources, snapshots, status, dedup, pricing blockers, availability."""

import pytest

from apps.ai_assistant.constants import Availability, PriceStatus, ProviderStatus
from apps.ai_assistant.models import AssistantConfig
from apps.ai_assistant.services import discovery, nlu, pricing_estimate
from apps.ai_assistant.services.sources import internal
from apps.ai_assistant.services.sources.base import SearchSpec
from apps.product_sources.models import DataOrigin, ProductSource
from tests import ai_helpers as h

pytestmark = pytest.mark.django_db


def run(text="Nike sneaker", **spec_over):
    config = AssistantConfig.load()
    parsed = nlu.parse_message(text)
    spec = SearchSpec(
        language="en",
        concept=parsed.concept,
        brands=parsed.brands,
        brand_ids=parsed.brand_ids,
        keywords=parsed.keywords,
        min_irr=parsed.min_irr,
        max_irr=parsed.max_irr,
        sizes=parsed.sizes,
        colors=parsed.colors,
        sources=["maral", "trendyol", "namshi"],
    )
    for k, v in spec_over.items():
        setattr(spec, k, v)
    return discovery.discover(spec, internal.make_context(None, "fa"), config)


def by_source(result, code):
    return [c for c in result.candidates if c.source == code]


def report(result, code):
    return next(r for r in result.reports if r.source == code)


# ------------------------------------------------------------------------------------------------ internal catalogue
def test_internal_catalog_search_finds_real_products_by_persian_text():
    h.sources()
    h.internal_sneaker()
    result = run("کتونی نایک")
    assert [c.data["title"] for c in result.candidates] == ["کتونی نایک ایر زوم پیاده‌روی"]
    card = result.candidates[0].data
    assert card["source"]["code"] == "maral" and card["kind"] == "internal"
    assert card["prices"]["estimated_maral"]["amount"] == "4000000" and card["prices"]["confirmed"] is None
    assert card["availability"]["status"] == Availability.IN_STOCK


def test_internal_search_never_returns_products_that_do_not_exist():
    h.sources()
    assert run("کتونی نایک").candidates == []


def test_internal_variants_sizes_colors_and_relaxation_is_reported():
    h.sources()
    h.internal_sneaker(size="42", color="black")
    strict = run("کتونی نایک سایز ۴۲ مشکی")
    assert strict.candidates and strict.relaxed == []
    relaxed = run("کتونی نایک سایز ۴۶")
    assert relaxed.candidates and relaxed.relaxed == ["size"]
    assert relaxed.candidates[0].data["match"]["unmet"] == ["size"]


def test_out_of_stock_internal_product_is_flagged_and_dropped_when_alternatives_exist():
    h.sources()
    h.internal_sneaker(available=False, slug="oos", name="کتونی نایک ناموجود")
    h.internal_sneaker(slug="ok", name="کتونی نایک موجود")
    titles = [c.data["title"] for c in run("کتونی نایک").candidates]
    assert titles == ["کتونی نایک موجود"]


# ------------------------------------------------------------------------------------------------ snapshots
def test_snapshots_are_labelled_stored_not_live_and_never_in_stock():
    h.sources()
    h.pricing()
    h.snapshot()
    cand = by_source(run(), "trendyol")[0].data
    assert cand["kind"] == "imported_snapshot" and cand["snapshot"]["live_marketplace_result"] is False
    assert cand["snapshot"]["price_observed_at"] and cand["snapshot"]["data_origin"] == "live"
    assert cand["availability"]["status"] == Availability.NOT_VERIFIED
    assert cand["availability"]["last_known"] == "in_stock" and cand["availability"]["confidence"] == "medium"
    assert cand["source"]["code"] == "trendyol"


def test_snapshot_with_no_stored_availability_is_unknown():
    h.sources()
    h.pricing()
    h.snapshot(variants=(("42", "black", "1000", None),))
    assert by_source(run(), "trendyol")[0].data["availability"]["status"] == Availability.UNKNOWN


def test_old_snapshot_has_low_confidence_and_is_stale():
    h.sources()
    h.pricing()
    h.snapshot(price_age_hours=200)
    cand = by_source(run(), "trendyol")[0].data
    assert cand["availability"]["confidence"] == "low" and cand["snapshot"]["stale"] is True
    assert cand["prices"]["estimated_maral"]["status"] == PriceStatus.PROVISIONAL
    assert "stale_source_price" in cand["prices"]["estimated_maral"]["warnings"]


def test_fixture_snapshots_are_hidden_unless_staff_allows_them():
    h.sources()
    h.pricing()
    h.snapshot(origin=DataOrigin.FIXTURE)
    assert by_source(run(), "trendyol") == []
    h.enable_snapshots(True)
    cand = by_source(run(), "trendyol")[0]
    assert "fixture_data_not_real" in cand.data["warnings"]


def test_marketplace_url_is_withheld_by_default_and_images_follow_the_source_licence():
    h.sources()
    h.pricing()
    h.snapshot()
    cand = by_source(run(), "trendyol")[0].data
    assert cand["product_url"] is None and cand["images"] == [] and cand["images_note"]
    config = AssistantConfig.load()
    config.expose_marketplace_urls = True  # staff switch for the URL only
    config.save()
    cand = by_source(run(), "trendyol")[0].data
    assert cand["product_url"] == "https://www.trendyol.com/p-1"
    assert cand["images"] == []  # the source's terms still forbid image copying: no config switch can override that
    ProductSource.objects.filter(code="trendyol").update(allows_image_copy=True)
    cand = by_source(run(), "trendyol")[0].data
    assert cand["images"][0]["url"] == "https://img.test/1.jpg" and cand["images_note"] is None


def test_snapshot_size_and_color_filters_use_stored_variants_only():
    h.sources()
    h.pricing()
    h.snapshot(variants=(("42", "black", "1000", "in_stock"), ("43", "white", "1100", "out_of_stock")))
    assert by_source(run("Nike sneaker size 42"), "trendyol")
    assert by_source(run("Nike sneaker size 44"), "trendyol") == []  # never invents a size
    assert by_source(run("Nike sneaker white"), "trendyol")


# ------------------------------------------------------------------------------------------------ source status
def test_external_sources_report_authorization_required_never_live_searched():
    h.sources()
    h.pricing()
    result = run()
    for code in ("trendyol", "namshi"):
        r = report(result, code)
        assert r.live_search == "disabled" and r.live_search_status == ProviderStatus.AUTHORIZATION_REQUIRED
        assert r.status == ProviderStatus.AUTHORIZATION_REQUIRED  # never "AVAILABLE": no live access exists
        assert r.snapshot_status == ProviderStatus.AVAILABLE and r.mode == "imported_snapshot"
    assert report(result, "maral").status == ProviderStatus.AVAILABLE


def test_authorised_source_without_search_capability_is_unsupported_not_live():
    h.sources()
    h.pricing()
    ProductSource.objects.filter(code="trendyol").update(
        integration_type="official_api", terms_note="Written permission on file"
    )
    r = report(run(), "trendyol")
    assert r.live_search_status in (ProviderStatus.UNSUPPORTED, ProviderStatus.AUTHORIZATION_REQUIRED)
    assert r.live_search == "disabled"


def test_missing_or_inactive_source_is_not_configured():
    h.sources()
    ProductSource.objects.filter(code="namshi").update(is_active=False)
    assert report(run(), "namshi").status == ProviderStatus.NOT_CONFIGURED
    assert report(run(), "namshi").searched is False
    ProductSource.objects.filter(code="trendyol").delete()
    assert report(run(), "trendyol").status == ProviderStatus.NOT_CONFIGURED


def test_source_disabled_by_staff_is_not_searched():
    h.sources()
    h.pricing()
    h.snapshot()
    config = AssistantConfig.load()
    config.enabled_sources = ["maral"]
    config.save()
    result = run()
    assert by_source(result, "trendyol") == []
    assert (
        report(result, "trendyol").status == ProviderStatus.NOT_CONFIGURED and not report(result, "trendyol").searched
    )


def test_failing_source_does_not_stop_the_others(monkeypatch):
    h.sources()
    h.pricing()
    h.internal_sneaker()
    h.snapshot()
    from apps.ai_assistant.services.sources import snapshots

    def boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(snapshots, "search", boom)
    result = run("کتونی نایک")
    assert by_source(result, "maral") and report(result, "trendyol").status == ProviderStatus.ERROR
    assert "db exploded" not in report(result, "trendyol").detail  # error text is never leaked


def test_failing_internal_search_is_disclosed_and_external_continues(monkeypatch):
    h.sources()
    h.pricing()
    h.snapshot()
    monkeypatch.setattr(internal, "search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    result = run()
    assert report(result, "maral").status == ProviderStatus.ERROR and by_source(result, "trendyol")


def test_combined_multi_source_results_keep_their_source_identity():
    h.sources()
    h.pricing()
    h.internal_sneaker()
    h.snapshot("trendyol", "T1")
    h.snapshot("namshi", "N1", title="Nike Air Zoom Sneaker AE", variants=(("42", "black", "300", "in_stock"),))
    result = run("Nike sneaker")
    assert {c.source for c in result.candidates} == {"maral", "trendyol", "namshi"}
    assert {c.data["source"]["code"] for c in result.candidates} == {"maral", "trendyol", "namshi"}


def test_only_requested_sources_are_searched():
    h.sources()
    h.pricing()
    h.snapshot("trendyol", "T1")
    h.snapshot("namshi", "N1", variants=(("42", "black", "300", "in_stock"),))
    result = run(sources=["namshi"])
    assert [r.source for r in result.reports] == ["namshi"] and {c.source for c in result.candidates} == {"namshi"}


# ------------------------------------------------------------------------------------------------ dedup
def test_similar_names_alone_never_merge_products():
    h.sources()
    h.pricing()
    h.internal_sneaker(name="Nike Air Zoom Running Sneaker")
    h.snapshot(title="Nike Air Zoom Running Sneaker")  # identical title, but never matched by Phase 5
    assert len(run("Nike sneaker").candidates) == 2


def test_snapshot_matched_to_a_shown_product_is_folded_into_it():
    h.sources()
    h.pricing()
    product = h.internal_sneaker(name="Nike Air Zoom Running Sneaker")
    h.snapshot(canonical=product, match="auto_matched")
    result = run("Nike sneaker")
    assert len(result.candidates) == 1
    also = result.candidates[0].data["also_listed_at"]
    assert also[0]["source"] == "trendyol" and also[0]["kind"] == "imported_snapshot"


def test_unreviewed_match_is_not_used_for_dedup():
    h.sources()
    h.pricing()
    product = h.internal_sneaker(name="Nike Air Zoom Running Sneaker")
    h.snapshot(canonical=product, match="needs_review")
    assert len(run("Nike sneaker").candidates) == 2


# ------------------------------------------------------------------------------------------------ prices
def test_estimate_uses_phase6_engine_and_records_rule_version_and_time():
    h.sources()
    rs = h.pricing()
    h.snapshot()
    est = by_source(run(), "trendyol")[0].data["prices"]["estimated_maral"]
    # 1000 TRY x 15000 = 15,000,000 IRR + shipping 3,000,000 + 10% customs, then 25% markup (see PHASE6 S1): 2,475,000 Toman
    assert est["amount"] == "2475000" and est["currency"] == "IRT"
    assert est["status"] == PriceStatus.ESTIMATED and est["provisional"] is False
    assert est["rule_version"] == rs.version and est["calculated_at"] and est["fx"]["rate"].startswith("15000")


def test_original_estimated_and_confirmed_prices_are_distinct():
    h.sources()
    h.pricing()
    h.snapshot()
    prices = by_source(run(), "trendyol")[0].data["prices"]
    assert prices["original"]["amount"] == "1000" and prices["original"]["currency"] == "TRY"
    assert prices["estimated_maral"]["currency"] == "IRT" and prices["confirmed"] is None


def test_missing_shipping_and_customs_are_provisional_never_zero():
    h.sources()
    h.pricing(with_costs=False)
    h.snapshot()
    est = by_source(run(), "trendyol")[0].data["prices"]["estimated_maral"]
    assert est["status"] == PriceStatus.PROVISIONAL and est["provisional"] is True
    assert est["missing_costs"] == ["shipping", "customs"]


def test_no_exchange_rate_blocks_the_estimate():
    h.sources()
    from tests import pricing_helpers as ph

    ph.make_ruleset(rules=h.SHIPPING_CUSTOMS_RULES)  # rule set but no rates
    h.snapshot()
    est = by_source(run(), "trendyol")[0].data["prices"]["estimated_maral"]
    assert est["amount"] is None and est["status"] == PriceStatus.INCOMPLETE and est["blockers"]


def test_no_published_rule_set_gives_an_incomplete_price_not_a_guess():
    h.sources()
    h.snapshot()
    est = by_source(run(), "trendyol")[0].data["prices"]["estimated_maral"]
    assert est["amount"] is None and est["blockers"][0]["code"] == "no_published_ruleset"


def test_snapshot_without_a_source_price_is_unavailable():
    h.sources()
    h.pricing()
    h.snapshot(variants=(("42", "black", None, "in_stock"),))
    est = by_source(run(), "trendyol")[0].data["prices"]["estimated_maral"]
    assert est["amount"] is None and est["status"] == PriceStatus.UNAVAILABLE


def test_budget_filter_uses_estimated_price_and_excludes_unpriced_snapshots():
    h.sources()
    h.pricing()
    h.snapshot("trendyol", "T1")  # 2,475,000 Toman
    h.snapshot("trendyol", "T2", title="Nike Pegasus Sneaker", variants=(("42", "black", None, "in_stock"),))
    assert [c.pk for c in by_source(run("Nike sneaker under 3 million toman"), "trendyol")] != []
    over = run("Nike sneaker under 2 million toman")
    assert by_source(over, "trendyol") == []
    within = run("Nike sneaker under 3 million toman")
    assert report(within, "trendyol").excluded_no_price == 1  # the unpriced one cannot be verified against a budget


def test_phase6_pricing_is_not_duplicated():
    import inspect

    source = inspect.getsource(pricing_estimate)
    assert "calculate(inp, rule_set)" in source and "effective_amounts" in source


# ------------------------------------------------------------------------------------------------ ranking
def test_cheaper_sort_orders_by_estimated_price_and_unpriced_last():
    h.sources()
    h.pricing()
    h.snapshot("trendyol", "T1", variants=(("42", "black", "2000", "in_stock"),))
    h.snapshot("trendyol", "T2", title="Nike Pegasus Sneaker", variants=(("42", "black", "1000", "in_stock"),))
    h.snapshot("trendyol", "T3", title="Nike Vomero Sneaker", variants=(("42", "black", None, "in_stock"),))
    prices = [c.price_irr for c in run("Nike sneaker", sort="price_asc").candidates]
    assert prices[0] < prices[1] and prices[-1] is None


def test_ranking_prefers_verified_in_stock_internal_over_unverified_snapshot():
    h.sources()
    h.pricing()
    h.internal_sneaker(name="Nike Air Zoom Running Sneaker")
    h.snapshot(title="Nike Pegasus Sneaker")
    assert run("Nike sneaker").candidates[0].source == "maral"
