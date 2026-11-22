from dataclasses import replace
from decimal import Decimal

import pytest

from apps.catalog.models import (
    Product,
    ProductImage,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
    TranslationOrigin,
    TranslationStatus,
    VariantAvailability,
    VariantPrice,
)
from apps.common.exceptions import BusinessRuleViolation, Conflict
from apps.imports.services import pipeline, readiness
from apps.imports.services.pipeline import ItemFailure
from apps.imports.services.publication import publish
from apps.product_sources.models import (
    ExternalCategoryMapping,
    ExternalProduct,
    MatchStatus,
    SourceAvailability,
    SourcePrice,
)
from apps.product_sources.models import (
    PipelineStatus as S,
)
from apps.product_sources.providers.namshi import NamshiProvider
from apps.product_sources.providers.trendyol import TrendyolProvider
from tests import catalog_factories as f
from tests.imports_helpers import fixture_world, import_one, make_product, make_variant, world

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_enqueue")]


# ------------------------------------------------------------------ validation (step 4)
@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"title": ""}, "missing_title"),
        ({"external_id": ""}, "missing_external_id"),
        ({"variants": ()}, "no_variants"),
        ({"variants": (make_variant("A", price=None),)}, "no_importable_variants"),
        ({"variants": (make_variant("A", price="5", sale="9"),)}, "no_importable_variants"),
        ({"variants": (make_variant("A"), make_variant("A"))}, "duplicate_variant_ids"),
    ],
)
def test_invalid_products_fail_without_touching_the_database(overrides, code):
    src, *_ = world()
    with pytest.raises(ItemFailure) as info:
        import_one(src, make_product(**overrides))
    assert info.value.code == code
    assert ExternalProduct.objects.count() == 0


def test_unsupported_currency_excludes_only_that_variant():
    src, *_ = world()
    p = make_product(variants=[make_variant("A", size="40"), make_variant("B", size="41", currency="JPY")])
    ext = import_one(src, p).ext
    assert ext.variants.count() == 2 and ProductVariant.objects.count() == 1
    assert ext.variants.get(external_variant_id="B").current_price is None


# ------------------------------------------------------------------ save + idempotency (steps 7-9)
def test_first_import_builds_listing_canonical_product_and_variants():
    src, acme, shoes = world()
    r = import_one(src, make_product(variants=[make_variant("A", size="40"), make_variant("B", size="41", qty=0)]))
    ext = r.ext
    product = ext.canonical_product
    assert r.action == "created" and product.status == ProductStatus.DRAFT
    assert (product.brand, product.category, product.source_language) == (acme, shoes, "tr")
    assert (
        ext.match_status == MatchStatus.AUTO_MATCHED
        and ext.matched_by == "rule:new-canonical"
        and ext.is_primary_listing
    )
    assert ext.images.count() == 1 and ext.variants.count() == 2
    variants = {v.source_sku: v for v in product.variants.all()}
    assert set(variants) == {"SKU-A", "SKU-B"}
    assert all(v.fulfillment_type == "PROXY_PURCHASE" and v.source == src for v in variants.values())
    assert [v.availability.status for v in product.variants.order_by("sku")] == ["in_stock", "out_of_stock"]
    row = ProductTranslation.objects.get(product=product)
    assert (row.language, row.origin, row.status) == ("tr", TranslationOrigin.SOURCE, TranslationStatus.APPROVED)
    assert not VariantPrice.objects.exists()  # customer prices come from the pricing phase, never from the importer


def test_reimport_of_identical_data_changes_nothing():
    src, *_ = world()
    p = make_product()
    first = import_one(src, p)
    counts = (SourcePrice.objects.count(), SourceAvailability.objects.count(), Product.objects.count())
    second = import_one(src, p)
    assert second.action == "unchanged" and second.ext.pk == first.ext.pk
    assert (SourcePrice.objects.count(), SourceAvailability.objects.count(), Product.objects.count()) == counts


def test_price_and_stock_history_is_appended_only_when_values_change():
    src, *_ = world()
    import_one(src, make_product(variants=[make_variant("A", price="100", qty=5)], raw={"v": 1}))
    ext = ExternalProduct.objects.get()
    r = import_one(src, make_product(variants=[make_variant("A", price="120", qty=0)], raw={"v": 2}))
    assert r.action == "updated"
    ev = ext.variants.get()
    assert [str(p.list_amount) for p in ev.prices.order_by("id")] == ["100.00", "120.00"]
    assert ev.current_price.list_amount == Decimal("120.00") and ev.current_availability.status == "out_of_stock"
    assert ev.availabilities.count() == 2
    import_one(src, make_product(variants=[make_variant("A", price="120", qty=0)], raw={"v": 3}))
    assert ev.prices.count() == 2  # the payload changed but the price did not
    with pytest.raises(RuntimeError):
        p = ev.prices.first()
        p.list_amount = 1
        p.save()  # price history is append-only


