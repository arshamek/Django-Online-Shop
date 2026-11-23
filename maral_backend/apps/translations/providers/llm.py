"""
AI translation adapter (LLM over HTTPS, Anthropic Messages API shape). Server-side only: the key comes from
AI_SETTINGS (environment), is sent in a request header and never logged, stored or returned by any API.
NOT verified against the live service in this repository (LIVE_VERIFIED = False).
"""

import json

from django.conf import settings

from ..errors import TranslationProviderError
from .base import TranslationProvider, TranslationRequest, TranslationResult

LANGUAGE_NAMES = {"fa": "Persian", "en": "English", "ar": "Arabic", "tr": "Turkish"}
SYSTEM = (
    "You are a professional e-commerce translator. Translate the user's text from {src} to {dst}. "
    "Tokens written like ⟦12⟧ are protected brand names, codes, measurements or numbers: copy each one exactly, "
    "once, where it belongs grammatically. Never add, remove or change numbers. Keep any HTML tags and line breaks. "
    "Return only the translation, with no commentary or quotation marks."
)
_transport = None  # tests inject fn(url, headers, body_bytes, timeout) -> (status, text)


def set_transport(fn):
    global _transport
    previous, _transport = _transport, fn
    return previous


def _default_transport(url, headers, body, timeout):
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 - https enforced below
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(20_000).decode("utf-8", "replace")


class LLMTranslationProvider(TranslationProvider):
    name = "llm"

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None):
        ai = settings.AI_SETTINGS
        self.model = model or ai.get("model", "")
        self._key = api_key if api_key is not None else (ai.get("translation_api_key") or ai.get("api_key", ""))
        self.base_url = (base_url or settings.TRANSLATION_LLM_BASE_URL).rstrip("/")

    def translate(self, request: TranslationRequest) -> TranslationResult:
        if not self._key or not self.model:
            raise TranslationProviderError(
                "provider_not_configured", "Translation credentials or model are not configured."
            )
        if not self.base_url.startswith("https://"):
            raise TranslationProviderError(
                "insecure_endpoint", "The translation endpoint must use https.", transient=False
            )
        if len(request.text) > settings.TRANSLATION_MAX_CHARS:
            raise TranslationProviderError("text_too_long", "The text exceeds the maximum length.", transient=False)
        payload = {
            "model": self.model,
            "max_tokens": 2048,
            "system": SYSTEM.format(
                src=LANGUAGE_NAMES[request.source_language], dst=LANGUAGE_NAMES[request.target_language]
            )
            + (f" Context: {request.context}." if request.context else ""),
            "messages": [{"role": "user", "content": request.text}],
        }
        headers = {"content-type": "application/json", "x-api-key": self._key, "anthropic-version": "2023-06-01"}
        try:
            status, text = (_transport or _default_transport)(
                f"{self.base_url}/v1/messages", headers, json.dumps(payload).encode(), 60
            )
        except Exception as exc:  # noqa: BLE001 - never echo exception text (it could contain the URL/headers)
            raise TranslationProviderError("network_error", type(exc).__name__) from None
        if status in (408, 409, 429) or status >= 500:
            raise TranslationProviderError(f"http_{status}", "The translation service is busy or unavailable.")
        if status in (401, 403):
            raise TranslationProviderError(
                "auth_failed", "The translation service rejected the credentials.", transient=False
            )
        if status != 200:
            raise TranslationProviderError(
                f"http_{status}", "Unexpected response from the translation service.", transient=False
            )
        try:
            body = json.loads(text)
            out = "".join(block["text"] for block in body["content"] if block.get("type") == "text").strip()
            usage = body.get("usage", {})
        except (ValueError, KeyError, TypeError):
            raise TranslationProviderError(
                "invalid_response", "The translation service returned an unreadable response."
            ) from None
        if not out:
            raise TranslationProviderError("empty_translation", "The translation service returned nothing.")
        return TranslationResult(
            out, self.name, self.model, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        )
