"""
Domain-level API exceptions. Raise these from services; the global handler turns them
into the standard error envelope (see docs/api/API_CONVENTIONS.md section 7).
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException


class DomainError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = _("The request could not be processed.")
    default_code = "error"

    def __init__(self, detail=None, code=None, fields: dict | None = None):
        super().__init__(detail=detail, code=code)
        self.fields = fields


class InvalidFilter(DomainError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = _("Invalid filter.")
    default_code = "invalid_filter"


class Conflict(DomainError):
    """409 - e.g. out_of_stock, price_changed, idempotency_conflict (pass code=...)."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = _("The request conflicts with the current state of the resource.")
    default_code = "conflict"


class BusinessRuleViolation(DomainError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = _("The request violates a business rule.")
    default_code = "business_rule_violation"


class UpstreamUnavailable(DomainError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = _("An upstream service is unavailable.")
    default_code = "upstream_unavailable"


class TransientError(Exception):
    """Raise inside Celery tasks to request an automatic retry with backoff."""
