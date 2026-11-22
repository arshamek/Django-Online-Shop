"""
Trendyol provider.

WHAT THE OFFICIAL API IS (Trendyol developer documentation, read 2026-11-21): the Marketplace "Partner"
API is for *sellers*. Product V2 `GET /integration/product/sellers/{sellerId}/products/approved` lists the
products of the authenticated seller's own store. It is not a catalogue-read API for third parties, so it
can only serve Maral if Maral (or a seller who authorises Maral) holds Partner credentials, or if Trendyol
grants a separate licensed feed. The V1 product endpoints were retired on 15 Oct 2026, so only V2 is used.

STATUS: the mapper below is written from the documented V2 response shape and is exercised only against
fixtures (providers/fixtures/trendyol_approved_products.json, synthetic values in the documented shape).
`LIVE_VERIFIED` stays False until the live transport has run against Trendyol's stage environment.
"""

import re
from datetime import UTC, datetime
from typing import Any

from django.conf import settings

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
from .http import SafeHttpClient
from .parsing import clean_str, normalize_gtin, strip_html, to_decimal, to_int
from .transports import FIXTURE_DIR, BaseTransport, load_json

PROD_HOST = "apigw.trendyol.com"
STAGE_HOST = "stageapigw.trendyol.com"
MAX_PAGE_SIZE = 100  # documented maximum
MAX_OFFSET_ITEMS = 10_000  # documented: page x size may not exceed 10,000; beyond that nextPageToken is used

_SIZE_NAMES = {"beden", "size", "boyut", "numara"}
_COLOR_NAMES = {"renk", "color", "colour"}
_GENDER_NAMES = {"cinsiyet", "gender"}
_GENDER_VALUES = {
    "erkek": "men",
    "kadın": "women",
    "kadin": "women",
    "unisex": "unisex",
    "çocuk": "kids",
    "cocuk": "kids",
    "kız çocuk": "kids",
    "erkek çocuk": "kids",
    "bebek": "kids",
}


