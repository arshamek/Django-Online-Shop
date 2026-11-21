from datetime import timedelta

import pytest
from django.utils import timezone

from . import catalog_factories as f
from .conftest import API

pytestmark = pytest.mark.django_db


def slugs(resp):
    assert resp.status_code == 200, resp.data
    return [r["slug"] for r in resp.data["results"]]


def test_list_shape_matches_the_frontend_product_contract(demo_catalog, catalog_api):
    resp = catalog_api("/products", page_size=100)
    assert resp.status_code == 200
    assert resp.data["count"] == 16 and resp.data["page"] == 1
    card = next(r for r in resp.data["results"] if r["slug"] == "belted-wool-coat")
    assert card["name"] == "پالتو بلند کمربنددار پشمی" and card["brand"]["name"] == "Atelier Nour"
    assert card["category"]["slug"] == "women" and card["source"]["code"] == "trendyol"
    assert card["price"]["original"] == {"amount": "8900", "currency": "TRY"}
    assert card["price"]["list"] == {"amount": "18400000", "currency": "IRT"}
    assert (
        card["price"]["final"] == {"amount": "14720000", "currency": "IRT"} and card["price"]["discount_percent"] == 20
    )
    assert card["fulfillment"] == "order" and card["fulfillment_type"] == "PROXY_PURCHASE"
    assert card["is_new"] is True and card["available"] is True
    assert [s["name"] for s in card["sizes"]] == ["S", "M", "L", "XL"]
    assert {c["name"] for c in card["colors"]} == {"مشکی", "شتری"}
    assert len(card["images"]) == 2 and card["images"][0]["thumbnail_url"].endswith(".webp")


def test_both_trailing_slash_forms_are_served(demo_catalog, catalog_api):
    assert catalog_api("/products").data["count"] == catalog_api("/products/").data["count"] == 16


def test_pagination(demo_catalog, catalog_api):
    first = catalog_api("/products", page_size=5)
    assert len(first.data["results"]) == 5 and first.data["next"] and first.data["previous"] is None
    last = catalog_api("/products", page_size=5, page=4)
    assert len(last.data["results"]) == 1 and last.data["next"] is None
    assert catalog_api("/products", page_size=5, page=5).status_code == 404
    assert len(catalog_api("/products", page_size=1000).data["results"]) == 16  # clamped to max_page_size
    pages = [slugs(catalog_api("/products", page_size=4, page=p)) for p in (1, 2, 3, 4)]
    flat = [s for page in pages for s in page]
    assert len(flat) == len(set(flat)) == 16  # stable ordering: no duplicates or gaps


def test_deep_paging_is_bounded(demo_catalog, catalog_api):
    resp = catalog_api("/products", page_size=100, page=101)
    assert resp.status_code == 400 and resp.data["error"]["code"] == "invalid_filter"


@pytest.mark.parametrize(
    "params,field",
    [
        ({"colour": "x"}, "colour"),
        ({"sort": "cheapest"}, "sort"),
        ({"price_min": "abc"}, "price_min"),
        ({"price_min": "10", "price_max": "5"}, "price_min"),
        ({"sale": "maybe"}, "sale"),
        ({"ids": "not-a-uuid"}, "ids"),
        ({"currency": "USD"}, "currency"),
        ({"category": "a" * 70}, "category"),
        ({"q": "x"}, "q"),
        ({"q": "x" * 101}, "q"),
        ({"sort": "relevance"}, "sort"),
    ],
)
def test_invalid_parameters_are_rejected_not_ignored(demo_catalog, catalog_api, params, field):
    resp = catalog_api("/products", **params)
    assert resp.status_code == 400 and resp.data["error"]["code"] == "invalid_filter"
    assert field in resp.data["error"]["fields"]


def test_filter_value_counts_are_capped(demo_catalog, catalog_api):
    resp = catalog_api("/products", brand=",".join(f"b{i}" for i in range(21)))
    assert resp.status_code == 400 and "brand" in resp.data["error"]["fields"]
    assert catalog_api("/products", ids=",".join(["8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f"] * 51)).status_code == 400


