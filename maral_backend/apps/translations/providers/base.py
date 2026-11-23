"""TranslationProvider port. Adapters translate one already-masked text; protection/validation happen outside."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationRequest:
    text: str
    source_language: str
    target_language: str
    context: str = ""  # e.g. "product title" - helps register, never contains customer data


@dataclass(frozen=True)
class TranslationResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class TranslationProvider(ABC):
    name = ""
    model = ""
    LIVE_VERIFIED = False  # no adapter has been exercised against a live service in this repository

    @property
    def fingerprint(self) -> str:
        return f"{self.name}:{self.model}"

    @abstractmethod
    def translate(self, request: TranslationRequest) -> TranslationResult:
        """Return the translation or raise TranslationProviderError."""
