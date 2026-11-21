import pytest
from rest_framework.test import APIClient

from apps.catalog.models import DeliveryEstimateRule, FulfillmentType
from apps.reviews.models import PublicationRights, Review, ReviewStatus

from . import catalog_factories as f
from .conftest import API

pytestmark = pytest.mark.django_db


def detail(slug, **params):
    return APIClient().get(f"{API}/products/{slug}", params or None)


def test_detail_contains_everything_the_product_page_needs(demo_catalog):
    resp = detail("belted-wool-coat")
    assert resp.status_code == 200
    d = resp.data
    assert d["name"] == "پالتو بلند کمربنددار پشمی" and "کشمیر" in d["description"]
    assert d["brand"]["slug"] == "atelier-nour"
    assert d["category"]["breadcrumb"] == [{"slug": "women", "name": "پوشاک زنانه"}]
    assert len(d["images"]) == 2 and all(i["url"] and i["thumbnail_url"] and i["alt"] for i in d["images"])
    assert d["specifications"] == [{"code": "material", "label": "جنس", "value": "پشم و کشمیر"}]
    assert d["source"] == {"code": "trendyol", "name": "Trendyol", "country_code": "TR", "is_direct_inventory": False}
    assert d["fulfillment_type"] == "PROXY_PURCHASE" and d["available"] is True
    assert d["price"]["original"] == {"amount": "8900", "currency": "TRY"}
    assert d["price"]["breakdown"] is None  # the pricing phase has not defined fees / FX yet
    assert d["seo"]["title"] == d["name"]
    assert len(d["related"]) == 4 and "belted-wool-coat" not in [r["slug"] for r in d["related"]]


def test_variants_expose_options_source_fulfillment_and_availability(demo_catalog):
    variants = detail("belted-wool-coat").data["variants"]
    assert len(variants) == 8
    xl = [v for v in variants if v["size"]["code"] == "xl"]
    assert len(xl) == 2 and all(v["available"] is False and v["availability_status"] == "out_of_stock" for v in xl)
    camel_s = next(v for v in variants if v["sku"] == "BELTED-WOOL-COAT-S-CAMEL")
    assert camel_s["available"] is True and camel_s["color"]["value"].startswith("oklch")
    assert camel_s["fulfillment_type"] == "PROXY_PURCHASE" and camel_s["source"]["code"] == "trendyol"


def test_available_sizes_and_colors_reflect_variant_availability(demo_catalog):
    d = detail("belted-wool-coat").data
    assert {s["code"]: s["available"] for s in d["sizes"]} == {"s": True, "m": True, "l": True, "xl": False}
    assert all(c["available"] for c in d["colors"])
    unavailable = detail("white-leather-sneakers").data
    assert unavailable["available"] is False and not any(s["available"] for s in unavailable["sizes"])


def test_price_range_when_variants_differ(demo_catalog):
    price = detail("amber-wood-parfum").data["price"]
    assert price["final"]["amount"] == "5865000"  # display price = cheapest sellable variant (50ml)
    assert price["range"] == {
        "min": {"amount": "5865000", "currency": "IRT"},
        "max": {"amount": "9384000", "currency": "IRT"},
    }
    assert detail("belted-wool-coat").data["price"]["range"] is None


def test_direct_inventory_product(demo_catalog):
    d = detail("cable-knit-turtleneck").data
    assert d["fulfillment_type"] == "DIRECT_INVENTORY" and d["fulfillment"] == "stock"
    assert d["price"]["discount_percent"] == 0 and d["price"]["list"] == d["price"]["final"]


def test_mixed_fulfillment_is_reported_as_mixed_and_order():
    f.product(
        "mix",
        variants=[
            {"size": "s", "fulfillment": "DIRECT_INVENTORY", "source_code": "maral"},
            {"size": "m", "fulfillment": "PROXY_PURCHASE"},
        ],
    )
    d = detail("mix").data
    assert d["fulfillment_type"] == "MIXED" and d["fulfillment"] == "order"
    assert {v["fulfillment_type"] for v in d["variants"]} == {"DIRECT_INVENTORY", "PROXY_PURCHASE"}


