"""
A small, strict HTTP client for real provider adapters: https only, hard timeouts, no redirects followed, bounded
response size, JSON in / JSON out, and no secret ever logged. Adapters pass credentials in headers they build
themselves; nothing here reads the database or the request.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import structlog

from .base import ProviderError

logger = structlog.get_logger("maral.payments.http")

MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_TIMEOUT = 15


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_json(
    method: str, url: str, *, headers: dict | None = None, payload: dict | None = None, timeout: float = DEFAULT_TIMEOUT
) -> dict:
    """Raises ProviderError for transport problems, non-2xx answers and malformed JSON (the outcome is then unknown)."""
    if not url.lower().startswith("https://"):
        raise ProviderError("Provider endpoints must use https.")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        logger.warning("provider_http_error", host=urllib.parse.urlparse(url).hostname, status=exc.code)
        raise ProviderError(f"provider answered HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("provider_http_unreachable", host=urllib.parse.urlparse(url).hostname, error=type(exc).__name__)
        raise ProviderError("provider unreachable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError("provider response too large")
    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise ProviderError("provider returned malformed JSON") from None
