import json
import urllib.error
from datetime import UTC, datetime

import pytest

from apps.product_sources.models import IntegrationType
from apps.product_sources.providers.dto import ProviderPage
from apps.product_sources.providers.errors import (
    InvalidProviderResponse,
    ProviderNotAuthorized,
    ProviderNotConfigured,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedOperation,
)
from apps.product_sources.providers.http import MAX_RESPONSE_BYTES, SafeHttpClient, _RefuseRedirects
from apps.product_sources.providers.namshi import FeedFileTransport, NamshiProvider
from apps.product_sources.providers.parsing import normalize_gtin, to_decimal
from apps.product_sources.providers.ratelimit import SourceRateLimiter
from apps.product_sources.providers.registry import get_provider
from apps.product_sources.providers.trendyol import (
    TrendyolFixtureTransport,
    TrendyolProvider,
    TrendyolSellerApiTransport,
)
from tests import catalog_factories as f
from tests.imports_helpers import FakeOpener, ScriptedTransport

pytestmark = pytest.mark.django_db


@pytest.fixture
def trendyol(db):
    return TrendyolProvider(f.source("trendyol"), mode="fixture")


@pytest.fixture
def namshi(db):
    return NamshiProvider(f.source("namshi", base_currency="AED"), mode="fixture")


# ------------------------------------------------------------------ parsing
def test_gtin_requires_valid_check_digit_and_length():
    assert normalize_gtin("8690000001019") == "8690000001019"
    assert normalize_gtin("8690000001018") == ""  # bad check digit
    assert normalize_gtin("NW-JKT-M") == ""  # a seller code is not promoted to a GTIN
    assert normalize_gtin("0000000000000") == ""


@pytest.mark.parametrize("value", [None, True, "abc", "-5", "NaN", "inf"])
def test_money_parsing_rejects_garbage(value):
    assert to_decimal(value) is None


# ------------------------------------------------------------------ Trendyol mapping
def test_trendyol_fixture_maps_the_documented_shape(trendyol):
    page = trendyol.discover(page_size=2)
    assert isinstance(page, ProviderPage) and len(page.items) == 2 and page.total == 5
    assert page.next_cursor == "page:1"
    sneaker = page.items[0]
    assert (sneaker.external_id, sneaker.brand, sneaker.language, sneaker.gender) == (
        "9000001",
        "Acme Sport",
        "tr",
        "men",
    )
    assert sneaker.manufacturer_code == ""  # productMainId is a seller code, never an MPN
    assert "Beden" not in sneaker.description and "<" not in sneaker.description
    v40, v41, v42 = sneaker.variants
    assert (v40.size, v40.color, v40.gtin) == ("40", "Black", "8690000001019")
    assert str(v40.price.list_amount) == "2499.90" and str(v40.price.sale_amount) == "1999.90"
    assert str(v40.price.customer_amount) == "1899.90" and v40.price.currency == "TRY"
    assert (v40.stock.status, v41.stock.status, v42.stock.status) == ("in_stock", "out_of_stock", "in_stock")
    assert len(sneaker.images) == 3 and sneaker.url.startswith("https://")


def test_trendyol_pagination_walks_every_item_once(trendyol):
    ids = [p.external_id for page in trendyol.iter_pages(page_size=2) for p in page.items]
    assert ids == ["9000001", "9000002", "9000003", "9000004", "9000005"]


def test_trendyol_unknown_stock_and_non_gtin_barcode(trendyol):
    jacket = trendyol.get_product("9000003")
    assert jacket.variants[0].gtin == "" and jacket.variants[0].barcode == "NW-JKT-M"
    assert jacket.variants[1].stock.status == "unknown"  # quantity not supplied: never guessed


def test_trendyol_archived_variant_is_inactive(trendyol):
    shorts = trendyol.get_product("9000005")
    assert [v.is_active for v in shorts.variants] == [True, False]
    assert shorts.variants[1].stock.status == "out_of_stock"


def test_trendyol_derived_operations(trendyol):
    assert [v.external_variant_id for v in trendyol.get_variants("9000002")] == ["7000201", "7000202"]
    assert set(trendyol.get_prices("9000002")) == {"7000201", "7000202"}
    assert trendyol.get_stock("9000002")["7000201"].quantity == 8
    assert len(trendyol.get_images("9000002")) == 2
    assert trendyol.get_source_url("9000002").startswith("https://")


