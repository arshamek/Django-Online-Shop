"""
Minimal, defensive HTTP client for *authorised* provider APIs (stdlib only).

Guard rails (each one is tested): https only, a fixed host allow-list, redirects refused, bounded response
size, bounded time, and no secrets in exception text. It does not rotate IPs, spoof user agents, solve
CAPTCHAs or evade anything: a 401/403/429 is reported, never worked around.
"""

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from .errors import (
    InvalidProviderResponse,
    ProviderNotAuthorized,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 15.0


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # surfaces the 3xx as an HTTPError instead of following it


def _retry_after(headers) -> float:
    raw = (headers.get("Retry-After") if headers else None) or ""
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 60.0


class SafeHttpClient:
    def __init__(self, allowed_hosts: Iterable[str], *, timeout: float = DEFAULT_TIMEOUT, opener=None):
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(_RefuseRedirects)

    def get_json(self, url: str, headers: dict[str, str]) -> Any:
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in self.allowed_hosts:
            raise ProviderNotAuthorized("Refusing to call a host that is not on the provider allow-list.")
        request = urllib.request.Request(url, headers={**headers, "Accept": "application/json"}, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise self._map_http_error(exc) from None
        except TimeoutError:
            raise ProviderTimeout("The source did not answer in time.") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeout("The source did not answer in time.") from None
            raise ProviderUnavailable("The source could not be reached.") from None
        except OSError:
            raise ProviderUnavailable("The source could not be reached.") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise InvalidProviderResponse("The response is larger than the allowed maximum.")
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise InvalidProviderResponse("The response is not valid JSON.") from None

    @staticmethod
    def _map_http_error(exc: urllib.error.HTTPError):
        status = exc.code
        if status in (401, 403):
            return ProviderNotAuthorized(f"The source refused access (HTTP {status}).")
        if status == 404:
            return ProviderNotFound("The source has no such resource.")
        if status == 429:
            return ProviderRateLimited("The source rate limit was hit.", retry_after=_retry_after(exc.headers))
        if status == 408:
            return ProviderTimeout("The source timed out.")
        if 500 <= status < 600:
            return ProviderUnavailable(f"The source failed (HTTP {status}).")
        return InvalidProviderResponse(f"Unexpected response from the source (HTTP {status}).")