def test_disappearing_variant_is_deactivated_and_marked_unavailable():
    src, *_ = world()
    import_one(src, make_product(variants=[make_variant("A", size="40"), make_variant("B", size="41")], raw={"v": 1}))
    import_one(src, make_product(variants=[make_variant("A", size="40")], raw={"v": 2}))
    ext = ExternalProduct.objects.get()
    assert ext.variants.get(external_variant_id="B").is_active is False


def test_text_change_updates_source_text_and_stales_machine_translations():
    src, *_ = world()
    ext = import_one(src, make_product(title="Old Title", raw={"v": 1})).ext
    product = ext.canonical_product
    ProductTranslation.objects.create(
        product=product,
        language="fa",
        name="قدیمی",
        origin=TranslationOrigin.MACHINE,
        status=TranslationStatus.APPROVED,
        source_text_hash=ext.content_hash,
    )
    ProductTranslation.objects.create(
        product=product, language="en", name="Human", origin=TranslationOrigin.HUMAN, status=TranslationStatus.HUMAN
    )
    import_one(src, make_product(title="New Title", raw={"v": 2}))
    assert ProductTranslation.objects.get(product=product, language="tr").name == "New Title"
    assert ProductTranslation.objects.get(product=product, language="fa").status == TranslationStatus.STALE
    assert ProductTranslation.objects.get(product=product, language="en").status == TranslationStatus.HUMAN  # untouched


# ------------------------------------------------------------------ vocabulary the importer will not guess
def test_unknown_brand_waits_for_staff_then_approval_teaches_the_mapping():
    src, acme, _ = world()
    ext = import_one(src, make_product(brand="Mystery Co")).ext
    assert ext.pipeline_status == S.PENDING_REVIEW and ext.pipeline_reason == "brand_unresolved"
    assert ext.match_status == MatchStatus.NEEDS_REVIEW and ext.canonical_product is None
    staff = object()
    ext = pipeline.approve(ext, None, create_new=True, brand=acme)
    assert ext.canonical_product.brand == acme and ext.match_status == MatchStatus.MANUALLY_MATCHED
    # the mapping is remembered: the next Mystery Co product no longer needs a brand decision
    again = import_one(src, make_product("P9", brand="Mystery Co", title="Different thing"))
    assert again.ext.canonical_product is not None and staff


def test_unmapped_category_holds_the_listing():
    src, acme, _ = world()
    ext = import_one(src, make_product(category_id="zzz", category_path="Gadgets")).ext
    assert (ext.pipeline_status, ext.pipeline_reason) == (S.PENDING_REVIEW, "category_unresolved")
    assert Product.objects.count() == 0
    ext = pipeline.approve(ext, None, create_new=True, category=f.category("gadgets"))
    assert ext.canonical_product.category.slug == "gadgets"


def test_unmapped_size_holds_the_listing_until_mapped():
    src, *_ = world()
    ext = import_one(src, make_product(variants=[make_variant("A", size="XXL-Tall")])).ext
    assert ext.pipeline_status == S.PENDING_REVIEW and ext.pipeline_reason == "option_unmapped:size:XXL-Tall"
    assert ext.canonical_product is not None and ext.canonical_product.variants.count() == 0
    xl = f.option("size", "xl")
    ext = pipeline.approve(ext, None, option_mappings=[{"attribute": "size", "raw": "xxl-tall", "option": xl}])
    assert (
        ext.pipeline_status in (S.TRANSLATION_PENDING, S.PRICING_PENDING)
        and ext.canonical_product.variants.count() == 1
    )


def test_only_listings_pending_review_can_be_approved():
    src, *_ = world()
    ext = import_one(src, make_product()).ext
    with pytest.raises(Conflict) as info:
        pipeline.approve(ext, None, create_new=True)
    assert info.value.detail.code == "not_pending_review"