def test_category_filter_includes_sub_categories():
    parent = f.category("clothing")
    kid = f.category("coats", parent=parent)
    f.product("coat", category_obj=kid)
    f.product("bag", category_obj=f.category("bags"))
    from rest_framework.test import APIClient

    c = APIClient()
    assert slugs(c.get(f"{API}/products", {"category": "clothing"})) == ["coat"]
    assert slugs(c.get(f"{API}/products", {"category": "coats"})) == ["coat"]
    assert sorted(slugs(c.get(f"{API}/products", {"category": "clothing,bags"}))) == ["bag", "coat"]
    assert slugs(c.get(f"{API}/products", {"category": "nope"})) == []


def test_brand_source_and_repeated_params(demo_catalog, catalog_api):
    assert set(slugs(catalog_api("/products", brand="hollis"))) == {"charcoal-wool-overcoat", "oxford-polo-set"}
    both = slugs(catalog_api("/products", brand="hollis,ora", page_size=100))
    assert len(both) == 3
    from rest_framework.test import APIClient

    repeated = APIClient().get(f"{API}/products?brand=hollis&brand=ora")
    assert len(repeated.data["results"]) == 3
    assert set(slugs(catalog_api("/products", source="maral"))) == {"pleated-wide-trousers", "silk-moisturizing-cream"}
    assert len(slugs(catalog_api("/products", source="namshi", page_size=100))) == 6


def test_size_and_color_filters(demo_catalog, catalog_api):
    assert set(slugs(catalog_api("/products", size="xxl"))) == {"charcoal-wool-overcoat"}
    assert set(slugs(catalog_api("/products", size="50ml"))) == {"amber-wood-parfum", "silk-moisturizing-cream"}
    assert set(slugs(catalog_api("/products", color="gold"))) == {"classic-gold-watch", "amber-wood-parfum"}
    assert slugs(catalog_api("/products", size="90×90")) == ["silk-chain-scarf"]
    assert set(slugs(catalog_api("/products", size="m", color="navy"))) == {"charcoal-wool-overcoat", "oxford-polo-set"}
    assert slugs(catalog_api("/products", size="xl", color="black")) == ["belted-wool-coat", "charcoal-wool-overcoat"]
    assert slugs(catalog_api("/products", size="xs", color="navy")) == []  # AND semantics across groups


def test_sold_out_size_is_excluded_only_when_availability_is_requested(demo_catalog, catalog_api):
    # XL of the first coat is sold out in the demo data
    assert "belted-wool-coat" in slugs(catalog_api("/products", size="xl", page_size=100))
    assert "belted-wool-coat" not in slugs(catalog_api("/products", size="xl", available="true", page_size=100))
    assert "belted-wool-coat" in slugs(catalog_api("/products", size="l", available="true", page_size=100))


def test_price_range_in_toman_and_in_rial(demo_catalog, catalog_api):
    cheap = slugs(catalog_api("/products", price_max=3_500_000, page_size=100))
    assert set(cheap) == {"silk-moisturizing-cream", "white-leather-sneakers", "pleated-wide-trousers"}
    band = slugs(catalog_api("/products", price_min=5_000_000, price_max=10_000_000, page_size=100))
    assert set(band) == {"cable-knit-turtleneck", "suede-chelsea-boots", "silk-chain-scarf", "amber-wood-parfum"}
    rial = slugs(catalog_api("/products", currency="IRR", price_max=35_000_000, page_size=100))
    assert set(rial) == set(cheap)
    assert catalog_api("/products", currency="IRR").data["results"][0]["price"]["final"]["currency"] == "IRR"


