"""
Catalogue response cache. Every key embeds a version number; any catalogue write bumps the version
(see signals.py), which orphans old keys without scanning the cache. Public GETs are user-independent
(no wishlist state), so one entry serves everyone for a (language, currency) pair.
"""

import hashlib
import json
import time

from django.core.cache import cache

VERSION_KEY = "catalog:version"
TTL_SECONDS = 60


def version() -> int:
    value = cache.get(VERSION_KEY)
    if value is None:
        value = int(time.time() * 1000)
        cache.add(VERSION_KEY, value, None)
        value = cache.get(VERSION_KEY, value)
    return value


def bump_version() -> None:
    cache.set(VERSION_KEY, int(time.time() * 1000000), None)


def make_key(namespace: str, **parts) -> str:
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]
    return f"catalog:{namespace}:{version()}:{digest}"


def cached(namespace: str, parts: dict, builder, ttl: int = TTL_SECONDS):
    key = make_key(namespace, **parts)
    hit = cache.get(key)
    if hit is not None:
        return hit
    value = builder()
    cache.set(key, value, ttl)
    return value
