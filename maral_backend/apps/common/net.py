"""Client IP resolution that cannot be spoofed beyond the configured number of trusted proxies."""

from django.conf import settings


def get_client_ip(request) -> str | None:
    if request is None:
        return None
    meta = getattr(request, "META", {})
    proxies = settings.TRUSTED_PROXY_COUNT
    forwarded = [part.strip() for part in meta.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
    if proxies and len(forwarded) >= proxies:
        # The client address is the entry appended by our outermost trusted proxy.
        return forwarded[-proxies]
    return meta.get("REMOTE_ADDR")
