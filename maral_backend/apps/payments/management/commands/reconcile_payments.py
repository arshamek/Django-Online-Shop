from django.core.management.base import BaseCommand

from apps.payments import reconciliation


class Command(BaseCommand):
    help = "Compare payments with what each provider reports (server-to-server) and apply verified results."

    def add_arguments(self, parser):
        parser.add_argument("--provider", default="")
        parser.add_argument("--grace-minutes", type=int, default=10)
        parser.add_argument("--lookback-days", type=int, default=7)

    def handle(self, *args, **opts):
        run = reconciliation.reconcile(
            provider=opts["provider"], grace_minutes=opts["grace_minutes"], lookback_days=opts["lookback_days"]
        )
        self.stdout.write(
            f"scanned={run.scanned} applied={run.applied} errors={run.errors} discrepancies={len(run.discrepancies)}"
        )
        for item in run.discrepancies:
            self.stdout.write(f"  {item}")