def test_sale_available_and_new_filters(demo_catalog, catalog_api):
    sale = slugs(catalog_api("/products", sale="true", page_size=100))
    assert len(sale) == 11 and "cable-knit-turtleneck" not in sale
    assert len(slugs(catalog_api("/products", sale="false", page_size=100))) == 5
    unavailable = slugs(catalog_api("/products", available="false", page_size=100))
    assert set(unavailable) == {"white-leather-sneakers", "black-tuxedo-blazer"}
    assert len(slugs(catalog_api("/products", available="true", page_size=100))) == 14
    assert len(slugs(catalog_api("/products", is_new="true", page_size=100))) == 6


def test_new_badge_expires(demo_catalog, catalog_api):
    from apps.catalog.models import Product

    obj = Product.objects.get(slug="belted-wool-coat")
    obj.new_until = timezone.now() - timedelta(days=1)
    obj.save()
    assert "belted-wool-coat" not in slugs(catalog_api("/products", is_new="true", page_size=100))
    assert (
        next(r for r in catalog_api("/products", page_size=100).data["results"] if r["slug"] == "belted-wool-coat")[
            "is_new"
        ]
        is False
    )


def test_multiple_filters_combine_with_and(demo_catalog, catalog_api):
    result = slugs(catalog_api("/products", category="shoes", brand="strada", sale="true", available="true"))
    assert result == ["chunky-beige-sneakers"]
    assert slugs(catalog_api("/products", category="men", source="trendyol")) == ["oxford-polo-set"]


def test_sorting(demo_catalog, catalog_api):
    def final(resp):
        return [int(r["price"]["final"]["amount"]) for r in resp.data["results"]]

    asc = catalog_api("/products", sort="price_asc", page_size=100)
    desc = catalog_api("/products", sort="price_desc", page_size=100)
    assert final(asc) == sorted(final(asc)) and final(desc) == sorted(final(desc), reverse=True)
    assert final(asc)[0] == 2_450_000 and final(desc)[0] == 40_480_000  # 46M list, 12% off
    discounts = [
        r["price"]["discount_percent"] for r in catalog_api("/products", sort="discount", page_size=100).data["results"]
    ]
    assert discounts == sorted(discounts, reverse=True) and discounts[0] == 30
    assert slugs(catalog_api("/products", page_size=3)) == [
        "belted-wool-coat",
        "cable-knit-turtleneck",
        "leather-handbag",
    ]  # popular = demo rank
    newest = catalog_api("/products", sort="new", page_size=100).data["results"]
    assert all(r["is_new"] for r in newest[:6]) and not any(r["is_new"] for r in newest[6:])


def test_ids_hydrates_a_list_of_products(demo_catalog, catalog_api):
    everything = catalog_api("/products", page_size=3).data["results"]
    ids = ",".join(r["id"] for r in everything[:2])
    assert slugs(catalog_api("/products", ids=ids)) and len(catalog_api("/products", ids=ids).data["results"]) == 2


def test_hidden_products_never_appear():
    f.product("visible")
    f.product("draft", status="draft")
    f.product("no-price", variants=[{"price": False}])
    f.product("archived", status="archived")
    from rest_framework.test import APIClient

    assert slugs(APIClient().get(f"{API}/products")) == ["visible"]


def test_language_and_currency_negotiation(demo_catalog, catalog_api, api_client):
    assert catalog_api("/products", lang="en", page_size=1).data["results"][0]["name"] == "Belted Long Wool Coat"
    resp = api_client.get(
        f"{API}/products",
        {"page_size": 1},
        headers={"Accept-Language": "en-US,en;q=0.9,fa;q=0.5", "Accept-Currency": "IRR"},
    )
    assert resp.data["results"][0]["name"] == "Belted Long Wool Coat"
    assert resp.data["results"][0]["price"]["final"] == {"amount": "147200000", "currency": "IRR"}
    assert {"Accept-Language", "Accept-Currency"} <= {v.strip() for v in resp["Vary"].split(",")}
    assert (
        catalog_api("/products", lang="ar", page_size=1).data["results"][0]["language"] == "fa"
    )  # fallback chain: ar -> fa
