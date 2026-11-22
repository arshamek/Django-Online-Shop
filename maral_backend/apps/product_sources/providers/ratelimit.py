"""
Per-source request ceiling shared by every worker (Django cache; Redis in production).

We never sleep inside a worker and never retry sooner than we were told: when the budget is spent, or the
source asked us to back off, `acquire()` raises ProviderRateLimited(retry_after) and the job waits.
"""

import time

from django.core.cache import cache as default_cache

from .errors import ProviderRateLimited


class SourceRateLimiter:
    def __init__(self, source_code: str, per_minute: int, *, cache=None, clock=time.time):
        self.code = source_code
        self.per_minute = max(1, int(per_minute or 1))
        self.cache = cache or default_cache
        self.clock = clock

    @property
    def _cooloff_key(self) -> str:
        return f"imports:cooloff:{self.code}"

    def acquire(self) -> None:
        now = self.clock()
        until = self.cache.get(self._cooloff_key)
        if until and until > now:
            raise ProviderRateLimited("The source asked us to back off.", retry_after=max(1.0, until - now))
        window = int(now // 60)
        key = f"imports:rl:{self.code}:{window}"
        self.cache.add(key, 0, timeout=120)
        try:
            used = self.cache.incr(key)
        except ValueError:  # key expired between add and incr
            self.cache.set(key, 1, timeout=120)
            used = 1
        if used > self.per_minute:
            raise ProviderRateLimited(
                f"Local ceiling of {self.per_minute} requests/min reached.", retry_after=60 - (now % 60) + 1
            )

    def cool_off(self, seconds: float) -> None:
        """Record an upstream 429 / Retry-After so *all* workers stop calling the source until then."""
        seconds = max(1.0, float(seconds))
        self.cache.set(self._cooloff_key, self.clock() + seconds, timeout=int(seconds) + 60)
