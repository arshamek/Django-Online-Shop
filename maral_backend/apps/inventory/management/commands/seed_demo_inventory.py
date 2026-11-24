"""DEMO ONLY: give every DIRECT_INVENTORY variant some stock so the demo storefront can be ordered end to end."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import FulfillmentType, ProductVariant
from apps.inventory.models import InventoryItem
from apps.inventory.services import stock


class Command(BaseCommand):
    help = "Receive demo stock for direct-inventory variants that have none. Refuses to run when DEBUG is off."

    def add_arguments(self, parser):
        parser.add_argument("--quantity", type=int, default=20)

    def handle(self, *args, quantity, **options):
        if not settings.DEBUG:
            raise CommandError("Demo stock is for development only (DEBUG is off).")
        count = 0
        for variant in ProductVariant.objects.filter(fulfillment_type=FulfillmentType.DIRECT_INVENTORY, is_active=True):
            if not InventoryItem.objects.filter(variant=variant).exists():
                stock.receive(variant, quantity, reason="demo seed")
                count += 1
        self.stdout.write(f"Demo stock received for {count} variants.")
