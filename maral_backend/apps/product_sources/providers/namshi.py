"""
Namshi provider.

WHAT IS KNOWN (read 2026-11-21): the Namshi integration paths that are publicly described are SELLER-side
(a partner account on Noon's partner portal; catalogue *uploads* into Namshi, e.g. via BigQuery imports or
integrator platforms). Namshi's affiliate programmes run through affiliate networks and are about tracked
links and commissions. No public API for reading Namshi's catalogue as a third party was found. Maral
therefore needs a written agreement with Namshi (a licensed catalogue feed or a partner API) before any
live import. See docs/imports/PROVIDER_ACCESS_REQUIREMENTS.md.

WHAT IS BUILT: the provider reads a *licensed feed file* (JSON) delivered by the licensor and maps it. The
feed schema below ("maral-namshi-feed v1") is an ASSUMED, documented contract so the pipeline can be built
and tested; it MUST be reconciled with the real feed specification when one is granted. Until then the
provider only runs against fixtures. `LIVE_VERIFIED` stays False.
"""

import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.common.text import normalize_search_text

from .base import BaseProductProvider, ProviderCapabilities
from .dto import (
    ProviderImage,
    ProviderPage,
    ProviderPrice,
    ProviderProduct,
    ProviderStock,
    ProviderVariant,
    RejectedItem,
)
from .errors import InvalidProviderResponse, ProviderNotConfigured, ProviderNotFound, UnsupportedOperation
from .parsing import clean_str, normalize_gtin, strip_html, to_decimal, to_int
from .transports import FIXTURE_DIR, BaseTransport, load_json

MAX_PAGE_SIZE = 200
_GENDERS = {
    "men": "men",
    "male": "men",
    "women": "women",
    "female": "women",
    "kids": "kids",
    "boys": "kids",
    "girls": "kids",
    "unisex": "unisex",
}


class FeedFileTransport(BaseTransport):
    """Reads a licensed feed file once and serves it with offset paging and an optional category filter."""

    def __init__(self, payload: dict | None = None, *, path: Path | None = None):
        data = payload if payload is not None else load_json(path or FIXTURE_DIR / "namshi_feed.json")
        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            raise InvalidProviderResponse("Feed has no 'products' list.")
        self.data = data

    @staticmethod
    def _category_key(path) -> str:
        parts = path if isinstance(path, list) else [path]
        return "/".join(normalize_search_text(clean_str(p)) for p in parts if clean_str(p))

    def request(self, operation: str, **params) -> Any:
        if operation != "list_products":
            raise UnsupportedOperation(f"Feed transport has no operation {operation!r}.")
        products = [p for p in self.data["products"]]
        if params.get("external_id") is not None:
            products = [
                p
                for p in products
                if isinstance(p, dict) and clean_str(p.get("parent_sku")) == str(params["external_id"])
            ]
        if params.get("category"):
            wanted = self._category_key(str(params["category"]).split("/"))
            products = [
                p
                for p in products
                if isinstance(p, dict) and (self._category_key(p.get("category_path")) + "/").startswith(wanted + "/")
            ]
        offset = max(0, int(params.get("offset", 0)))
        limit = max(1, int(params.get("limit", 50)))
        return {
            "feed_version": self.data.get("feed_version"),
            "currency": self.data.get("currency"),
            "total": len(products),
            "offset": offset,
            "products": products[offset : offset + limit],
        }


