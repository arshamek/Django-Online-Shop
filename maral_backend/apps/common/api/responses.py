"""Common API response builders. Success responses are plain resources (no envelope)."""

from typing import Any

from rest_framework.response import Response

from apps.common.context import get_request_id


def error_payload(
    code: str, message: str, *, fields: dict | None = None, request_id: str | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": str(message)}
    if fields:
        error["fields"] = fields
    error["request_id"] = request_id or get_request_id()
    return {"error": error}


def error_response(
    code: str, message: str, *, status: int, fields: dict | None = None, headers: dict | None = None
) -> Response:
    return Response(error_payload(code, message, fields=fields), status=status, headers=headers)
