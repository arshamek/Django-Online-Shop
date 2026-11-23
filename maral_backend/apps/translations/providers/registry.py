from contextlib import contextmanager

from django.conf import settings

from ..errors import TranslationProviderError
from .base import TranslationProvider
from .fake import FakeProvider
from .llm import LLMTranslationProvider

_override: TranslationProvider | None = None


def get_provider() -> TranslationProvider:
    if _override is not None:
        return _override
    key = settings.TRANSLATION_PROVIDER
    if key == "fake":
        return FakeProvider()
    if key == "llm":
        return LLMTranslationProvider()
    raise TranslationProviderError("unknown_provider", f"Unknown translation provider {key!r}.", transient=False)


@contextmanager
def use_provider(provider: TranslationProvider):
    global _override
    previous, _override = _override, provider
    try:
        yield provider
    finally:
        _override = previous
