from contextlib import contextmanager

from ..errors import ProviderError
from .base import ExchangeRateProvider
from .fixture import FixtureProvider
from .http_json import HttpJsonProvider
from .manual import ManualProvider

_FACTORIES = {"manual": ManualProvider, "fixture": FixtureProvider, "http_json": HttpJsonProvider}


def get_provider(source) -> ExchangeRateProvider:
    factory = _FACTORIES.get(source.provider_key)
    if factory is None:
        raise ProviderError("unknown_provider", f"Unknown provider {source.provider_key!r}.", transient=False)
    return factory()


@contextmanager
def use_provider(key: str, factory):
    previous = _FACTORIES.get(key)
    _FACTORIES[key] = factory
    try:
        yield
    finally:
        if previous is None:
            _FACTORIES.pop(key, None)
        else:
            _FACTORIES[key] = previous
