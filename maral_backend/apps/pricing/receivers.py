from django.dispatch import receiver

from apps.currencies.signals import rates_changed


@receiver(rates_changed, dispatch_uid="pricing_reprice_on_rates")
def on_rates_changed(sender, currencies, source=None, **kwargs):
    from . import tasks

    tasks.reprice_currency.delay(sorted(currencies))