def test_trendyol_missing_product_and_unsupported_filters(trendyol):
    with pytest.raises(ProviderNotFound):
        trendyol.get_product("404")
    with pytest.raises(UnsupportedOperation):
        trendyol.discover(category="shoes")
    assert trendyol.capabilities.category_filter is False


def test_trendyol_incremental_filters_by_modified_date(trendyol):
    future = datetime(2100, 1, 1, tzinfo=UTC)
    assert trendyol.discover(since=future).items == ()
    assert len(trendyol.discover(since=datetime(2000, 1, 1, tzinfo=UTC), page_size=50).items) == 5


def test_trendyol_invalid_response_is_rejected(db):
    for bad in ({"oops": 1}, [], "html", {"content": "nope"}):
        provider = TrendyolProvider(f.source("trendyol"), mode="fixture", transport=ScriptedTransport(bad))
        with pytest.raises(InvalidProviderResponse):
            provider.discover()


def test_trendyol_malformed_items_are_rejected_individually(db):
    good = {"contentId": 1, "title": "Ok", "variants": [{"variantId": 9, "price": {"salePrice": 10, "listPrice": 12}}]}
    payload = {
        "totalPages": 1,
        "content": [
            good,
            {"title": "no id"},
            "not an object",
            {"contentId": 2, "variants": [{"price": {"salePrice": 1}}]},  # variant without any identifier
        ],
    }
    page = TrendyolProvider(f.source("trendyol"), mode="fixture", transport=ScriptedTransport(payload)).discover()
    assert [i.external_id for i in page.items] == ["1"]
    assert {r.code for r in page.rejected} == {"missing_id", "invalid_item", "variant_missing_id"}


def test_trendyol_missing_and_negative_prices_become_none(db):
    payload = {
        "totalPages": 1,
        "content": [
            {
                "contentId": 3,
                "title": "T",
                "variants": [
                    {"variantId": 1, "price": {"salePrice": -5, "listPrice": "abc"}},
                    {"variantId": 2},
                ],
            }
        ],
    }
    item = (
        TrendyolProvider(f.source("trendyol"), mode="fixture", transport=ScriptedTransport(payload)).discover().items[0]
    )
    assert [v.price for v in item.variants] == [None, None]


def test_trendyol_pagination_beyond_ten_thousand_uses_the_token(db):
    payload = {"totalPages": 500, "totalElements": 50000, "nextPageToken": "abc", "content": []}
    provider = TrendyolProvider(f.source("trendyol"), mode="fixture", transport=ScriptedTransport(payload))
    page = provider.discover(cursor="page:99", page_size=100)
    assert page.next_cursor == "token:abc"
    payload2 = {"totalPages": 500, "content": []}
    provider = TrendyolProvider(f.source("trendyol"), mode="fixture", transport=ScriptedTransport(payload2))
    assert "pagination_limit_reached" in provider.discover(cursor="page:99", page_size=100).warnings[0]


# ------------------------------------------------------------------ Namshi mapping
def test_namshi_feed_maps_products(namshi):
    page = namshi.discover(page_size=10)
    assert [p.external_id for p in page.items] == ["NSH-ACM-001", "NSH-ACM-002", "NSH-ORB-001", "NSH-ZPH-001"]
    sneaker = page.items[0]
    assert (
        sneaker.brand == "Acme Sport" and sneaker.language == "en" and sneaker.category_path == "Men > Shoes > Sneakers"
    )
    assert sneaker.variants[0].gtin == "8690000001019" and sneaker.variants[0].price.currency == "AED"
    assert sneaker.attributes["title_ar"]
    assert page.items[2].manufacturer_code == "ORB-HYD-50"
    assert page.items[3].is_listed is False


def test_namshi_category_filter_and_paging(namshi):
    assert [p.external_id for p in namshi.discover(category="Men/Shoes").items] == ["NSH-ACM-001"]
    first = namshi.discover(page_size=3)
    assert first.next_cursor == "offset:3"
    assert [p.external_id for p in namshi.discover(cursor=first.next_cursor, page_size=3).items] == ["NSH-ZPH-001"]


