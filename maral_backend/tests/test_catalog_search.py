import pytest
from rest_framework.test import APIClient

from apps.brands.models import BrandAlias

from . import catalog_factories as f
from .conftest import API

pytestmark = pytest.mark.django_db


def search(q, **params):
    return APIClient().get(f"{API}/products/search", {"q": q, **params})


def slugs(resp):
    assert resp.status_code == 200, resp.data
    return [r["slug"] for r in resp.data["results"]]


def test_exact_and_partial_title_match_in_persian(demo_catalog):
    assert slugs(search("کتانی چانکی بژ"))[0] == "chunky-beige-sneakers"
    assert set(slugs(search("کتانی"))) == {"chunky-beige-sneakers", "white-leather-sneakers"}
    assert slugs(search("چانک")) == ["chunky-beige-sneakers"]  # partial


def test_arabic_letters_digits_and_zwnj_normalise_to_the_same_match(demo_catalog):
    assert slugs(search("كتاني")) == slugs(search("کتانی"))
    assert slugs(search("  کتانی   ")) == slugs(search("کتانی"))


def test_localized_title_matching(demo_catalog):
    assert slugs(search("tuxedo", lang="en")) == ["black-tuxedo-blazer"]
    assert slugs(search("TUXEDO")) == ["black-tuxedo-blazer"]  # case-insensitive, any language


def test_brand_matching_by_name_local_name_and_alias(demo_catalog):
    assert set(slugs(search("hollis"))) == {"charcoal-wool-overcoat", "oxford-polo-set"}
    assert slugs(search("ابریشم پارسی")) == ["silk-chain-scarf"]  # Persian brand name (BrandTranslation)
    from apps.brands.models import Brand

    BrandAlias.objects.create(brand=Brand.objects.get(slug="ora"), alias="اورا")
    assert slugs(search("اورا")) == ["classic-gold-watch"]


def test_category_matching(demo_catalog):
    assert "oxford-polo-set" in slugs(search("پوشاک مردانه"))
    assert set(slugs(search("shoes", lang="en"))) >= {"suede-chelsea-boots"}


def test_sku_matches_exactly(demo_catalog):
    assert slugs(search("belted-wool-coat-s-camel")) == ["belted-wool-coat"]


def test_every_token_must_match(demo_catalog):
    assert slugs(search("کتانی سفید")) == ["white-leather-sneakers"]
    assert slugs(search("hollis ora")) == []


def test_relevance_prefers_exact_title_then_brand_then_prefix():
    f.product("brand-hit", names={"fa": "کیف دستی"}, brand_obj=f.brand("Lumo"), popularity=90)
    f.product("exact", names={"fa": "lumo"}, popularity=1)
    f.product("prefix", names={"fa": "lumo classic"}, popularity=50)
    assert slugs(search("lumo")) == ["exact", "brand-hit", "prefix"]


def test_search_combines_with_filters_and_sorting(demo_catalog):
    assert slugs(search("کتانی", brand="strada", sale="true")) == ["chunky-beige-sneakers", "white-leather-sneakers"]
    ordered = slugs(search("کتانی", sort="price_asc"))
    assert ordered == ["white-leather-sneakers", "chunky-beige-sneakers"]


def test_hidden_products_are_not_searchable():
    f.product("hidden-draft", names={"fa": "ناپیدا"}, status="draft")
    assert slugs(search("ناپیدا")) == []


def test_search_validation():
    assert search("a").status_code == 400
    assert APIClient().get(f"{API}/products/search").status_code == 400
    assert search("x" * 101).status_code == 400
    assert slugs(search("%%")) == [] and slugs(search("__")) == []  # wildcard characters are literal, not patterns


def test_search_term_count_is_bounded(demo_catalog):
    from apps.catalog.search.database import DatabaseSearchBackend

    many = " ".join(["ab"] * 30)  # 89 chars: within the length cap, far above the token cap
    assert search(many).status_code == 200
    assert len(DatabaseSearchBackend().tokens(many)) == 6


def test_list_endpoint_accepts_q_too(demo_catalog):
    assert slugs(APIClient().get(f"{API}/products", {"q": "hollis"})) == slugs(search("hollis"))


def test_search_is_throttled(demo_catalog, settings):
    from django.core.cache import cache
    from rest_framework.throttling import ScopedRateThrottle

    cache.clear()
    original = ScopedRateThrottle.THROTTLE_RATES
    ScopedRateThrottle.THROTTLE_RATES = {**original, "catalog_search": "2/min"}
    try:
        client = APIClient()
        codes = [client.get(f"{API}/products/search", {"q": f"کتانی{i}"}).status_code for i in range(4)]
    finally:
        ScopedRateThrottle.THROTTLE_RATES = original
    assert codes[:2] == [200, 200] and 429 in codes[2:]


def test_search_backend_is_swappable(demo_catalog, settings):
    from apps.catalog.search import get_search_backend

    settings.CATALOG_SEARCH_BACKEND = "tests.test_catalog_search.AlwaysFirstBackend"
    assert isinstance(get_search_backend(), AlwaysFirstBackend)


class AlwaysFirstBackend:
    def apply(self, queryset, query):
        from django.db.models import IntegerField, Value

        return queryset.annotate(search_score=Value(1, output_field=IntegerField()))
