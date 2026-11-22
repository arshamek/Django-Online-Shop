"""Steps 10-12: translation / pricing readiness and the publication rules."""

from django.conf import settings

from apps.catalog.models import ProductImage, ProductStatus, ProductTranslation, ProductVariant, TranslationStatus
from apps.catalog.services import publishing
from apps.product_sources.models import ExternalProduct, ListingStatus, MatchStatus
from apps.product_sources.models import PipelineStatus as S

from . import ports
from .statuses import set_status

_UNREVIEWED = {TranslationStatus.STALE, TranslationStatus.MACHINE, TranslationStatus.NEEDS_REVIEW}


def translation_gaps(product) -> list[str]:
    rows = {t.language: t for t in ProductTranslation.objects.filter(product=product)}
    gaps = []
    for language in settings.IMPORTS_REQUIRED_LANGUAGES:
        row = rows.get(language)
        if row is None:
            gaps.append(f"translation_missing:{language}")
        elif row.status in _UNREVIEWED:
            gaps.append(f"translation_unreviewed:{language}")
    return gaps


def pricing_gaps(product) -> list[str]:
    return list(
        ProductVariant.objects.filter(product=product, is_active=True, price__isnull=True).values_list("sku", flat=True)
    )


def publication_blockers(ext: ExternalProduct) -> list[str]:
    blockers = []
    if ext.data_origin == "fixture" and not settings.IMPORTS_ALLOW_FIXTURE_PUBLISH:
        blockers.append("fixture_data_cannot_be_published")
    if ext.status != ListingStatus.ACTIVE:
        blockers.append("listing_not_active_at_source")
    product = ext.canonical_product
    if product is None or ext.match_status not in (MatchStatus.AUTO_MATCHED, MatchStatus.MANUALLY_MATCHED):
        blockers.append("not_matched_to_a_canonical_product")
        return blockers
    blockers += translation_gaps(product)
    blockers += [f"price_missing:{sku}" for sku in pricing_gaps(product)]
    if settings.IMPORTS_REQUIRE_IMAGE and not ProductImage.objects.published().filter(product=product).exists():
        blockers.append("image_missing")
    if product.status != ProductStatus.ACTIVE:
        blockers += [f"catalog:{b}" for b in publishing.publish_blockers(product)]
    return blockers


def refresh_readiness(ext: ExternalProduct, *, queue: bool = False, auto_publish: bool = True) -> str:
    """Move a matched listing to the right waiting state. Queues translation/pricing hand-off when asked."""
    product = ext.canonical_product
    if product is None or ext.pipeline_status in (S.PENDING_REVIEW, S.FAILED, S.ARCHIVED, S.DISCOVERED, S.PROCESSING):
        return ext.pipeline_status
    if product.status == ProductStatus.ACTIVE:
        set_status(ext, S.PUBLISHED, "product_active")
        return ext.pipeline_status
    t_gaps, p_gaps = translation_gaps(product), pricing_gaps(product)
    if t_gaps:
        set_status(ext, S.TRANSLATION_PENDING, ",".join(t_gaps))
    elif p_gaps:
        set_status(ext, S.PRICING_PENDING, "price_missing")
    else:
        set_status(ext, S.READY_TO_PUBLISH, "")
    if queue:
        if t_gaps:
            ports.queue_translation(ext)
        if p_gaps:
            ports.queue_pricing(ext)
    if (
        auto_publish
        and ext.pipeline_status == S.READY_TO_PUBLISH
        and settings.IMPORTS_AUTO_PUBLISH
        and not publication_blockers(ext)
    ):
        from .publication import publish

        publish(ext, actor=None)
    return ext.pipeline_status
