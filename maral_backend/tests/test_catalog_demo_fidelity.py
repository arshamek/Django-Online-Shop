"""The demo catalogue must reproduce the frontend mock exactly (src/lib/shop-data.ts is the source of truth)."""

import re
from pathlib import Path

import pytest
from rest_framework.test import APIClient

from apps.catalog.demo import final_toman

from .conftest import API

TS = Path(__file__).resolve().parents[2] / "src" / "lib" / "shop-data.ts"
ROW = re.compile(
    r'p\(\{ id: "p(\d+)", brand: "([^"]+)", name: "([^"]+)", originalPrice: (\d+), originalCurrency: "(\w+)", '
    r'priceToman: ([\d_]+), discount: (\d+), category: "(\w+)", source: "(\w+)", images: \[[^\]]*\], '
    r'sizes: \[([^\]]*)\], colors: \[([^\]]*)\], available: (true|false), fulfillment: "(\w+)"(, isNew: true)?'
)
pytestmark = [pytest.mark.django_db, pytest.mark.skipif(not TS.exists(), reason="frontend source not present")]


def frontend_rows():
    rows = [m.groups() for m in ROW.finditer(TS.read_text())]
    assert len(rows) == 16
    return rows


def test_every_demo_product_equals_its_frontend_counterpart(demo_catalog):
    api = {r["slug"]: r for r in APIClient().get(f"{API}/products", {"page_size": 100}).data["results"]}
    by_name = {r["name"]: r for r in api.values()}
    for (
        pid,
        brand,
        name,
        original,
        currency,
        price,
        discount,
        category,
        source,
        sizes,
        colors,
        available,
        fulfillment,
        is_new,
    ) in frontend_rows():
        card = by_name[name]
        list_toman, disc = int(price.replace("_", "")), int(discount)
        assert card["brand"]["name"] in (brand, {"Soie Persane": "ابریشم پارسی", "Lumière": "لومیر"}.get(brand)), pid
        assert card["category"]["slug"] == category, pid
        assert card["source"]["name"] == source, pid
        assert card["price"]["original"] == {"amount": original, "currency": currency}, pid
        assert card["price"]["list"]["amount"] == str(list_toman if pid != "12" else list_toman), pid
        assert card["price"]["final"]["amount"] == str(final_toman(list_toman, disc)), pid
        assert card["price"]["discount_percent"] == disc, pid
        assert [x["name"] for x in card["sizes"]] == [
            x.strip().strip('"') if x.strip().strip('"') != "One Size" else "تک‌سایز" for x in sizes.split(",")
        ], pid
        assert len(card["colors"]) == colors.count("C."), pid
        assert card["available"] is (available == "true"), pid
        assert card["fulfillment"] == fulfillment, pid
        assert card["is_new"] is bool(is_new), pid


def test_frontend_price_formula_is_reproduced():
    assert final_toman(18_400_000, 20) == 14_720_000
    assert final_toman(46_000_000, 12) == 40_480_000
    assert final_toman(4_650_000, 10) == 4_185_000
