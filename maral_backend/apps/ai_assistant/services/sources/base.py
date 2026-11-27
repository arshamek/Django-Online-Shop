"""Shared structures of the product-discovery layer."""

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class SearchSpec:
    """Structured filters extracted from the conversation (never raw customer text)."""

    language: str = "fa"
    concept: str = ""
    brand_ids: list = field(default_factory=list)
    brands: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    uses: list = field(default_factory=list)
    colors: list = field(default_factory=list)
    sizes: list = field(default_factory=list)
    min_irr: Decimal | None = None
    max_irr: Decimal | None = None
    sort: str = "relevance"  # relevance | price_asc
    sources: list = field(default_factory=list)  # enabled + requested sources

    def has_terms(self) -> bool:
        return bool(self.concept or self.brands or self.brand_ids or self.keywords)

    def as_public(self) -> dict:
        def toman(v):
            return None if v is None else str(int(v / 10))

        return {
            "category_concept": self.concept or None,
            "brands": self.brands,
            "keywords": self.keywords,
            "intended_use": self.uses,
            "colors": self.colors,
            "sizes": self.sizes,
            "budget": {"min": toman(self.min_irr), "max": toman(self.max_irr), "currency": "IRT"}
            if (self.min_irr is not None or self.max_irr is not None)
            else None,
            "sort": self.sort,
            "sources": self.sources,
        }


@dataclass
class Candidate:
    data: dict  # public JSON representation
    source: str
    kind: str  # internal | snapshot
    pk: int
    price_irr: Decimal | None = None
    canonical_product_id: int | None = None
    score: float = 0.0
    matched: list = field(default_factory=list)
    unmet: list = field(default_factory=list)


@dataclass
class SourceReport:
    source: str
    status: str  # ProviderStatus of the source as a whole: for Trendyol/Namshi the LIVE-search capability (or ERROR / NOT_CONFIGURED)
    detail: str = ""
    mode: str = ""  # internal_catalog | imported_snapshot | live (never produced today)
    searched: bool = False
    result_count: int = 0
    live_search: str = "disabled"  # disabled | n/a
    live_search_status: str = ""
    snapshot_status: str = ""  # status of the stored-snapshot search that actually ran (empty for Maral)
    relaxed: list = field(default_factory=list)
    excluded_no_price: int = 0

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "status": self.status,
            "detail": self.detail,
            "mode": self.mode,
            "searched": self.searched,
            "result_count": self.result_count,
            "live_search": self.live_search,
            "live_search_status": self.live_search_status or None,
            "snapshot_search_status": self.snapshot_status or None,
            "relaxed_filters": self.relaxed,
            "excluded_without_price": self.excluded_no_price,
        }
