import pytest
from django.core.management import call_command

from apps.catalog.models import ProductListingProjection, ProductListingSource, ProductStatus
from apps.catalog.services import projection

from . import catalog_factories as f

pytestmark = pytest.mark.django_db


def visible(obj):
    return ProductListingProjection.objects.filter(pk=obj.pk).exists()


def test_active_priced_product_is_visible():
    assert visible(f.product("p"))


@pytest.mark.parametrize("status", [ProductStatus.DRAFT, ProductStatus.IN_REVIEW, ProductStatus.ARCHIVED])
def test_non_active_products_are_hidden(status):
    assert not visible(f.product("p", status=status))


def test_product_without_priced_active_variant_is_hidden():
    assert not visible(f.product("a", variants=[]))
    assert not visible(f.product("b", variants=[{"price": False}]))
    assert not visible(f.product("c", variants=[{"active": False}]))


def test_inactive_brand_or_category_hides_products_via_signals():
    brand, cat = f.brand("Hidden"), f.category("hide-me")
    a, b = f.product("a", brand_obj=brand), f.product("b", category_obj=cat)
    assert visible(a) and visible(b)
    brand.is_active = False
    brand.save()
    cat.is_active = False
    cat.save()
    assert not visible(a) and not visible(b)
    brand.is_active = True
    brand.save()
    assert visible(a)


def test_inactive_parent_category_hides_descendants():
    parent = f.category("parent")
    child = f.category("child", parent=parent)
    obj = f.product("p", category_obj=child)
    assert visible(obj)
    parent.is_active = False
    parent.save()
    assert not visible(obj)


def test_projection_follows_variant_changes_without_manual_refresh():
    obj = f.product("p", variants=[{"final": 500}])
    assert ProductListingProjection.objects.get(pk=obj.pk).price_final_irr == 5000
    f.add_variant(obj, final=300)
    assert ProductListingProjection.objects.get(pk=obj.pk).price_final_irr == 3000
    obj.variants.all().update(is_active=False)  # bulk update bypasses signals ...
    projection.refresh_projection(obj.pk)  # ... so bulk writers must call the service
    assert not visible(obj)


def test_display_price_is_the_cheapest_sellable_variant():
    obj = f.product(
        "p",
        variants=[
            {"final": 100, "available": False},
            {"final": 900},
            {"final": 700, "discount": 10, "list_price": 800},
        ],
    )
    proj = ProductListingProjection.objects.get(pk=obj.pk)
    assert proj.price_final_irr == 7000 and proj.discount_percent == 10 and proj.is_available
    assert (proj.min_final_irr, proj.max_final_irr) == (1000, 9000)


def test_all_variants_sold_out_means_unavailable_but_still_listed():
    obj = f.product("p", variants=[{"final": 100, "available": False}, {"final": 200, "available": False}])
    proj = ProductListingProjection.objects.get(pk=obj.pk)
    assert proj.is_available is False and proj.price_final_irr == 1000


@pytest.mark.parametrize(
    "variants,summary,direct_stock",
    [
        ([{"fulfillment": "DIRECT_INVENTORY", "source_code": "maral"}], "DIRECT_INVENTORY", True),
        ([{"fulfillment": "PROXY_PURCHASE"}], "PROXY_PURCHASE", False),
        (
            [{"fulfillment": "DIRECT_INVENTORY", "source_code": "maral"}, {"fulfillment": "PROXY_PURCHASE"}],
            "MIXED",
            True,
        ),
        (
            [
                {"fulfillment": "DIRECT_INVENTORY", "source_code": "maral", "available": False},
                {"fulfillment": "PROXY_PURCHASE"},
            ],
            "PROXY_PURCHASE",
            False,
        ),
    ],
)
def test_fulfillment_summary_distinguishes_types(variants, summary, direct_stock):
    proj = ProductListingProjection.objects.get(pk=f.product("p", variants=variants).pk)
    assert proj.fulfillment_summary == summary and proj.has_direct_stock is direct_stock


def test_listing_sources_track_active_priced_variants():
    obj = f.product("p", variants=[{"source_code": "trendyol"}, {"source_code": "namshi"}])
    assert set(ProductListingSource.objects.filter(product=obj).values_list("source__code", flat=True)) == {
        "trendyol",
        "namshi",
    }


def test_rebuild_command_repairs_drift_and_removes_orphans():
    obj = f.product("p")
    ProductListingProjection.objects.all().delete()
    call_command("rebuild_catalog_projection")
    assert visible(obj)
    result = projection.rebuild_all()
    assert result["visible"] == 1 and result["orphans_removed"] == 0


def test_demo_loader_suspends_per_write_refresh_and_still_ends_consistent(demo_catalog):
    assert ProductListingProjection.objects.count() == 16
