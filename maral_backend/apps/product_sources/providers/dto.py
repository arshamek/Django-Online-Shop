"""
Provider-neutral data transfer objects. Providers translate their native payloads into these; nothing
past this point knows what Trendyol or Namshi look like. `raw` keeps the payload as received.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ProviderImage:
    url: str
    position: int = 0
    width: int | None = None
    height: int | None = None
    alt: str = ""


@dataclass(frozen=True)
class ProviderPrice:
    list_amount: Decimal
    sale_amount: Decimal | None
    currency: str
    customer_amount: Decimal | None = None  # what a shopper sees at the source, when the source says so


@dataclass(frozen=True)
class ProviderStock:
    status: str  # in_stock | out_of_stock | unknown
    quantity: int | None = None


@dataclass(frozen=True)
class ProviderVariant:
    external_variant_id: str
    sku: str = ""
    barcode: str = ""
    gtin: str = ""  # only ever a *validated* GTIN
    size: str = ""
    color: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    price: ProviderPrice | None = None
    stock: ProviderStock | None = None
    url: str = ""
    is_active: bool = True


@dataclass(frozen=True)
class ProviderProduct:
    source_code: str
    external_id: str
    title: str
    brand: str = ""
    category_id: str = ""
    category_path: str = ""
    description: str = ""
    url: str = ""
    language: str = ""
    gender: str = ""  # men | women | kids | unisex | ""
    manufacturer_code: str = ""  # a *manufacturer* model code only; never a seller's own style code
    variants: tuple[ProviderVariant, ...] = ()
    images: tuple[ProviderImage, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    modified_at: datetime | None = None
    is_listed: bool = True


@dataclass(frozen=True)
class RejectedItem:
    """One malformed record inside an otherwise good page. The page still imports (partial import)."""

    external_id: str
    code: str
    message: str


@dataclass(frozen=True)
class ProviderPage:
    items: tuple[ProviderProduct, ...]
    next_cursor: str | None = None
    total: int | None = None
    rejected: tuple[RejectedItem, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncResult:
    external_id: str
    product: ProviderProduct | None  # None = the source no longer lists it
    not_found: bool = False
