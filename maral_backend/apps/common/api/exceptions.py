"""Global DRF exception handler producing the standard error envelope."""

import structlog
from django.utils.translation import gettext as _
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.common.exceptions import DomainError

from .responses import error_payload

logger = structlog.get_logger("maral.api")

_CODE_BY_EXCEPTION = (
    (exceptions.ValidationError, "validation_error"),
    (exceptions.ParseError, "parse_error"),
    (exceptions.NotAuthenticated, "not_authenticated"),
    (exceptions.AuthenticationFailed, "not_authenticated"),
    (exceptions.PermissionDenied, "permission_denied"),
    (exceptions.NotFound, "not_found"),
    (exceptions.MethodNotAllowed, "method_not_allowed"),
    (exceptions.NotAcceptable, "not_acceptable"),
    (exceptions.UnsupportedMediaType, "unsupported_media_type"),
    (exceptions.Throttled, "rate_limited"),
)


def _plain(detail):
    """Convert DRF ErrorDetail trees to plain strings/lists/dicts."""
    if isinstance(detail, dict):
        return {key: _plain(value) for key, value in detail.items()}
    if isinstance(detail, (list, tuple)):
        return [_plain(item) for item in detail]
    return str(detail)


def _code_for(exc: Exception, status_code: int) -> str:
    if isinstance(exc, DomainError):
        codes = exc.get_codes()
        return codes if isinstance(codes, str) else exc.default_code
    if isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        specific = exc.get_codes()
        if isinstance(specific, str) and specific in {"session_expired", "invalid_credentials"}:
            return specific
    if isinstance(exc, exceptions.PermissionDenied):
        specific = exc.get_codes()
        if isinstance(specific, str) and specific not in {"permission_denied", "error"}:
            return specific
    for exc_type, code in _CODE_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            if code == "permission_denied" and str(getattr(exc, "detail", "")).startswith("CSRF Failed"):
                return "csrf_failed"
            return code
    return "server_error" if status_code >= 500 else "bad_request"


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)

    if response is None:  # not an APIException / Http404 / PermissionDenied -> genuine bug
        view = context.get("view")
        logger.error("unhandled_exception", view=type(view).__name__ if view else None, exc_info=exc)
        return Response(
            error_payload("server_error", _("An unexpected error occurred.")),
            status=500,
        )

    fields = None
    if isinstance(exc, exceptions.ValidationError):
        message = _("Validation failed.")
        fields = _plain(exc.detail) if isinstance(exc.detail, dict) else {"non_field_errors": _plain(exc.detail)}
    elif isinstance(exc, DomainError):
        message = str(exc.detail)
        fields = getattr(exc, "fields", None)
    elif isinstance(exc.detail if hasattr(exc, "detail") else None, (dict, list)):
        message = _("The request could not be processed.")
    else:
        message = str(getattr(exc, "detail", "")) or _("The request could not be processed.")

    code = _code_for(exc, response.status_code)
    response.data = error_payload(code, message, fields=fields)
    if response.status_code >= 500:
        logger.error("api_server_error", code=code, status_code=response.status_code, exc_info=exc)
    return response
