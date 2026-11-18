"""Correlation ids and request logging."""

import re
import time
import uuid

import structlog

from .context import request_id_var

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
_QUIET_PATHS = {"/health/", "/ready/"}
logger = structlog.get_logger("maral.request")


class RequestIDMiddleware:
    """
    Accept a caller-supplied `X-Request-ID` (only if it looks sane - it ends up in logs),
    otherwise generate one. The id is exposed on `request.request_id`, in a contextvar read
    by logging/error handling/Celery, and echoed in the response header.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response["X-Request-ID"] = request_id
        return response


class RequestLoggingMiddleware:
    """One structured access-log event per request. Logs path only: no query string, headers or body."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        user = getattr(request, "user", None)
        user_id = user.pk if user is not None and getattr(user, "is_authenticated", False) else None
        status_code = response.status_code
        if request.path in _QUIET_PATHS and status_code < 400:
            log = logger.debug
        elif status_code >= 500:
            log = logger.error
        elif status_code >= 400:
            log = logger.warning
        else:
            log = logger.info
        log(
            "request",
            method=request.method,
            path=request.path,
            status_code=status_code,
            duration_ms=duration_ms,
            user_id=user_id,
        )
        return response
