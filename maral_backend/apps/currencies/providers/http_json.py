"""
Generic JSON-over-HTTPS rate adapter, configured per source (no code change per provider).

config = {
  "url": "https://rates.example.com/latest",            # https only, host must be in CURRENCY_HTTP_ALLOWED_HOSTS
  "auth_header": "Authorization", "auth_prefix": "Bearer ",   # key from env CURRENCY_CRED_<credential_ref>
  "pairs": [{"base": "TRY", "quote": "IRR", "path": "rates.TRY", "multiplier": "1", "invert": false}],
  "as_of_path": "timestamp", "as_of_format": "epoch" | "iso",
  "trust_fetch_time": false                              # true = use our clock when the API gives no timestamp
}
Numbers are parsed straight to Decimal (never through float). NOT verified against any live service.
"""

import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..errors import ProviderError
from .base import ExchangeRateProvider, RateObservation

MAX_BYTES = 1024 * 1024
_transport = None  # tests inject fn(url, headers, timeout) -> (status, text)


def set_transport(fn):
    global _transport
    previous, _transport = _transport, fn
    return previous


def _default_transport(url, headers, timeout):
    import urllib.request

    request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310 - https + allow-list enforced
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.status, response.read(MAX_BYTES + 1).decode("utf-8", "replace")


def _dig(data, path):
    for part in path.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            raise KeyError(path)
    return data


class HttpJsonProvider(ExchangeRateProvider):
    key = "http_json"

    def fetch(self, source):
        cfg = source.config or {}
        url = cfg.get("url", "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in settings.CURRENCY_HTTP_ALLOWED_HOSTS:
            raise ProviderError("host_not_allowed", "URL must be https and its host allow-listed.", transient=False)
        headers = {"Accept": "application/json"}
        if cfg.get("auth_header"):
            key = os.environ.get(f"CURRENCY_CRED_{source.credential_ref.upper()}", "") if source.credential_ref else ""
            if not key:
                raise ProviderError("credentials_missing", "No credential configured for this source.", transient=False)
            headers[cfg["auth_header"]] = f"{cfg.get('auth_prefix', '')}{key}"
        started = time.monotonic()
        try:
            status, text = (_transport or _default_transport)(url, headers, 10)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("network_error", f"{type(exc).__name__}", transient=True) from None
        if status == 429 or status >= 500:
            raise ProviderError(f"http_{status}", "Provider unavailable.", transient=True)
        if status != 200 or len(text) > MAX_BYTES:
            raise ProviderError(f"http_{status}", "Unexpected provider response.", transient=False)
        try:
            data = json.loads(text, parse_float=Decimal, parse_int=Decimal)
        except ValueError:
            raise ProviderError("invalid_json", "Provider returned invalid JSON.", transient=False) from None
        as_of = self._as_of(cfg, data)
        out = []
        for pair in cfg.get("pairs", []):
            try:
                value = Decimal(str(_dig(data, pair["path"]))) * Decimal(str(pair.get("multiplier", "1")))
                if pair.get("invert"):
                    value = Decimal(1) / value
            except (KeyError, InvalidOperation, ZeroDivisionError):
                continue  # a missing pair is simply absent; the route then reports the rate as missing
            out.append(
                RateObservation(
                    pair["base"], pair["quote"], value, as_of, {"latency_ms": int((time.monotonic() - started) * 1000)}
                )
            )
        return out

    @staticmethod
    def _as_of(cfg, data):
        path = cfg.get("as_of_path")
        if path:
            try:
                raw = _dig(data, path)
                if cfg.get("as_of_format", "iso") == "epoch":
                    return datetime.fromtimestamp(int(raw), UTC)
                parsed = parse_datetime(str(raw))
                return parsed  # naive values are rejected later (timestamp_naive)
            except (KeyError, ValueError, OverflowError, InvalidOperation):
                return None
        return timezone.now() if cfg.get("trust_fetch_time") else None
