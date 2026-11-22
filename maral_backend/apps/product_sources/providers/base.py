"""
BaseProductProvider: the single seam between the import engine and any product source.

Everything a provider returns is a provider-neutral DTO (dto.py). A provider is free to implement only the
operations its authorised access actually allows; the rest raise UnsupportedOperation and `capabilities`
says so up front. Operations that the source returns in one response (variants, prices, stock, images)
default to slicing `get_product`, so a provider that only has a details endpoint still satisfies the whole
interface.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from django.conf import settings

from ..models import ProductSource
from .authorization import assert_source_authorized
from .dto import (
    ProviderImage,
    ProviderPage,
    ProviderPrice,
    ProviderProduct,
    ProviderStock,
    ProviderVariant,
    SyncResult,
)
from .errors import ProviderNotFound, ProviderRateLimited, UnsupportedOperation
from .ratelimit import SourceRateLimiter


@dataclass(frozen=True)
class ProviderCapabilities:
    discovery: bool = True
    details: bool = True
    variants: bool = True
    prices: bool = True
    stock: bool = True
    images: bool = True
    category_filter: bool = False  # discovery can be narrowed to a category
    incremental: bool = False  # discovery can be narrowed to "changed since"
    gtin_trusted: bool = False  # the source's barcode/GTIN field is a real GTIN (else it is only a hint)
    catalog_scope: str = "unknown"  # own_seller_catalog | licensed_feed | fixture


class BaseProductProvider(ABC):
    code: ClassVar[str] = ""
    capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities()
    #: True only after the live adapter has been exercised against the real (sandbox) service.
    LIVE_VERIFIED: ClassVar[bool] = False

    def __init__(self, source: ProductSource, *, transport=None, limiter: SourceRateLimiter | None = None, mode=None):
        self.source = source
        self.mode = mode or getattr(settings, "IMPORTS_PROVIDER_MODE", "fixture")
        if self.mode not in {"fixture", "live"}:
            raise ValueError(f"Unknown provider mode {self.mode!r}.")
        self._transport = transport
        self._limiter = limiter or SourceRateLimiter(source.code, source.rate_limit_per_min)

    # ------------------------------------------------------------------ readiness
    @property
    def data_origin(self) -> str:
        return "live" if self.mode == "live" else "fixture"

    def assert_ready(self) -> None:
        """Raises ProviderNotAuthorized / ProviderNotConfigured when this provider may not be called."""
        if self.mode == "live":
            assert_source_authorized(self.source)
        if self._transport is None:
            self._transport = self.build_live_transport() if self.mode == "live" else self.build_fixture_transport()

    @property
    def transport(self):
        self.assert_ready()
        return self._transport

    @abstractmethod
    def build_fixture_transport(self):
        """Offline stand-in that speaks the source's native payload shape."""

    @abstractmethod
    def build_live_transport(self):
        """Transport for authorised live access. Must raise ProviderNotConfigured without credentials."""

    def _call(self, operation: str, **params) -> Any:
        self._limiter.acquire()
        try:
            return self.transport.request(operation, **params)
        except ProviderRateLimited as exc:
            self._limiter.cool_off(exc.retry_after or 60)
            raise

    # ------------------------------------------------------------------ discovery / details
    @abstractmethod
    def discover(
        self,
        *,
        cursor: str | None = None,
        page_size: int = 50,
        category: str | None = None,
        since: datetime | None = None,
    ) -> ProviderPage:
        """One page of products with variants, prices, stock and image metadata."""

    @abstractmethod
    def get_product(self, external_id: str) -> ProviderProduct:
        """Raises ProviderNotFound when the source no longer lists the product."""

    def _require(self, flag: bool, operation: str) -> None:
        if not flag:
            raise UnsupportedOperation(f"{self.code} does not support {operation} with its authorised access.")

    # ------------------------------------------------------------------ derived operations
    def get_variants(self, external_id: str) -> tuple[ProviderVariant, ...]:
        self._require(self.capabilities.variants, "variants")
        return self.get_product(external_id).variants

    def get_prices(self, external_id: str) -> dict[str, ProviderPrice]:
        self._require(self.capabilities.prices, "prices")
        return {v.external_variant_id: v.price for v in self.get_product(external_id).variants if v.price}

    def get_stock(self, external_id: str) -> dict[str, ProviderStock]:
        self._require(self.capabilities.stock, "stock")
        return {v.external_variant_id: v.stock for v in self.get_product(external_id).variants if v.stock}

    def get_images(self, external_id: str) -> tuple[ProviderImage, ...]:
        self._require(self.capabilities.images, "image metadata")
        return self.get_product(external_id).images

    def get_source_url(self, external_id: str) -> str:
        return self.get_product(external_id).url

    # ------------------------------------------------------------------ synchronisation
    def sync(self, external_ids: Sequence[str]) -> Iterator[SyncResult]:
        """Re-read known listings. A product the source no longer has yields `not_found` (it is delisted,
        not failed). Retryable errors propagate: the job runner decides when to try again."""
        for external_id in external_ids:
            try:
                yield SyncResult(external_id, self.get_product(external_id))
            except ProviderNotFound:
                yield SyncResult(external_id, None, not_found=True)

    def iter_pages(self, **kwargs) -> Iterator[ProviderPage]:
        cursor = kwargs.pop("cursor", None)
        while True:
            page = self.discover(cursor=cursor, **kwargs)
            yield page
            if not page.next_cursor:
                return
            cursor = page.next_cursor