def test_namshi_rejects_bad_feeds_and_unsupported_filters(db):
    src = f.source("namshi")
    with pytest.raises(InvalidProviderResponse):
        FeedFileTransport(payload={"nope": []})
    bad_version = NamshiProvider(
        src, mode="fixture", transport=FeedFileTransport(payload={"feed_version": 9, "products": []})
    )
    with pytest.raises(InvalidProviderResponse):
        bad_version.discover()
    with pytest.raises(UnsupportedOperation):
        NamshiProvider(src, mode="fixture").discover(since=datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ProviderNotFound):
        NamshiProvider(src, mode="fixture").get_product("missing")


def test_namshi_missing_fields_reject_only_that_record(db):
    feed = {
        "feed_version": 1,
        "currency": "AED",
        "products": [
            {"parent_sku": "A", "title_en": "Ok", "variants": [{"partner_sku": "A1", "price": {"msrp": 5}}]},
            {"title_en": "no sku"},
            {"parent_sku": "B", "title_en": "bad variant", "variants": [{"price": {"msrp": 5}}]},
        ],
    }
    provider = NamshiProvider(f.source("namshi"), mode="fixture", transport=FeedFileTransport(payload=feed))
    page = provider.discover()
    assert [i.external_id for i in page.items] == ["A"] and len(page.rejected) == 2


# ------------------------------------------------------------------ authorisation gate
def test_live_mode_is_refused_without_a_recorded_authorisation(db):
    manual = f.source("trendyol")  # integration_type = manual
    with pytest.raises(ProviderNotAuthorized, match="official_api or licensed_feed"):
        TrendyolProvider(manual, mode="live").assert_ready()
    manual.integration_type, manual.terms_note = IntegrationType.OFFICIAL_API, ""
    with pytest.raises(ProviderNotAuthorized, match="terms_note"):
        TrendyolProvider(manual, mode="live").assert_ready()
    manual.terms_note, manual.is_active = "Partner agreement #1", False
    with pytest.raises(ProviderNotAuthorized, match="inactive"):
        TrendyolProvider(manual, mode="live").assert_ready()


def test_authorised_live_mode_still_needs_credentials(db, settings):
    src = f.source("trendyol", integration_type=IntegrationType.OFFICIAL_API, terms_note="Partner agreement #1")
    src.integration_type, src.terms_note = IntegrationType.OFFICIAL_API, "Partner agreement #1"
    settings.IMPORTS_CREDENTIALS = {"trendyol": {}}
    with pytest.raises(ProviderNotConfigured, match="seller_id"):
        TrendyolProvider(src, mode="live").assert_ready()
    settings.IMPORTS_CREDENTIALS = {"namshi": {}}
    src2 = f.source("namshi", integration_type=IntegrationType.LICENSED_FEED, terms_note="Feed licence")
    src2.integration_type, src2.terms_note = IntegrationType.LICENSED_FEED, "Feed licence"
    with pytest.raises(ProviderNotConfigured, match="feed"):
        NamshiProvider(src2, mode="live").assert_ready()


def test_fixture_mode_never_needs_authorisation(trendyol):
    trendyol.assert_ready()
    assert trendyol.data_origin == "fixture" and TrendyolProvider.LIVE_VERIFIED is False


def test_unknown_source_has_no_provider(db):
    with pytest.raises(ProviderNotConfigured):
        get_provider(f.source("maral"))


def test_trendyol_live_transport_builds_the_documented_request():
    class Client:
        def get_json(self, url, headers):
            self.url, self.headers = url, headers
            return {"content": []}

    client = Client()
    t = TrendyolSellerApiTransport(seller_id="123", api_key="key", api_secret="secret", stage=True, client=client)
    t.request("list_products", page=0, size=10, content_id=55)
    assert client.url.startswith("https://stageapigw.trendyol.com/integration/product/sellers/123/products/approved?")
    assert "contentId=55" in client.url and "size=10" in client.url
    assert (
        client.headers["Authorization"].startswith("Basic ") and client.headers["User-Agent"] == "123 - SelfIntegration"
    )
    t2 = TrendyolSellerApiTransport(seller_id="123", api_key="k", api_secret="s", integrator="My Corp!", client=client)
    assert t2.host == "apigw.trendyol.com" and t2._headers["User-Agent"] == "123 - MyCorp"
    with pytest.raises(UnsupportedOperation):
        t.request("create_product")


# ------------------------------------------------------------------ HTTP safety
def client(result=b"{}"):
    return SafeHttpClient({"api.example.test"}, opener=FakeOpener(result))


