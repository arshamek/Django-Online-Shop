import redis
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Verify Redis connectivity for the cache and the Celery broker."

    def handle(self, *args, **options):
        targets = {"cache/redis": settings.REDIS_URL, "celery broker": settings.CELERY_BROKER_URL}
        for label, url in targets.items():
            if not url.startswith(("redis://", "rediss://")):
                self.stdout.write(f"{label}: skipped (not a Redis URL)")
                continue
            try:
                client = redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
                client.ping()
                info = client.info("server")
                try:
                    policy = client.config_get("maxmemory-policy").get("maxmemory-policy", "?")
                except redis.RedisError:  # CONFIG is disabled/renamed on some managed Redis services
                    policy = "?"
            except Exception as exc:  # noqa: BLE001
                raise CommandError(f"{label}: Redis connection FAILED: {type(exc).__name__}: {exc}") from exc
            self.stdout.write(f"{label}: OK (redis {info.get('redis_version')}, maxmemory-policy={policy})")
            if policy not in {"noeviction", "?"}:
                self.stdout.write(
                    self.style.WARNING(
                        "  Celery broker data can be evicted under memory pressure; use maxmemory-policy noeviction."
                    )
                )
        self.stdout.write(self.style.SUCCESS("Redis OK"))
