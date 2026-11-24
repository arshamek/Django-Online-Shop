"""DEMO ONLY: the numbers the existing React checkout shows (standard 450k Toman, free from 20M; express 950k)."""

from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.shipping.models import ShippingMethod


class Command(BaseCommand):
    help = "Create the demo delivery methods from the frontend mock (marked 'estimated'). Not for production data."

    def handle(self, *args, **options):
        ShippingMethod.objects.update_or_create(
            code="standard",
            defaults={"names": {"fa": "ارسال استاندارد", "en": "Standard delivery"}, "fee_irr": Decimal("4500000"),
                      "free_over_irr": Decimal("200000000"), "value_status": "estimated", "countries": ["IR"], "sort_order": 10},
        )
        ShippingMethod.objects.update_or_create(
            code="express",
            defaults={"names": {"fa": "ارسال سریع", "en": "Express delivery"}, "fee_irr": Decimal("9500000"),
                      "free_over_irr": None, "value_status": "estimated", "countries": ["IR"], "sort_order": 20},
        )
        self.stdout.write("Demo delivery methods loaded (estimated values).")
