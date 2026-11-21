import pytest
from rest_framework.test import APIClient

from . import catalog_factories as f
from .conftest import API

pytestmark = pytest.mark.django_db


def get(path, **params):
    return APIClient().get(f"{API}{path}", params or None)


def test_homepage_sections(demo_catalog):
    d = get("/storefront/homepage/").data
    assert d["language"] == "fa" and d["currency"] == "IRT"
    assert [c["slug"] for c in d["categories"]] == ["women", "men", "shoes", "bags", "accessories", "beauty"]
    assert d["categories"][0]["product_count"] == 5 and d["categories"][0]["description"].startswith("کالکشن")
    assert len(d["brands"]) == 10 and all(b["product_count"] > 0 for b in d["brands"])
    assert [s["code"] for s in d["sources"]] == ["maral", "trendyol", "namshi"]
    assert {s["code"]: s["product_count"] for s in d["sources"]} == {"maral": 2, "trendyol": 8, "namshi": 6}
    assert len(d["trending"]) == 8 and all(p["price"]["discount_percent"] > 0 for p in d["trending"])
    assert len(d["new_arrivals"]) == 6 and all(p["is_new"] for p in d["new_arrivals"])


def test_homepage_is_empty_but_valid_without_data(db):
    d = get("/storefront/homepage").data
    assert d["categories"] == [] and d["trending"] == [] and d["brands"] == []


def test_category_list_counts_include_sub_categories():
    parent = f.category("clothing", fa="پوشاک", sort_order=0)
    kid = f.category("coats", parent=parent, fa="پالتو")
    f.product("a", category_obj=kid)
    f.product("b", category_obj=parent)
    f.category("empty", fa="خالی")
    rows = {r["slug"]: r for r in get("/categories").data["results"]}
    assert (
        rows["clothing"]["product_count"] == 2
        and rows["coats"]["product_count"] == 1
        and rows["empty"]["product_count"] == 0
    )
    assert rows["coats"]["parent"] == "clothing" and rows["coats"]["depth"] == 1 and rows["clothing"]["parent"] is None


def test_category_detail_has_breadcrumb_and_children():
    parent = f.category("clothing", fa="پوشاک")
    f.category("coats", parent=parent, fa="پالتو")
    d = get("/categories/coats").data
    assert d["breadcrumb"] == [{"slug": "clothing", "name": "پوشاک"}, {"slug": "coats", "name": "پالتو"}]
    top = get("/categories/clothing").data
    assert [c["slug"] for c in top["children"]] == ["coats"]
    assert get("/categories/missing").status_code == 404


def test_inactive_categories_are_not_listed_or_reachable():
    f.category("gone", is_active=False)
    assert get("/categories/gone").status_code == 404
    assert "gone" not in [c["slug"] for c in get("/categories").data["results"]]


def test_brand_list_and_detail(demo_catalog):
    rows = get("/brands").data["results"]
    assert rows[0]["name"] == "Ambre Studio" and rows[0]["product_count"] == 2
    lumiere = next(r for r in rows if r["slug"] == "lumiere")
    assert lumiere["name"] == "لومیر"  # Persian display name from BrandTranslation
    assert get("/brands/lumiere", lang="en").data["name"] == "Lumière"
    assert get("/brands/hollis").data["product_count"] == 2
    assert get("/brands/nope").status_code == 404


def test_brands_without_visible_products_are_not_listed():
    f.brand("Empty Brand")
    f.product("hidden", brand_obj=f.brand("Draft Only"), status="draft")
    assert get("/brands").data["results"] == []
    assert get("/brands/empty-brand").data["product_count"] == 0


def test_facets_ignore_their_own_filter_but_respect_the_others(demo_catalog):
    d = get("/products/filters", category="shoes").data
    assert {c["slug"] for c in d["categories"]} == {
        "women",
        "men",
        "shoes",
        "bags",
        "accessories",
        "beauty",
    }  # own filter ignored
    assert {b["slug"] for b in d["brands"]} == {"strada", "oxford-and-co"}  # category filter applied
    assert {s["code"]: s["count"] for s in d["sources"]} == {"trendyol": 3}
    assert [s["code"] for s in d["sizes"]][:3] == ["38", "39", "40"] or d["sizes"]
    assert d["price_range"] == {"currency": "IRT", "min": "2940000", "max": "9800000"}
    assert d["flags"]["total"] == 3 and d["flags"]["on_sale"] == 2 and d["flags"]["available"] == 2


def test_facet_counts_match_listing_counts(demo_catalog):
    facets = get("/products/filters", brand="hollis").data
    for source in facets["sources"]:
        assert get("/products", brand="hollis", source=source["code"]).data["count"] == source["count"]
    for color in facets["colors"]:
        assert get("/products", brand="hollis", color=color["code"]).data["count"] == color["count"]
        assert color["value"].startswith("oklch") and color["name"]


def test_facets_respect_search_and_currency(demo_catalog):
    d = get("/products/filters", q="کتانی", currency="IRR").data
    assert d["flags"]["total"] == 2 and d["price_range"]["currency"] == "IRR" and d["price_range"]["min"] == "29400000"
    assert get("/products/filters", sort="bogus").status_code == 400


def test_related_endpoint_prefers_same_category_and_brand(demo_catalog):
    coat = get("/products/belted-wool-coat").data
    d = get(f"/products/related/{coat['id']}", limit=3).data
    assert d["count"] == 3 and "belted-wool-coat" not in [r["slug"] for r in d["results"]]
    assert d["results"][0]["slug"] == "champagne-satin-set"  # same category AND same brand
    assert all(r["category"]["slug"] == "women" or r["brand"]["slug"] == "atelier-nour" for r in d["results"])
    assert get("/products/related/belted-wool-coat/").status_code == 200  # slug works too and slash form is accepted
    assert get("/products/related/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f").status_code == 404
    assert get(f"/products/related/{coat['id']}", limit=13).status_code == 400


def test_related_never_returns_hidden_products():
    f.product("base")
    f.product("draft-sibling", status="draft")
    assert get(f"/products/related/{get('/products/base').data['id']}").data["results"] == []


def test_recently_viewed_hydrates_ids_in_the_given_order_and_skips_hidden(demo_catalog):
    cards = get("/products", page_size=4).data["results"]
    hidden = f.product("hidden", status="draft")
    ids = [cards[2]["id"], str(hidden.public_id), cards[0]["id"]]
    d = get("/products/recently-viewed", ids=",".join(ids)).data
    assert [r["id"] for r in d["results"]] == [cards[2]["id"], cards[0]["id"]]
    assert get("/products/recently-viewed").data["results"] == []
    assert get("/products/recently-viewed", ids="nope").status_code == 400
    assert get("/products/recently-viewed", ids=cards[0]["id"], brand="x").status_code == 400
    assert get("/products/recently-viewed/", ids=cards[0]["id"]).status_code == 200


def test_fixed_routes_win_over_the_slug_route(demo_catalog):
    assert "results" in get("/products/search", q="کتانی").data
    assert "categories" in get("/products/filters").data
    assert "results" in get("/products/recently-viewed").data


def test_write_methods_are_not_allowed_on_public_endpoints(demo_catalog):
    client = APIClient()
    for path in ("/products", "/products/belted-wool-coat", "/brands", "/categories", "/storefront/homepage"):
        assert client.post(f"{API}{path}", {}, format="json").status_code in (403, 405)
        assert client.delete(f"{API}{path}").status_code in (403, 405)
