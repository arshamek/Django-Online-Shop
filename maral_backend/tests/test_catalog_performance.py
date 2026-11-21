import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.catalog import cache

from . import catalog_factories as f
from .conftest import API

pytestmark = pytest.mark.django_db


def queries(path, **params):
    cache.bump_version()  # force a cold cache so we measure the real query path
    with CaptureQueriesContext(connection) as ctx:
        resp = APIClient().get(f"{API}{path}", params or None)
    assert resp.status_code == 200, resp.data
    return len(ctx), resp


def test_listing_query_count_does_not_grow_with_page_size(demo_catalog):
    small, _ = queries("/products", page_size=2)
    large, resp = queries("/products", page_size=16)
    assert len(resp.data["results"]) == 16
    assert large == small, f"N+1 detected: {small} queries for 2 products vs {large} for 16"
    assert large <= 14


def test_search_and_sorted_listing_stay_flat(demo_catalog):
    a, _ = queries("/products/search", q="کتانی", page_size=1)
    b, _ = queries("/products/search", q="کتانی", page_size=2)
    c, _ = queries("/products", sort="price_asc", brand="hollis,strada,ora", size="m", page_size=50)
    assert a == b and c <= 14


def test_detail_query_count_is_bounded_regardless_of_variant_count(db):
    f.product("few", variants=[{"size": "s", "color": "black"}])
    f.product(
        "many",
        variants=[
            {"size": size, "color": color}
            for size in ("s", "m", "l", "xl", "xxl", "xs")
            for color in ("black", "navy", "cream")
        ],
    )
    few, _ = queries("/products/few")
    many, resp = queries("/products/many")
    assert len(resp.data["variants"]) == 18
    assert few == many, f"N+1 detected: {few} queries for 1 variant vs {many} for 18"
    assert many <= 26


def test_detail_with_specifications_and_reviews_stays_bounded(demo_catalog):
    count, resp = queries("/products/belted-wool-coat")
    assert resp.data["specifications"] and resp.data["reviews"]["count"] == 2
    assert count <= 28


def test_homepage_and_facets_are_bounded(demo_catalog):
    home, _ = queries("/storefront/homepage")
    facets, _ = queries("/products/filters", category="women")
    assert home <= 24 and facets <= 24


def test_second_identical_request_is_served_from_cache(demo_catalog):
    client = APIClient()
    client.get(f"{API}/products", {"brand": "hollis"})
    with CaptureQueriesContext(connection) as ctx:
        client.get(f"{API}/products", {"brand": "hollis"})
    assert len(ctx) == 0


def test_cache_is_per_language_and_currency(demo_catalog):
    client = APIClient()
    fa = client.get(f"{API}/products", {"page_size": 1}).data["results"][0]["name"]
    en = client.get(f"{API}/products", {"page_size": 1, "lang": "en"}).data["results"][0]["name"]
    irr = client.get(f"{API}/products", {"page_size": 1, "currency": "IRR"}).data["results"][0]["price"]["final"][
        "currency"
    ]
    assert fa != en and irr == "IRR"


def test_writes_invalidate_the_cache(demo_catalog):
    client = APIClient()
    before = client.get(f"{API}/products/belted-wool-coat").data["price"]["final"]["amount"]
    from apps.catalog.models import VariantPrice

    VariantPrice.objects.filter(variant__product__slug="belted-wool-coat").update(final_irr=1_000_000)
    from apps.catalog.models import VariantPrice as VP

    for price in VP.objects.filter(variant__product__slug="belted-wool-coat"):
        price.save()  # signal path: refresh projection + bump cache version
    after = client.get(f"{API}/products/belted-wool-coat").data["price"]["final"]["amount"]
    assert before == "14720000" and after == "100000"


def test_response_carries_conditional_get_headers(demo_catalog):
    resp = APIClient().get(f"{API}/products")
    assert resp["Cache-Control"] == "public, max-age=60, stale-while-revalidate=300" and resp["ETag"]


def test_listing_uses_the_projection_indexes_not_python_sorting(demo_catalog):
    from apps.catalog.models import ProductListingProjection

    names = {i.name for i in ProductListingProjection._meta.indexes}
    assert {
        "proj_cat_avail_price_idx",
        "proj_brand_price_idx",
        "proj_popularity_idx",
        "proj_category_path_idx",
    } <= names
