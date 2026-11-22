"""
Hand-off to the translation and pricing phases. Those phases do not exist yet, so the ports call a handler
configured in settings (IMPORTS_TRANSLATION_HANDLER / IMPORTS_PRICING_HANDLER). With no handler the request
is recorded and reported as not queued: nothing is translated or priced behind the scenes, and the product
stays TRANSLATION_PENDING / PRICING_PENDING until staff (or the future handler) supplies the data.
"""

from django.conf import settings
from django.db import transaction
from django.utils.module_loading import import_string

from apps.catalog.models import ProductVariant
from apps.product_sources.models import ExternalProduct


def queue_translation(ext: ExternalProduct) -> None:
    from .. import tasks

    transaction.on_commit(lambda: tasks.request_translation.apply_async(args=[ext.pk], queue="translations"))


def queue_pricing(ext: ExternalProduct) -> None:
    from .. import tasks

    transaction.on_commit(lambda: tasks.request_pricing.apply_async(args=[ext.pk], queue="pricing"))


def dispatch_translation(ext: ExternalProduct) -> dict:
    path = getattr(settings, "IMPORTS_TRANSLATION_HANDLER", None)
    product = ext.canonical_product
    if not path or product is None:
        return {"queued": False, "reason": "no_translation_handler" if not path else "no_canonical_product"}
    targets = [lang for lang in settings.IMPORTS_TRANSLATION_TARGETS if lang != product.source_language]
    import_string(path)(product_id=product.pk, targets=targets)
    return {"queued": True, "targets": targets}


def dispatch_pricing(ext: ExternalProduct) -> dict:
    path = getattr(settings, "IMPORTS_PRICING_HANDLER", None)
    product = ext.canonical_product
    if not path or product is None:
        return {"queued": False, "reason": "no_pricing_handler" if not path else "no_canonical_product"}
    ids = list(
        ProductVariant.objects.filter(product=product, is_active=True, price__isnull=True).values_list("pk", flat=True)
    )
    import_string(path)(product_id=product.pk, variant_ids=ids)
    return {"queued": True, "variants": len(ids)}
