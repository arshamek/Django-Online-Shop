"""
Progressive login throttling per account.

After LOGIN_LOCKOUT_THRESHOLD consecutive failures the account is locked for a time that doubles
with every further failure (1 min, 2, 4 ... capped at 1 h). Existing accounts keep their counters in
the database (durable even if Redis is down); unknown identifiers are tracked in the cache so the
observable behaviour (429 + Retry-After) is identical and does not reveal whether an account exists.
Trade-off: an attacker can keep a *known* identifier locked; the cap and the per-IP throttle bound it.
"""

import hashlib
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import Throttled

from apps.audit import services as audit


def identifier_key(identifier: str) -> str:
    return hashlib.sha256(identifier.encode()).hexdigest()[:16]


def lock_seconds(failures: int) -> int:
    over = failures - settings.LOGIN_LOCKOUT_THRESHOLD
    if over < 0:
        return 0
    return min(settings.LOGIN_LOCKOUT_BASE_SECONDS * (2**over), settings.LOGIN_LOCKOUT_MAX_SECONDS)


def _cache_key(key: str) -> str:
    return f"login-lock:{key}"


def ensure_not_locked(user, key: str) -> None:
    if user is not None:
        if user.is_locked:
            raise Throttled(wait=max(1, int((user.locked_until - timezone.now()).total_seconds())))
        return
    state = cache.get(_cache_key(key))
    if state and state["until"] > time.time():
        raise Throttled(wait=max(1, int(state["until"] - time.time())))


def register_failure(request, user, key: str) -> None:
    if user is not None:
        type(user).objects.filter(pk=user.pk).update(failed_login_count=F("failed_login_count") + 1)
        user.refresh_from_db(fields=["failed_login_count"])
        seconds = lock_seconds(user.failed_login_count)
        if seconds:
            user.locked_until = timezone.now() + timedelta(seconds=seconds)
            user.save(update_fields=["locked_until"])
            audit.record(
                "auth.account.locked",
                request=request,
                actor=None,
                target=user,
                failures=user.failed_login_count,
                seconds=seconds,
            )
        return
    state = cache.get(_cache_key(key)) or {"count": 0, "until": 0}
    state["count"] += 1
    seconds = lock_seconds(state["count"])
    if seconds:
        state["until"] = time.time() + seconds
    cache.set(_cache_key(key), state, timeout=settings.LOGIN_LOCKOUT_MAX_SECONDS * 2)


def register_success(user, key: str) -> None:
    if user.failed_login_count or user.locked_until:
        user.failed_login_count = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_count", "locked_until"])
    cache.delete(_cache_key(key))
