"""Builders for the import-engine tests."""

from decimal import Decimal

from apps.imports.models import ImportJob
from apps.product_sources.models import (
    ExternalCategoryMapping,
)
from apps.product_sources.providers.dto import (
    ProviderImage,
    ProviderPrice,
    ProviderProduct,
    ProviderStock,
    ProviderVariant,
)
from tests import catalog_factories as f


def make_variant(
    vid="V1", size="40", color="black", price="100.00", sale=None, qty=5, gtin="", currency="TRY", **extra
):
    return ProviderVariant(
        external_variant_id=vid,
        sku=f"SKU-{vid}",
        barcode=gtin,
        gtin=gtin,
        size=size,
        color=color,
        price=ProviderPrice(Decimal(price), Decimal(sale) if sale else None, currency) if price is not None else None,
        stock=ProviderStock("in_stock" if qty else "out_of_stock", qty),
        url="https://shop.test/p",
        **extra,
    )


def make_product(external_id="P1", title="Acme Runner Shoe", brand="Acme", variants=None, **extra):
    data = dict(
        source_code="trendyol",
        external_id=external_id,
        title=title,
        brand=brand,
        category_id="c1",
        category_path="Shoes",
        description="A shoe.",
        url="https://shop.test/p",
        language="tr",
        gender="men",
        variants=tuple(variants if variants is not None else [make_variant()]),
        images=(ProviderImage("https://img.test/1.jpg", 0),),
        raw={"id": external_id, "title": title, "v": 1},
    )
    data.update(extra)
    return ProviderProduct(**data)


def world(source_code="trendyol"):
    """A catalogue with the vocabulary an import needs: brand, category (+mappings), size/colour options."""
    src = f.source(source_code)
    acme = f.brand("Acme")
    shoes = f.category("shoes")
    f.axes()
    for size in ("40", "41", "42", "s", "m", "l"):
        f.option("size", size)
    for color in ("black", "white"):
        f.option("color", color)
    ExternalCategoryMapping.objects.get_or_create(source=src, raw_key="c1", defaults={"category": shoes})
    return src, acme, shoes


def run_job_sync(job_or_id):
    """Drive run_slice to completion without a broker (what the Celery task does in a loop)."""
    from apps.imports.services import jobs

    job_id = getattr(job_or_id, "pk", job_or_id)
    for _ in range(50):
        outcome = jobs.run_slice(job_id)
        if outcome.state == "done":
            break
        if outcome.state == "retry":
            ImportJob.objects.filter(pk=job_id, status="waiting").update(resume_at=None)
    return ImportJob.objects.get(pk=job_id)


class ScriptedTransport:
    """Replays queued responses; an Exception in the queue is raised. Records every call."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []

    def request(self, operation, **params):
        self.calls.append((operation, params))
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]


class FakeOpener:
    def __init__(self, result):
        self.result, self.requests = result, []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return FakeResponse(self.result)


def import_one(src, product, *, trusted=True, origin="fixture"):
    from apps.imports.services.pipeline import process_product

    return process_product(product, source=src, data_origin=origin, gtin_trusted=trusted)


def fixture_world():
    """Vocabulary for the bundled Trendyol + Namshi fixtures. 'Orbit Cosmetics' is deliberately unknown."""
    from apps.common.text import normalize_search_text

    tr, ns = f.source("trendyol"), f.source("namshi", base_currency="AED", country_code="AE")
    f.axes()
    for size in ("40", "41", "42", "s", "m", "l", "50ml"):
        f.option("size", size)
    for color in ("black", "white", "navy", "beige", "grey"):
        f.option("color", color)
    for name in ("Acme Sport", "Nordic Wear", "Lumen Home", "Zephyr"):
        f.brand(name)
    cats = {k: f.category(k) for k in ("sneakers", "tops", "outerwear", "home", "bottoms")}
    for key, cat in (("1172", "sneakers"), ("73", "tops"), ("119", "outerwear"), ("880", "home"), ("211", "bottoms")):
        ExternalCategoryMapping.objects.create(source=tr, raw_key=key, category=cats[cat])
    for path, cat in (
        ("Men > Shoes > Sneakers", "sneakers"),
        ("Women > Clothing > T-Shirts", "tops"),
        ("Men > Clothing > Shorts", "bottoms"),
        ("Beauty > Skincare", "home"),
    ):
        ExternalCategoryMapping.objects.create(source=ns, raw_key=normalize_search_text(path), category=cats[cat])
    return tr, ns
