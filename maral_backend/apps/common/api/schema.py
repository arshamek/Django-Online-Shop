"""OpenAPI customisation: session-auth scheme and the shared error envelope."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension

ERROR_REF = {"$ref": "#/components/schemas/ErrorEnvelope"}

_ERROR_DESCRIPTIONS = {
    "400": "Validation or filter error (`validation_error`, `invalid_filter`, `parse_error`).",
    "401": "Not authenticated (`not_authenticated`).",
    "403": "Forbidden (`permission_denied`, `csrf_failed`).",
    "404": "Not found (`not_found`).",
    "429": "Rate limited (`rate_limited`). Honour the `Retry-After` header.",
    "500": "Unexpected server error (`server_error`). Quote `request_id` when reporting.",
}


class SessionAuthentication401Scheme(OpenApiAuthenticationExtension):
    target_class = "apps.common.api.authentication.SessionAuthentication401"
    name = "sessionAuth"
    match_subclasses = True

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "cookie", "name": "sessionid"}


def _error_response(status: str) -> dict:
    return {"description": _ERROR_DESCRIPTIONS[status], "content": {"application/json": {"schema": ERROR_REF}}}


def add_error_envelope(result, generator, request, public):
    """Postprocessing hook: declare ErrorEnvelope and attach the standard error responses."""
    components = result.setdefault("components", {}).setdefault("schemas", {})
    components["ErrorEnvelope"] = {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {
                "type": "object",
                "required": ["code", "message", "request_id"],
                "properties": {
                    "code": {"type": "string", "example": "validation_error"},
                    "message": {"type": "string"},
                    "fields": {"type": "object", "additionalProperties": True},
                    "request_id": {"type": "string", "nullable": True},
                },
            }
        },
    }
    for path, item in result.get("paths", {}).items():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            responses = operation.setdefault("responses", {})
            wanted = ["429", "500"]
            security = operation.get("security") or []
            if security and {} not in security:
                wanted += ["401", "403"]
            if method in {"post", "put", "patch"} or operation.get("parameters"):
                wanted.append("400")
            if "{" in path:
                wanted.append("404")
            for status in wanted:
                responses.setdefault(status, _error_response(status))
    return result