# ------------------------------------------------------------------ deduplication (step 6)
def test_same_gtin_from_a_trusted_source_links_to_the_existing_product():
    src, acme, _ = world()
    gtin = "8690000001019"
    first = import_one(src, make_product("A", variants=[make_variant("A1", gtin=gtin)])).ext
    other = f.source("namshi", base_currency="AED")
    second = import_one(
        other,
        make_product("N1", title="Totally different words", variants=[make_variant("N1", gtin=gtin, currency="AED")]),
        trusted=True,
    ).ext
    assert second.canonical_product == first.canonical_product
    assert (second.match_status, second.matched_by) == (MatchStatus.AUTO_MATCHED, "rule:T1-gtin")
    assert Product.objects.count() == 1 and first.canonical_product.external_listings.count() == 2
    assert not second.is_primary_listing  # text stays owned by the first listing


def test_shared_gtin_without_a_trusted_side_goes_to_review_not_auto_link():
    src, *_ = world()
    gtin = "8690000001019"
    first = import_one(src, make_product("A", variants=[make_variant("A1", gtin=gtin)]), trusted=False).ext
    second = import_one(
        src, make_product("B", title="Another", variants=[make_variant("B1", gtin=gtin)]), trusted=False
    ).ext
    assert second.pipeline_status == S.PENDING_REVIEW and second.pipeline_reason == "gtin_unverified"
    assert second.canonical_product is None
    candidate = second.match_candidates.get()
    assert candidate.product == first.canonical_product and candidate.signals["shared_gtin"]


def test_gtin_shared_with_two_canonical_products_is_ambiguous():
    src, *_ = world()
    gtin = "8690000001019"
    a = import_one(src, make_product("A", title="Alpha", variants=[make_variant("A1", gtin="")])).ext
    b = import_one(src, make_product("B", title="Beta", variants=[make_variant("B1", gtin="")])).ext
    for ext, v in ((a, "A1"), (b, "B1")):  # data-quality problem: two canonical products carry one GTIN
        ext.variants.filter(external_variant_id=v).update(gtin=gtin, gtin_trusted=True)
    c = import_one(f.source("namshi"), make_product("C", variants=[make_variant("C1", gtin=gtin)])).ext
    assert c.pipeline_reason == "ambiguous_gtin" and c.match_candidates.count() == 2


def test_gtin_with_a_different_brand_is_never_auto_linked():
    src, *_ = world()
    gtin = "8690000001019"
    import_one(src, make_product("A", variants=[make_variant("A1", gtin=gtin)]))
    f.brand("Other Brand")
    r = import_one(f.source("namshi"), make_product("B", brand="Other Brand", variants=[make_variant("B1", gtin=gtin)]))
    assert r.ext.pipeline_reason == "gtin_brand_conflict"


def test_brand_plus_manufacturer_code_links_deterministically():
    src, *_ = world()
    first = import_one(src, make_product("A", manufacturer_code="RUN-1")).ext
    second = import_one(f.source("namshi"), make_product("B", title="Other words", manufacturer_code="RUN-1")).ext
    assert second.canonical_product == first.canonical_product and second.matched_by == "rule:T1-mpn"


def test_similar_listings_need_staff_approval_and_then_share_variants():
    src, *_ = world()
    first = import_one(
        src,
        make_product(
            "A", title="Acme Court Sneaker", variants=[make_variant("A1", size="40"), make_variant("A2", size="41")]
        ),
    ).ext
    other = f.source("namshi", base_currency="AED")
    second = import_one(
        other,
        make_product(
            "B",
            title="Acme Court Sneaker Men",
            variants=[make_variant("B1", size="40", currency="AED"), make_variant("B2", size="41", currency="AED")],
        ),
    ).ext
    assert second.pipeline_status == S.PENDING_REVIEW and second.pipeline_reason == "match_candidates"
    candidate = second.match_candidates.get()
    assert candidate.product == first.canonical_product and float(candidate.score) >= 0.55
    approved = pipeline.approve(second, None, product=first.canonical_product)
    assert (
        approved.canonical_product == first.canonical_product and approved.match_status == MatchStatus.MANUALLY_MATCHED
    )
    assert first.canonical_product.variants.count() == 2  # same sizes: one canonical variant, two sources behind it
    assert approved.variants.first().canonical_variant.external_variants.count() == 2
    candidate.refresh_from_db()
    assert candidate.status == "approved"


def test_contradicting_gender_vetoes_a_soft_match():
    src, *_ = world()
    import_one(src, make_product("A", title="Acme Court Sneaker", gender="men"))
    other = f.source("namshi")
    ExternalCategoryMapping.objects.create(source=other, raw_key="c1", category=f.category("shoes"))
    r = import_one(other, make_product("B", title="Acme Court Sneaker", gender="women"))
    assert r.ext.pipeline_status != S.PENDING_REVIEW and r.ext.match_candidates.count() == 0
    assert Product.objects.count() == 2


