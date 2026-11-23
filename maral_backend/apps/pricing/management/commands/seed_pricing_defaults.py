"""Creates the first (all-pending) pricing rule set so the admin panel has something to configure. Idempotent."""

from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.pricing.models import PricingRuleSet
from apps.pricing.services import rulesets


class Command(BaseCommand):
    help = "Seed rule set v1: profit method PENDING, initial 25% target (unconfirmed), shipping+customs required. Nothing is priced until the owner decides."

    def handle(self, *args, **options):
        if PricingRuleSet.objects.exists():
            self.stdout.write("A pricing rule set already exists; nothing to do.")
            return
        rs = PricingRuleSet.objects.create(
            name="Initial (all business decisions pending)",
            profit_method="pending",
            target_profit_percent=Decimal("25"),
            target_profit_confirmed=False,
            required_cost_kinds=["shipping", "customs"],
            required_for_fulfillment=["PROXY_PURCHASE"],
            estimates_policy="pending",
            notes="The 25% target is the initial figure from the project brief. The owner must choose markup or margin, how costs are charged, etc.",
        )
        rulesets.publish(rs)
        self.stdout.write(
            self.style.SUCCESS(
                "Seeded pricing rule set v1 (everything pending). See GET /api/v1/staff/pricing/readiness."
            )
        )
