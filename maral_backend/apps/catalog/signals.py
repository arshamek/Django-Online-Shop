"""
Keeps the listing projection and the response cache in step with writes. Everything runs on commit.
Bulk writers (imports, fixtures) that bypass signals must call services.projection.refresh_many().
"""

import threading
from contextlib import contextmanager

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from apps.brands.models import Brand, BrandAlias, BrandTranslation
from apps.categories.models import Category, CategoryTranslation
from apps.product_sources.models import ProductSource
from apps.reviews.models import Review

from . import cache
from .models import (
    AttributeOption,
    AttributeOptionTranslation,
    DeliveryEstimateRule,
    Product,
    ProductAttribute,
    ProductAttributeTranslation,
    ProductAttributeValue,
    ProductImage,
    ProductTranslation,
    ProductVariant,
    ProductVariantOption,
    VariantAvailability,
    VariantPrice,
)
from .services import projection
from .services.variants import sync_option_signature

_state = threading.local()


@contextmanager
def suspend_projection_refresh():
    """Bulk loaders wrap their writes in this and call services.projection.refresh_many() once at the end."""
    previous = getattr(_state, "suspended", False)
    _state.suspended = True
    try:
        yield
    finally:
        _state.suspended = previous


def _suspended() -> bool:
    return getattr(_state, "suspended", False)


PRODUCT_SCOPED = (Product, ProductVariant, VariantPrice, VariantAvailability)
CACHE_ONLY = (
    ProductTranslation,
    ProductImage,
    ProductAttributeValue,
    ProductAttribute,
    ProductAttributeTranslation,
    AttributeOption,
    AttributeOptionTranslation,
    DeliveryEstimateRule,
    BrandTranslation,
    BrandAlias,
    CategoryTranslation,
    ProductSource,
    Review,
)


def _product_id(sender, instance):
    if sender is Product:
        return instance.pk
    if sender is ProductVariant:
        return instance.product_id
    return ProductVariant.objects.filter(pk=instance.variant_id).values_list("product_id", flat=True).first()


def _refresh(product_id):
    if product_id and not _suspended():
        transaction.on_commit(lambda: (projection.refresh_projection(product_id), cache.bump_version()))


def _on_product_scoped_change(sender, instance, **kwargs):
    _refresh(_product_id(sender, instance))


def _before_variant_child_delete(sender, instance, **kwargs):
    instance._product_id_cache = _product_id(sender, instance)


def _after_variant_child_delete(sender, instance, **kwargs):
    _refresh(getattr(instance, "_product_id_cache", None))


for _model in PRODUCT_SCOPED:
    post_save.connect(_on_product_scoped_change, sender=_model, dispatch_uid=f"catalog.refresh.save.{_model.__name__}")
for _model in (ProductVariant, VariantPrice, VariantAvailability):
    pre_delete.connect(
        _before_variant_child_delete, sender=_model, dispatch_uid=f"catalog.refresh.pre.{_model.__name__}"
    )
    post_delete.connect(
        _after_variant_child_delete, sender=_model, dispatch_uid=f"catalog.refresh.del.{_model.__name__}"
    )


@receiver([post_save, post_delete], sender=ProductVariantOption, dispatch_uid="catalog.variant_option")
def _variant_option_changed(sender, instance, **kwargs):
    sync_option_signature(instance.variant_id)
    cache.bump_version()


@receiver(post_save, sender=Brand, dispatch_uid="catalog.brand.saved")
def _brand_saved(sender, instance, **kwargs):
    if _suspended():
        return
    from .tasks import refresh_brand_projections

    transaction.on_commit(lambda: (refresh_brand_projections.delay(instance.pk), cache.bump_version()))


@receiver(post_save, sender=Category, dispatch_uid="catalog.category.saved")
def _category_saved(sender, instance, **kwargs):
    if _suspended():
        return
    from .tasks import refresh_category_projections

    transaction.on_commit(lambda: (refresh_category_projections.delay(instance.pk), cache.bump_version()))


def _bump(sender, instance, **kwargs):
    transaction.on_commit(cache.bump_version)


for _model in CACHE_ONLY:
    post_save.connect(_bump, sender=_model, dispatch_uid=f"catalog.cache.save.{_model.__name__}")
    post_delete.connect(_bump, sender=_model, dispatch_uid=f"catalog.cache.del.{_model.__name__}")
