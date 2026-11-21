from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.demo import load_demo_catalog


class Command(BaseCommand):
    help = "Load the fictitious demo catalogue (the 16 products of the frontend mock). Not for production."

    def add_arguments(self, parser):
        parser.add_argument("--assets-dir", help="Directory with the frontend demo images (e.g. ../src/assets).")
        parser.add_argument("--no-images", action="store_true")
        parser.add_argument("--force", action="store_true", help="Allow when DEBUG is off.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to load demo data with DEBUG off (use --force if you really mean it).")
        assets = Path(options["assets_dir"]).resolve() if options["assets_dir"] else None
        result = load_demo_catalog(assets_dir=assets, with_images=not options["no_images"])
        self.stdout.write(self.style.SUCCESS(f"Demo catalogue loaded: {result}"))
