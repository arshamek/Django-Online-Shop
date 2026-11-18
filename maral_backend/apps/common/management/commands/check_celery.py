from celery.exceptions import TimeoutError as CeleryTimeoutError
from django.core.management.base import BaseCommand, CommandError

from apps.common.tasks import ping
from config.celery import app


class Command(BaseCommand):
    help = "Send common.ping to the workers and wait for the result (needs broker, worker and result backend)."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=15)

    def handle(self, *args, **options):
        timeout = options["timeout"]
        workers = app.control.inspect(timeout=3).ping() or {}
        if workers:
            self.stdout.write(f"Workers online: {', '.join(sorted(workers))}")
        else:
            self.stdout.write(self.style.WARNING("No workers answered the ping (is the worker running?)"))
        result = ping.delay()
        try:
            payload = result.get(timeout=timeout)
        except CeleryTimeoutError as exc:
            raise CommandError(
                f"Task {result.id} did not finish within {timeout}s (worker down or wrong queue)."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Task {result.id} failed: {type(exc).__name__}: {exc}") from exc
        self.stdout.write(f"Task {result.id} -> {payload}")
        self.stdout.write(self.style.SUCCESS("Celery OK"))
