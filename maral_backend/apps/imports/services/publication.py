"""Step 12: only products that satisfy validation and business rules reach the storefront."""

from django.db import transaction

from apps.catalog.models import ProductStatus
from apps.catalog.services import publishing
from apps.common.exceptions import BusinessRuleViolation
from apps.product_sources.models import ExternalProduct
from apps.product_sources.models import PipelineStatus as S

from .readiness import publication_blockers, refresh_readiness
from .statuses import set_status


@transaction.atomic
def publish(ext: ExternalProduct, actor=None) -> ExternalProduct:
    refresh_readiness(ext, queue=False, auto_publish=False)
    if ext.pipeline_status == S.PUBLISHED:
        return ext
    if ext.pipeline_status != S.READY_TO_PUBLISH:
        raise BusinessRuleViolation(
            f"The product is {ext.pipeline_status}, not READY_TO_PUBLISH.", code="not_ready_to_publish"
        )
    blockers = publication_blockers(ext)
    if blockers:
        raise BusinessRuleViolation(
            "The product cannot be published yet.", code="not_publishable", fields={"blockers": blockers}
        )
    product = ext.canonical_product
    publishing.change_status(product, ProductStatus.ACTIVE)
    for listing in product.external_listings.filter(pipeline_status=S.READY_TO_PUBLISH):
        set_status(listing, S.PUBLISHED, "published")
    ext.refresh_from_db()
    return ext