def test_unmatched_policy_review_holds_everything_new(settings):
    settings.IMPORTS_UNMATCHED_POLICY = "review"
    src, *_ = world()
    ext = import_one(src, make_product()).ext
    assert ext.pipeline_reason == "unmatched_policy_review"
    approved = pipeline.approve(ext, None, create_new=True)
    assert approved.canonical_product is not None


def test_real_fixtures_link_the_same_sneaker_across_sources():
    tr, ns = fixture_world()
    trendyol, namshi = TrendyolProvider(tr, mode="fixture"), NamshiProvider(ns, mode="fixture")
    for p in trendyol.discover(page_size=50).items:
        import_one(tr, p, trusted=False)
    for p in namshi.discover(page_size=50).items:
        import_one(ns, p, trusted=True)
    sneaker_tr = ExternalProduct.objects.get(source=tr, external_id="9000001")
    sneaker_ns = ExternalProduct.objects.get(source=ns, external_id="NSH-ACM-001")
    assert sneaker_ns.canonical_product == sneaker_tr.canonical_product and sneaker_ns.matched_by == "rule:T1-gtin"
    assert sneaker_tr.canonical_product.variants.count() == 3
    tee = ExternalProduct.objects.get(source=ns, external_id="NSH-ACM-002")
    assert tee.pipeline_status == S.PENDING_REVIEW and tee.pipeline_reason == "match_candidates"
    assert ExternalProduct.objects.get(source=ns, external_id="NSH-ORB-001").pipeline_reason == "brand_unresolved"
    assert (
        ExternalProduct.objects.get(source=ns, external_id="NSH-ZPH-001").pipeline_status == S.ARCHIVED
    )  # inactive at source
    bedding = ExternalProduct.objects.get(source=tr, external_id="9000004")
    assert bedding.pipeline_reason.startswith("option_unmapped:size:160 x 220 cm")


# ------------------------------------------------------------------ staff decisions
def test_reject_archives_and_later_imports_do_not_resurrect_it():
    src, *_ = world()
    ext = import_one(src, make_product(brand="Mystery Co", raw={"v": 1})).ext
    rejected = pipeline.reject(ext, None, "not our range")
    assert (rejected.pipeline_status, rejected.match_status) == (S.ARCHIVED, MatchStatus.REJECTED)
    again = import_one(src, make_product(brand="Mystery Co", raw={"v": 2}))
    assert again.action == "skipped" and again.ext.pipeline_status == S.ARCHIVED and Product.objects.count() == 0


def test_a_published_listing_cannot_be_rejected():
    src, *_ = world()
    ext = ready_listing(src)
    publish(ext)
    with pytest.raises(Conflict):
        pipeline.reject(ext, None)


def test_delisted_listing_is_archived_and_its_variants_become_unavailable():
    src, *_ = world()
    ext = import_one(src, make_product(raw={"v": 1})).ext
    r = import_one(src, replace(make_product(raw={"v": 2}), is_listed=False))
    assert r.action == "delisted"
    ext.refresh_from_db()
    assert (ext.status, ext.pipeline_status) == ("delisted", S.ARCHIVED)
    assert VariantAvailability.objects.get(variant__product=ext.canonical_product).status == "out_of_stock"


# ------------------------------------------------------------------ translation / pricing / publication (steps 10-12)
def ready_listing(src):
    """An imported listing with everything the publication rules require, supplied the way staff would."""
    ext = import_one(src, make_product()).ext
    product = ext.canonical_product
    ProductTranslation.objects.create(
        product=product, language="fa", name="کفش", origin=TranslationOrigin.HUMAN, status=TranslationStatus.HUMAN
    )
    for variant in product.variants.all():
        VariantPrice.objects.create(
            variant=variant, original_amount=100, original_currency="TRY", list_irr=1_000_000, final_irr=900_000
        )
    ProductImage.objects.create(product=product, image="products/x.jpg", rights="owned")
    readiness.refresh_readiness(ext)
    ext.refresh_from_db()
    return ext


