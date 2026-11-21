from django.core.management.base import BaseCommand

from apps.catalog.services import projection


class Command(BaseCommand):
    help = "Rebuild the product listing projection from the source tables (safe to re-run)."

    def handle(self, *args, **options):
        result = projection.rebuild_all()
        self.stdout.write(self.style.SUCCESS(f"Projection rebuilt: {result}"))