def test_http_client_only_talks_to_https_allow_listed_hosts():
    c = client()
    for url in ("http://api.example.test/x", "https://evil.test/x", "file:///etc/passwd"):
        with pytest.raises(ProviderNotAuthorized):
            c.get_json(url, {})
    assert c.opener.requests == []
    assert c.get_json("https://api.example.test/x", {"A": "b"}) == {}
    assert c.opener.requests[0][1] == 15.0  # a timeout is always set


def http_error(status, headers=None):
    return urllib.error.HTTPError("https://api.example.test/x", status, "x", headers or {}, None)


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, ProviderNotAuthorized),
        (403, ProviderNotAuthorized),
        (404, ProviderNotFound),
        (408, ProviderTimeout),
        (500, ProviderUnavailable),
        (503, ProviderUnavailable),
        (418, InvalidProviderResponse),
    ],
)
def test_http_status_mapping(status, expected):
    with pytest.raises(expected):
        client(http_error(status)).get_json("https://api.example.test/x", {})


def test_http_429_carries_retry_after():
    with pytest.raises(ProviderRateLimited) as info:
        client(http_error(429, {"Retry-After": "7"})).get_json("https://api.example.test/x", {})
    assert info.value.retry_after == 7 and info.value.retryable
    with pytest.raises(ProviderRateLimited) as info:
        client(http_error(429)).get_json("https://api.example.test/x", {})
    assert info.value.retry_after == 60


def test_http_network_timeouts_and_outages_are_retryable():
    for error in (TimeoutError(), TimeoutError(), urllib.error.URLError(TimeoutError())):
        with pytest.raises(ProviderTimeout) as info:
            client(error).get_json("https://api.example.test/x", {})
        assert info.value.retryable
    for error in (urllib.error.URLError("dns"), ConnectionResetError()):
        with pytest.raises(ProviderUnavailable):
            client(error).get_json("https://api.example.test/x", {})


def test_http_rejects_bad_bodies_and_refuses_redirects():
    with pytest.raises(InvalidProviderResponse):
        client(b"<html>").get_json("https://api.example.test/x", {})
    with pytest.raises(InvalidProviderResponse):
        client(b"x" * (MAX_RESPONSE_BYTES + 1)).get_json("https://api.example.test/x", {})
    assert _RefuseRedirects().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.test") is None


def test_error_messages_never_contain_credentials():
    t = TrendyolSellerApiTransport(
        seller_id="1",
        api_key="SUPERKEY",
        api_secret="SUPERSECRET",
        client=SafeHttpClient({"apigw.trendyol.com"}, opener=FakeOpener(http_error(403))),
    )
    with pytest.raises(ProviderNotAuthorized) as info:
        t.request("list_products", page=0, size=1)
    assert "SUPERKEY" not in str(info.value) and "SUPERSECRET" not in str(info.value)


# ------------------------------------------------------------------ rate limiting
def test_local_rate_limit_blocks_without_sleeping(db):
    now = {"t": 1_000_000.0}
    limiter = SourceRateLimiter("rl-test", 2, clock=lambda: now["t"])
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(ProviderRateLimited) as info:
        limiter.acquire()
    assert 0 < info.value.retry_after <= 61
    now["t"] += 61  # next window
    limiter.acquire()


def test_upstream_429_cools_the_whole_source_off(db):
    transport = ScriptedTransport(ProviderRateLimited("slow down", retry_after=30), {"content": []})
    provider = TrendyolProvider(f.source("trendyol", rate_limit_per_min=100), mode="fixture", transport=transport)
    with pytest.raises(ProviderRateLimited):
        provider.discover()
    with pytest.raises(ProviderRateLimited) as info:
        provider.discover()
    assert len(transport.calls) == 1  # the second attempt never reached the source
    assert info.value.retry_after > 1


def test_fixture_transport_serves_trendyol_shape():
    t = TrendyolFixtureTransport(payload={"content": [{"contentId": 1}, {"contentId": 2}]})
    r = t.request("list_products", page=0, size=1)
    assert r["totalElements"] == 2 and r["totalPages"] == 2 and len(r["content"]) == 1


def test_fixtures_are_valid_json_and_flagged_synthetic():
    from apps.product_sources.providers.transports import FIXTURE_DIR

    for name in ("trendyol_approved_products.json", "namshi_feed.json"):
        assert "SYNTHETIC" in json.loads((FIXTURE_DIR / name).read_text())["_fixture_note"]