def test_delivery_estimate_only_from_real_rules():
    f.product("p", variants=[{"size": "s"}])
    assert detail("p").data["delivery_estimate"] is None
    assert detail("p").data["variants"][0]["delivery_estimate"] is None
    DeliveryEstimateRule.objects.create(fulfillment_type=FulfillmentType.PROXY_PURCHASE, min_days=14, max_days=21)
    assert detail("p").data["delivery_estimate"] == {"min_days": 14, "max_days": 21, "unit": "working_days"}
    special = f.source("namshi")
    DeliveryEstimateRule.objects.create(
        fulfillment_type=FulfillmentType.PROXY_PURCHASE, source=special, min_days=5, max_days=7
    )
    f.product("q", variants=[{"source_code": "namshi"}])
    assert detail("q").data["delivery_estimate"]["min_days"] == 5  # source-specific rule wins over the generic one


def test_reviews_are_returned_only_with_publication_rights(demo_catalog):
    reviews = detail("belted-wool-coat").data["reviews"]
    assert reviews["count"] == 2 and reviews["average"] == 4.5
    assert {r["author_name"] for r in reviews["items"]} == {"سارا", "Reza"}
    assert detail("cable-knit-turtleneck").data["reviews"] == {"count": 0, "average": None, "items": []}


@pytest.mark.parametrize(
    "status,rights",
    [
        (ReviewStatus.PENDING, PublicationRights.CUSTOMER_CONSENT),
        (ReviewStatus.REJECTED, PublicationRights.LICENSED),
        (ReviewStatus.PUBLISHED, PublicationRights.NONE),
    ],
)
def test_unpublishable_reviews_never_leak(status, rights):
    obj = f.product("p")
    Review.objects.create(product=obj, author_name="x", rating=5, status=status, publication_rights=rights)
    assert detail("p").data["reviews"]["count"] == 0


def test_localised_description_and_fallback_chain(demo_catalog):
    en = detail("belted-wool-coat", lang="en").data
    assert (
        en["name"] == "Belted Long Wool Coat"
        and en["language"] == "en"
        and en["specifications"][0]["value"] == "Wool & cashmere"
    )
    assert en["specifications"][0]["label"] == "Material" and en["colors"][0]["name"] == "Black"
    tr = detail("belted-wool-coat", lang="tr").data  # no Turkish text: falls back to fa
    assert tr["language"] == "fa"


def test_machine_translation_is_flagged_and_can_be_hidden(settings):
    from apps.catalog.models import ProductTranslation

    obj = f.product("p", names={"fa": "اصلی"})
    ProductTranslation.objects.create(
        product=obj, language="en", name="Machine name", origin="machine", status="machine"
    )
    d = detail("p", lang="en").data
    assert d["name"] == "Machine name" and d["is_machine_translated"] is True
    settings.CATALOG_SHOW_MACHINE_TRANSLATIONS = False
    from apps.catalog import cache

    cache.bump_version()
    assert detail("p", lang="en").data["name"] == "اصلی"


def test_slug_or_uuid_lookup_and_hidden_products_are_404(demo_catalog):
    d = detail("belted-wool-coat").data
    assert detail(d["id"]).data["slug"] == "belted-wool-coat"
    f.product("secret", status="draft")
    assert detail("secret").status_code == 404
    assert detail("does-not-exist").status_code == 404
    assert detail("8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f").status_code == 404


def test_detail_uses_etag_and_cache_headers(demo_catalog):
    first = detail("belted-wool-coat")
    assert first["Cache-Control"].startswith("public") and first["ETag"]
    again = APIClient().get(f"{API}/products/belted-wool-coat", headers={"If-None-Match": first["ETag"]})
    assert again.status_code == 304 and again.content == b""


def test_internal_ids_and_source_urls_are_never_exposed(demo_catalog):
    from apps.catalog.models import ProductVariant

    ProductVariant.objects.update(source_product_url="https://shop.example/item/1", source_sku="SRC-1")
    from apps.catalog import cache

    cache.bump_version()
    body = detail("belted-wool-coat").content.decode()
    assert "shop.example" not in body and "SRC-1" not in body and "origin_url" not in body
    assert '"pk"' not in body
