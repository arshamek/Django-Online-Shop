"""Deterministic stand-in for development and tests. Output is clearly marked; never use it for real content."""

from ..errors import TranslationProviderError
from .base import TranslationProvider, TranslationRequest, TranslationResult


class FakeProvider(TranslationProvider):
    name = "fake"
    model = "echo-v1"

    def __init__(self, dictionary: dict | None = None):
        self.dictionary = dictionary or {}
        self.calls: list[TranslationRequest] = []

    def translate(self, request: TranslationRequest) -> TranslationResult:
        from django.conf import settings

        if not settings.TRANSLATION_ALLOW_FAKE_PROVIDER:
            raise TranslationProviderError(
                "fake_not_allowed", "The fake translation provider is disabled here.", transient=False
            )
        self.calls.append(request)
        text = (
            self.dictionary.get((request.text, request.target_language))
            or f"[{request.target_language}] {request.text}"
        )
        return TranslationResult(text, self.name, self.model, len(request.text), len(text))