def test_import_waits_for_translation_then_pricing_then_is_ready():
    src, *_ = world()
    ext = import_one(src, make_product()).ext
    assert (ext.pipeline_status, ext.pipeline_reason) == (S.TRANSLATION_PENDING, "translation_missing:fa")
    product = ext.canonical_product
    ProductTranslation.objects.create(
        product=product, language="fa", name="کفش", origin=TranslationOrigin.MACHINE, status=TranslationStatus.MACHINE
    )
    assert readiness.refresh_readiness(ext) == S.TRANSLATION_PENDING  # unreviewed machine text is not enough
    ProductTranslation.objects.filter(product=product, language="fa").update(status=TranslationStatus.APPROVED)
    assert readiness.refresh_readiness(ext) == S.PRICING_PENDING
    for variant in product.variants.all():
        VariantPrice.objects.create(
            variant=variant, original_amount=100, original_currency="TRY", list_irr=1_000_000, final_irr=900_000
        )
    assert readiness.refresh_readiness(ext) == S.READY_TO_PUBLISH


def test_queueing_hands_off_through_ports_without_inventing_results(settings, django_capture_on_commit_callbacks):
    from apps.imports import tasks

    src, *_ = world()
    ext = import_one(src, make_product()).ext
    assert tasks.request_translation.apply(args=[ext.pk]).get() == {"queued": False, "reason": "no_translation_handler"}
    assert tasks.request_pricing.apply(args=[ext.pk]).get() == {"queued": False, "reason": "no_pricing_handler"}
    assert not ProductTranslation.objects.filter(product=ext.canonical_product, language="fa").exists()
    settings.IMPORTS_TRANSLATION_HANDLER = f"{__name__}.fake_translate"
    settings.IMPORTS_PRICING_HANDLER = f"{__name__}.fake_price"
    assert tasks.request_translation.apply(args=[ext.pk]).get() == {"queued": True, "targets": ["fa", "en", "ar"]}
    assert tasks.request_pricing.apply(args=[ext.pk]).get() == {"queued": True, "variants": 1}
    assert CALLS == [
        ("translate", ext.canonical_product.pk, ["fa", "en", "ar"]),
        ("price", ext.canonical_product.pk, 1),
    ]


CALLS = []


def fake_translate(product_id, targets):
    CALLS.append(("translate", product_id, targets))


def fake_price(product_id, variant_ids):
    CALLS.append(("price", product_id, len(variant_ids)))


def test_publish_enforces_every_business_rule():
    src, *_ = world()
    ext = import_one(src, make_product()).ext
    with pytest.raises(BusinessRuleViolation) as info:
        publish(ext)
    assert info.value.detail.code == "not_ready_to_publish"
    ext = ready_listing(src)
    ProductImage.objects.all().delete()
    ext.refresh_from_db()
    with pytest.raises(BusinessRuleViolation) as info:
        publish(ext)
    assert "image_missing" in info.value.fields["blockers"]


def test_publish_makes_the_product_visible_and_marks_every_listing():
    src, *_ = world()
    ext = ready_listing(src)
    assert readiness.publication_blockers(ext) == []
    publish(ext)
    ext.refresh_from_db()
    product = ext.canonical_product
    product.refresh_from_db()
    assert product.status == ProductStatus.ACTIVE and ext.pipeline_status == S.PUBLISHED
    assert Product.objects.visible().filter(pk=product.pk).exists()


def test_fixture_data_can_never_be_published_in_production(settings):
    settings.IMPORTS_ALLOW_FIXTURE_PUBLISH = False
    src, *_ = world()
    ext = ready_listing(src)
    assert "fixture_data_cannot_be_published" in readiness.publication_blockers(ext)
    with pytest.raises(BusinessRuleViolation):
        publish(ext)
    ExternalProduct.objects.filter(pk=ext.pk).update(data_origin="live")
    ext.refresh_from_db()
    publish(ext)


def test_listing_gone_from_the_source_blocks_publication():
    src, *_ = world()
    ext = ready_listing(src)
    ExternalProduct.objects.filter(pk=ext.pk).update(status="delisted")
    ext.refresh_from_db()
    assert "listing_not_active_at_source" in readiness.publication_blockers(ext)


def test_auto_publish_is_off_by_default_and_works_when_enabled(settings):
    src, *_ = world()
    ext = ready_listing(src)
    assert ext.pipeline_status == S.READY_TO_PUBLISH  # default: staff decide
    settings.IMPORTS_AUTO_PUBLISH = True
    assert readiness.refresh_readiness(ext) == S.PUBLISHED


def test_unexpected_transitions_are_refused():
    from apps.imports.services.statuses import InvalidTransition, set_status

    ext = import_one(world()[0], make_product()).ext
    with pytest.raises(InvalidTransition):
        set_status(ext, S.DISCOVERED)
