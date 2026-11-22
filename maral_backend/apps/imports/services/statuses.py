"""The product pipeline state machine (ExternalProduct.pipeline_status)."""

import structlog

from apps.product_sources.models import ExternalProduct
from apps.product_sources.models import PipelineStatus as S

logger = structlog.get_logger("maral.imports")

ALLOWED: dict[str, set[str]] = {
    S.DISCOVERED: {S.PROCESSING, S.FAILED, S.ARCHIVED},
    S.PROCESSING: {S.PENDING_REVIEW, S.IMPORTED, S.FAILED, S.ARCHIVED},
    S.PENDING_REVIEW: {S.PROCESSING, S.FAILED, S.ARCHIVED},
    S.IMPORTED: {
        S.TRANSLATION_PENDING,
        S.PRICING_PENDING,
        S.READY_TO_PUBLISH,
        S.PUBLISHED,
        S.PROCESSING,
        S.FAILED,
        S.ARCHIVED,
    },
    S.TRANSLATION_PENDING: {S.PRICING_PENDING, S.READY_TO_PUBLISH, S.PROCESSING, S.FAILED, S.ARCHIVED},
    S.PRICING_PENDING: {S.TRANSLATION_PENDING, S.READY_TO_PUBLISH, S.PROCESSING, S.FAILED, S.ARCHIVED},
    S.READY_TO_PUBLISH: {S.PUBLISHED, S.TRANSLATION_PENDING, S.PRICING_PENDING, S.PROCESSING, S.FAILED, S.ARCHIVED},
    S.PUBLISHED: {S.PROCESSING, S.ARCHIVED, S.FAILED},
    S.FAILED: {S.PROCESSING, S.ARCHIVED},
    S.ARCHIVED: {S.PROCESSING},
}


class InvalidTransition(Exception):
    pass


def set_status(
    ext: ExternalProduct, new: str, reason: str = "", *, error_code: str = "", error_message: str = ""
) -> bool:
    """Returns True when the status changed. An illegal jump is a programming error, never silently applied."""
    old = ext.pipeline_status
    if old != new and new not in ALLOWED.get(old, set()):
        raise InvalidTransition(f"{old} -> {new}")
    changed = old != new
    ext.pipeline_status = new
    ext.pipeline_reason = reason[:200]
    ext.error_code = error_code[:60]
    ext.error_message = error_message[:500]
    ext.save(update_fields=["pipeline_status", "pipeline_reason", "error_code", "error_message", "updated_at"])
    if changed:
        logger.info("import_status", ext=str(ext.public_id), old=old, new=new, reason=reason)
    return changed
