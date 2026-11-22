"""Provider lookup by source code, plus a test/dev override hook."""

from collections.abc import Callable
from contextlib import contextmanager

from ..models import ProductSource
from .base import BaseProductProvider
from .errors import ProviderNotConfigured

_OVERRIDES: dict[str, Callable[..., BaseProductProvider]] = {}


def _builtin() -> dict[str, type[BaseProductProvider]]:
    from .namshi import NamshiProvider
    from .trendyol import TrendyolProvider

    return {TrendyolProvider.code: TrendyolProvider, NamshiProvider.code: NamshiProvider}


def get_provider(source: ProductSource, **kwargs) -> BaseProductProvider:
    if source.code in _OVERRIDES:
        return _OVERRIDES[source.code](source, **kwargs)
    cls = _builtin().get(source.code)
    if cls is None:
        raise ProviderNotConfigured(f"No import provider is registered for source '{source.code}'.")
    return cls(source, **kwargs)


@contextmanager
def use_provider(code: str, factory: Callable[..., BaseProductProvider]):
    """Swap a provider for the duration of a block (tests, local demos)."""
    previous = _OVERRIDES.get(code)
    _OVERRIDES[code] = factory
    try:
        yield
    finally:
        if previous is None:
            _OVERRIDES.pop(code, None)
        else:
            _OVERRIDES[code] = previous


def registered_codes() -> list[str]:
    return sorted(set(_builtin()) | set(_OVERRIDES))
