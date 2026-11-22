"""Transports return the source's *native* payload; providers map it. Fixture transports replay JSON files."""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .errors import InvalidProviderResponse, ProviderNotConfigured

FIXTURE_DIR = Path(__file__).parent / "fixtures"
MAX_FEED_BYTES = 64 * 1024 * 1024


class BaseTransport(ABC):
    @abstractmethod
    def request(self, operation: str, **params) -> Any: ...


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise ProviderNotConfigured(f"Feed file not found: {path.name}")
    if path.stat().st_size > MAX_FEED_BYTES:
        raise InvalidProviderResponse("The feed file is larger than the allowed maximum.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise InvalidProviderResponse(f"{path.name} is not valid JSON.") from None
