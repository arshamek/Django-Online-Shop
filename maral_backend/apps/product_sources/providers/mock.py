"""
In-memory provider for tests and local demos. It satisfies the whole BaseProductProvider contract without
any I/O, and can be scripted to fail so the job runner's error handling can be exercised deterministically.
"""

from collections import defaultdict, deque
from datetime import datetime

from .base import BaseProductProvider, ProviderCapabilities
from .dto import ProviderPage, ProviderProduct
from .errors import ProviderNotFound
from .transports import BaseTransport


class _NullTransport(BaseTransport):
    def request(self, operation, **params):  # pragma: no cover - never called
        raise AssertionError("InMemoryProvider does not use a transport")


class InMemoryProvider(BaseProductProvider):
    code = "mock"
    capabilities = ProviderCapabilities(
        category_filter=True, incremental=True, gtin_trusted=True, catalog_scope="fixture"
    )

    def __init__(self, source, products=(), *, failures=None, rejected=(), **kwargs):
        kwargs.setdefault("mode", "fixture")
        super().__init__(source, **kwargs)
        self.products: dict[str, ProviderProduct] = {p.external_id: p for p in products}
        self.rejected = tuple(rejected)
        self._failures: dict[str, deque] = defaultdict(deque)
        for operation, errors in (failures or {}).items():
            self._failures[operation].extend(errors)
        self.calls: list[tuple[str, dict]] = []

    def fail_next(self, operation: str, *errors: Exception) -> None:
        self._failures[operation].extend(errors)

    def build_fixture_transport(self):
        return _NullTransport()

    def build_live_transport(self):
        return _NullTransport()

    def _enter(self, operation: str, **params) -> None:
        self.calls.append((operation, params))
        self._limiter.acquire()
        if self._failures[operation]:
            raise self._failures[operation].popleft()

    def discover(self, *, cursor=None, page_size=50, category=None, since: datetime | None = None) -> ProviderPage:
        self._enter("discover", cursor=cursor, page_size=page_size, category=category)
        items = [p for p in self.products.values() if not category or p.category_path.startswith(category)]
        start = int(cursor[7:]) if cursor else 0
        chunk = items[start : start + page_size]
        more = start + page_size < len(items)
        return ProviderPage(
            tuple(chunk),
            f"offset:{start + page_size}" if more else None,
            len(items),
            self.rejected if start == 0 else (),
        )

    def get_product(self, external_id: str) -> ProviderProduct:
        self._enter("get_product", external_id=external_id)
        try:
            return self.products[str(external_id)]
        except KeyError:
            raise ProviderNotFound(f"{external_id} not found") from None
