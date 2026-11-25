from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.payments.models import Payment
from apps.payments.providers.mock import MockRemote


class Command(BaseCommand):
    help = "DEV ONLY: make a mock provider report a payment as paid/failed, then run `reconcile_payments` (or the callback) to apply it."

    def add_arguments(self, parser):
        parser.add_argument("payment_id")
        parser.add_argument("status", choices=["captured", "failed"])

    def handle(self, *args, **opts):
        if not settings.PAYMENTS_ALLOW_FAKE_PROVIDER:
            raise CommandError("Mock providers are disabled (PAYMENTS_ALLOW_FAKE_PROVIDER=false).")
        payment = Payment.objects.filter(public_id=opts["payment_id"]).first()
        if payment is None or payment.provider not in ("mock_ir", "mock_intl"):
            raise CommandError("Payment not found or not a mock_ir / mock_intl payment.")
        MockRemote.simulate(payment.provider_ref, opts["status"])
        self.stdout.write(f"{payment.provider_ref} is now {opts['status']} at the (mock) provider.")