class _MappingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TrendyolFixtureTransport(BaseTransport):
    """Replays a Trendyol-shaped `products/approved` response set from a JSON file (no network)."""

    def __init__(self, payload: dict | None = None, *, fixture: str = "trendyol_approved_products.json"):
        data = payload if payload is not None else load_json(FIXTURE_DIR / fixture)
        self.content = list(data.get("content", []))

    def request(self, operation: str, **params) -> Any:
        if operation != "list_products":
            raise UnsupportedOperation(f"Fixture transport has no operation {operation!r}.")
        items = self.content
        if params.get("content_id") is not None:
            items = [i for i in items if str(i.get("contentId")) == str(params["content_id"])]
        if params.get("start_date") is not None:
            items = [i for i in items if (i.get("lastModifiedDate") or 0) >= int(params["start_date"])]
        size = max(1, int(params.get("size", 50)))
        page = max(0, int(params.get("page", 0)))
        total_pages = max(1, -(-len(items) // size)) if items else 0
        return {
            "totalElements": len(items),
            "totalPages": total_pages,
            "page": page,
            "size": size,
            "nextPageToken": None,
            "content": items[page * size : (page + 1) * size],
        }


class TrendyolSellerApiTransport(BaseTransport):
    """
    Live transport for the documented Product V2 `products/approved` endpoint. Requires the seller's own
    Partner credentials (Basic auth) and is never constructed unless the source passed the authorisation
    gate. NOT VERIFIED against the live service (see module docstring).
    """

    def __init__(
        self, *, seller_id: str, api_key: str, api_secret: str, integrator: str = "", stage: bool = False, client=None
    ):
        import base64

        self.host = STAGE_HOST if stage else PROD_HOST
        self.seller_id = re.sub(r"\D", "", str(seller_id))
        token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        name = re.sub(r"[^A-Za-z0-9]", "", integrator)[:30] or "SelfIntegration"
        # Trendyol blocks requests without a User-Agent of the documented form "<sellerId> - <integrator>".
        self._headers = {"Authorization": f"Basic {token}", "User-Agent": f"{self.seller_id} - {name}"}
        self.client = client or SafeHttpClient({self.host})

    @classmethod
    def from_settings(cls, source) -> "TrendyolSellerApiTransport":
        creds = (getattr(settings, "IMPORTS_CREDENTIALS", {}) or {}).get(source.credential_ref or source.code, {})
        missing = [k for k in ("seller_id", "api_key", "api_secret") if not creds.get(k)]
        if missing:
            raise ProviderNotConfigured("Trendyol credentials are not configured: " + ", ".join(missing) + ".")
        return cls(
            seller_id=creds["seller_id"],
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            integrator=creds.get("integrator", ""),
            stage=bool(creds.get("stage")),
        )

    def request(self, operation: str, **params) -> Any:
        if operation != "list_products":
            raise UnsupportedOperation(f"Trendyol transport has no operation {operation!r}.")
        from urllib.parse import urlencode

        query = {
            "page": params.get("page"),
            "size": params.get("size"),
            "contentId": params.get("content_id"),
            "startDate": params.get("start_date"),
            "dateQueryType": params.get("date_query_type"),
            "nextPageToken": params.get("next_page_token"),
        }
        qs = urlencode({k: v for k, v in query.items() if v is not None})
        url = f"https://{self.host}/integration/product/sellers/{self.seller_id}/products/approved?{qs}"
        return self.client.get_json(url, self._headers)


class TrendyolProvider(BaseProductProvider):
    code = "trendyol"
    capabilities = ProviderCapabilities(
        category_filter=False,  # the documented filter has no category parameter
        incremental=True,
        gtin_trusted=False,  # `barcode` is the seller's own barcode; it is not guaranteed to be a GTIN
        catalog_scope="own_seller_catalog",
    )

    def build_fixture_transport(self):
        return TrendyolFixtureTransport()

    def build_live_transport(self):
        return TrendyolSellerApiTransport.from_settings(self.source)

    # ------------------------------------------------------------------ operations
    def discover(self, *, cursor=None, page_size=50, category=None, since=None) -> ProviderPage:
        if category:
            raise UnsupportedOperation("Trendyol's approved-products filter has no category parameter.")
        size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        params: dict[str, Any] = {"size": size}
        if cursor and cursor.startswith("token:"):
            params["next_page_token"] = cursor[6:]
            page_no = None
        else:
            try:
                page_no = int(cursor[5:]) if cursor else 0
            except ValueError:
                raise InvalidProviderResponse("Malformed page cursor.") from None
            params["page"] = page_no
        if since is not None:
            params["start_date"] = int(since.timestamp() * 1000)
            params["date_query_type"] = "VARIANT_MODIFIED_DATE"
        payload = self._call("list_products", **params)
        return self._parse_page(payload, page_no, size)

    def get_product(self, external_id: str) -> ProviderProduct:
        payload = self._call("list_products", content_id=str(external_id), page=0, size=1)
        page = self._parse_page(payload, 0, 1)
        for item in page.items:
            if item.external_id == str(external_id):
                return item
        if page.rejected:
            raise InvalidProviderResponse(page.rejected[0].message)
        raise ProviderNotFound(f"Trendyol content {external_id} was not found.")

    # ------------------------------------------------------------------ mapping
    def _parse_page(self, payload: Any, page_no: int | None, size: int) -> ProviderPage:
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
            raise InvalidProviderResponse("Trendyol response has no 'content' list.")
        items, rejected = [], []
        for index, raw in enumerate(payload["content"]):
            try:
                items.append(self._map_product(raw))
            except _MappingError as exc:
                ident = clean_str(raw.get("contentId")) if isinstance(raw, dict) else ""
                rejected.append(RejectedItem(ident or f"invalid:{page_no}:{index}", exc.code, str(exc)))
        total_pages = to_int(payload.get("totalPages")) or 0
        warnings, next_cursor = [], None
        if page_no is not None and page_no + 1 < total_pages:
            if (page_no + 2) * size <= MAX_OFFSET_ITEMS:
                next_cursor = f"page:{page_no + 1}"
            elif payload.get("nextPageToken"):
                next_cursor = f"token:{payload['nextPageToken']}"
            else:
                warnings.append("pagination_limit_reached: more pages exist but no nextPageToken was returned")
        elif page_no is None and payload.get("nextPageToken") and payload["content"]:
            next_cursor = f"token:{payload['nextPageToken']}"
        return ProviderPage(
            items=tuple(items),
            next_cursor=next_cursor,
            total=to_int(payload.get("totalElements")),
            rejected=tuple(rejected),
            warnings=tuple(warnings),
        )

    def _map_product(self, raw: Any) -> ProviderProduct:
        if not isinstance(raw, dict):
            raise _MappingError("invalid_item", "A content entry is not an object.")
        content_id = clean_str(raw.get("contentId"))
        if not content_id:
            raise _MappingError("missing_id", "A content entry has no contentId.")
        currency = (self.source.base_currency or "TRY").upper()
        attributes = self._attributes(raw.get("attributes"))
        color = self._first(attributes, _COLOR_NAMES)
        gender = _GENDER_VALUES.get(self._first(attributes, _GENDER_NAMES).casefold(), "")
        brand = raw.get("brand") if isinstance(raw.get("brand"), dict) else {}
        category = raw.get("category") if isinstance(raw.get("category"), dict) else {}
        variants = tuple(self._map_variant(v, currency, color) for v in (raw.get("variants") or []))
        images = tuple(
            ProviderImage(url=clean_str(img.get("url"), 500), position=i)
            for i, img in enumerate(raw.get("images") or [])
            if isinstance(img, dict) and clean_str(img.get("url"))
        )
        first_url = next((v.url for v in variants if v.url), "")
        return ProviderProduct(
            source_code=self.code,
            external_id=content_id,
            title=clean_str(raw.get("title"), 500),
            brand=clean_str(brand.get("name"), 200),
            category_id=clean_str(category.get("id"), 80),
            category_path=clean_str(category.get("name"), 500),
            description=strip_html(clean_str(raw.get("description"))),
            url=first_url,
            language="tr",
            gender=gender,
            manufacturer_code="",  # productMainId is the seller's own grouping code, not a manufacturer MPN
            variants=variants,
            images=images,
            attributes={**attributes, "productMainId": clean_str(raw.get("productMainId"), 80)},
            raw=raw,
            modified_at=self._ms(raw.get("lastModifiedDate")),
            is_listed=True,
        )

    def _map_variant(self, raw: Any, currency: str, product_color: str) -> ProviderVariant:
        if not isinstance(raw, dict):
            raise _MappingError("invalid_variant", "A variant entry is not an object.")
        barcode = clean_str(raw.get("barcode"), 120)
        variant_id = clean_str(raw.get("variantId")) or barcode
        if not variant_id:
            raise _MappingError("variant_missing_id", "A variant has neither variantId nor barcode.")
        attrs = self._attributes(raw.get("attributes"))
        price = raw.get("price") if isinstance(raw.get("price"), dict) else None
        list_amount = to_decimal(price.get("listPrice")) if price else None
        sale_amount = to_decimal(price.get("salePrice")) if price else None
        customer = to_decimal(price.get("priceSeenByCustomer")) if price else None
        provider_price = None
        if list_amount is not None or sale_amount is not None:
            provider_price = ProviderPrice(
                list_amount=list_amount if list_amount is not None else sale_amount,
                sale_amount=sale_amount,
                currency=currency,
                customer_amount=customer,
            )
        archived = bool(raw.get("archived")) or bool(raw.get("blacklisted"))
        quantity = to_int((raw.get("stock") or {}).get("quantity")) if isinstance(raw.get("stock"), dict) else None
        if archived or (raw.get("onSale") is False and quantity is not None):
            status = "out_of_stock"
        elif quantity is None:
            status = "unknown"
        else:
            status = "in_stock" if quantity > 0 else "out_of_stock"
        return ProviderVariant(
            external_variant_id=variant_id,
            sku=clean_str(raw.get("stockCode"), 120),
            barcode=barcode,
            gtin=normalize_gtin(barcode),  # promoted only if it passes the GTIN check digit
            size=self._first(attrs, _SIZE_NAMES),
            color=self._first(attrs, _COLOR_NAMES) or product_color,
            attributes=attrs,
            price=provider_price,
            stock=ProviderStock(status=status, quantity=quantity),
            url=clean_str(raw.get("productUrl"), 500),
            is_active=not archived,
        )

    @staticmethod
    def _attributes(raw) -> dict[str, str]:
        result: dict[str, str] = {}
        for entry in raw or []:
            if isinstance(entry, dict) and clean_str(entry.get("attributeName")):
                result[clean_str(entry["attributeName"], 80)] = clean_str(entry.get("attributeValue"), 200)
        return result

    @staticmethod
    def _first(attributes: dict[str, str], names: set[str]) -> str:
        for name, value in attributes.items():
            if name.casefold() in names and value:
                return value
        return ""

    @staticmethod
    def _ms(value) -> datetime | None:
        number = to_int(value)
        if number is None or number <= 0:
            return None
        try:
            return datetime.fromtimestamp(number / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
