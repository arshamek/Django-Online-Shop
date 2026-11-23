from decimal import Decimal

from django.db import migrations

CURRENCIES = [
    # code, name, symbol, decimals, base, factor, is_source, is_display, sort
    ("IRR", "Iranian rial", "ريال", 0, None, "1", False, True, 1),
    ("IRT", "Iranian toman", "تومان", 0, "IRR", "10", False, True, 2),
    ("TRY", "Turkish lira", "₺", 2, None, "1", True, True, 3),
    ("AED", "UAE dirham", "د.إ", 2, None, "1", True, True, 4),
    ("USD", "US dollar", "$", 2, None, "1", True, True, 5),
    ("EUR", "Euro", "€", 2, None, "1", True, True, 6),
]


def seed(apps, schema_editor):
    Currency = apps.get_model("currencies", "Currency")
    Source = apps.get_model("currencies", "ExchangeRateSource")
    for code, name, symbol, dp, base, factor, src, disp, order in CURRENCIES:
        Currency.objects.update_or_create(
            code=code,
            defaults={"name": name, "symbol": symbol, "decimal_places": dp, "base_currency_id": base,
                      "base_factor": Decimal(factor), "is_source": src, "is_display": disp, "sort_order": order},
        )
    Source.objects.get_or_create(
        code="manual",
        defaults={"name": "Manual rates", "provider_key": "manual", "kind": "manual",
                  "description": "Rates entered by authorised staff."},
    )


class Migration(migrations.Migration):
    dependencies = [("currencies", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