class NamshiProvider(BaseProductProvider):
    code = "namshi"
    capabilities = ProviderCapabilities(
        category_filter=True,
        incremental=False,  # a file feed is read whole; changes are detected by payload hash
        gtin_trusted=True,  # the feed has an explicit `gtin` field; it is still check-digit validated
        catalog_scope="licensed_feed",
    )

    def build_fixture_transport(self):
        return FeedFileTransport()

    def build_live_transport(self):
        path = (
            (getattr(settings, "IMPORTS_CREDENTIALS", {}) or {})
            .get(self.source.credential_ref or self.code, {})
            .get("feed_path")
        )
        if not path:
            raise ProviderNotConfigured(
                "No licensed Namshi feed is configured (IMPORTS_CREDENTIALS['namshi']['feed_path'])."
            )
        return FeedFileTransport(path=Path(path))

    def discover(self, *, cursor=None, page_size=50, category=None, since: datetime | None = None) -> ProviderPage:
        if since is not None:
            raise UnsupportedOperation("A file feed has no 'changed since' filter; re-read the feed instead.")
        limit = max(1, min(int(page_size), MAX_PAGE_SIZE))
        try:
            offset = int(cursor[7:]) if cursor else 0
        except ValueError:
            raise InvalidProviderResponse("Malformed page cursor.") from None
        payload = self._call("list_products", offset=offset, limit=limit, category=category)
        return self._parse_page(payload, offset, limit)

    def get_product(self, external_id: str) -> ProviderProduct:
        payload = self._call("list_products", external_id=str(external_id), offset=0, limit=1)
        page = self._parse_page(payload, 0, 1)
        for item in page.items:
            if item.external_id == str(external_id):
                return item
        if page.rejected:
            raise InvalidProviderResponse(page.rejected[0].message)
        raise ProviderNotFound(f"Namshi product {external_id} is not in the feed.")

    # ------------------------------------------------------------------ mapping
    def _parse_page(self, payload: Any, offset: int, limit: int) -> ProviderPage:
        if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
            raise InvalidProviderResponse("Feed response has no 'products' list.")
        if payload.get("feed_version") not in (1, "1"):
            raise InvalidProviderResponse("Unsupported feed_version; expected 1.")
        items, rejected = [], []
        for index, raw in enumerate(payload["products"]):
            try:
                items.append(self._map_product(raw, clean_str(payload.get("currency"))))
            except ValueError as exc:
                ident = clean_str(raw.get("parent_sku")) if isinstance(raw, dict) else ""
                rejected.append(RejectedItem(ident or f"invalid:{offset}:{index}", "invalid_item", str(exc)))
        total = to_int(payload.get("total"))
        consumed = offset + len(payload["products"])
        next_cursor = f"offset:{consumed}" if total is not None and consumed < total and payload["products"] else None
        return ProviderPage(tuple(items), next_cursor, total, tuple(rejected))

    def _map_product(self, raw: Any, feed_currency: str) -> ProviderProduct:
        if not isinstance(raw, dict):
            raise ValueError("A feed product is not an object.")
        external_id = clean_str(raw.get("parent_sku"), 120)
        if not external_id:
            raise ValueError("A feed product has no parent_sku.")
        currency = (feed_currency or self.source.base_currency or "AED").upper()
        variants = tuple(self._map_variant(v, currency) for v in (raw.get("variants") or []))
        path = raw.get("category_path")
        parts = [clean_str(p, 120) for p in (path if isinstance(path, list) else [path]) if clean_str(p)]
        images = tuple(
            ProviderImage(
                clean_str(i.get("url"), 500), position=n, width=to_int(i.get("width")), height=to_int(i.get("height"))
            )
            for n, i in enumerate(raw.get("images") or [])
            if isinstance(i, dict) and clean_str(i.get("url"))
        )
        return ProviderProduct(
            source_code=self.code,
            external_id=external_id,
            title=clean_str(raw.get("title_en"), 500),
            brand=clean_str(raw.get("brand_name"), 200),
            category_id="",
            category_path=" > ".join(parts)[:500],
            description=strip_html(clean_str(raw.get("description_en"))),
            url=clean_str(raw.get("product_url"), 500),
            language="en",
            gender=_GENDERS.get(clean_str(raw.get("gender")).casefold(), ""),
            manufacturer_code=clean_str(raw.get("manufacturer_code"), 80),
            variants=variants,
            images=images,
            attributes={
                "title_ar": unicodedata.normalize("NFKC", clean_str(raw.get("title_ar"), 500)),
                "brand_code": clean_str(raw.get("brand_code"), 80),
            },
            raw=raw,
            is_listed=clean_str(raw.get("status")).casefold() != "inactive",
        )

    def _map_variant(self, raw: Any, currency: str) -> ProviderVariant:
        if not isinstance(raw, dict):
            raise ValueError("A feed variant is not an object.")
        sku = clean_str(raw.get("partner_sku"), 120)
        if not sku:
            raise ValueError("A feed variant has no partner_sku.")
        price = raw.get("price") if isinstance(raw.get("price"), dict) else None
        msrp = to_decimal(price.get("msrp")) if price else None
        sale = to_decimal(price.get("sale")) if price else None
        provider_price = None
        if msrp is not None or sale is not None:
            provider_price = ProviderPrice(
                list_amount=msrp if msrp is not None else sale,
                sale_amount=sale,
                currency=clean_str(price.get("currency")).upper() or currency,
            )
        stock = raw.get("stock") if isinstance(raw.get("stock"), dict) else None
        qty = to_int(stock.get("qty")) if stock else None
        status = "unknown" if qty is None else ("in_stock" if qty > 0 else "out_of_stock")
        return ProviderVariant(
            external_variant_id=sku,
            sku=sku,
            barcode=clean_str(raw.get("gtin"), 120),
            gtin=normalize_gtin(raw.get("gtin")),
            size=clean_str(raw.get("size"), 80),
            color=clean_str(raw.get("color_name"), 80),
            attributes={},
            price=provider_price,
            stock=ProviderStock(status=status, quantity=qty),
            is_active=True,
        )
