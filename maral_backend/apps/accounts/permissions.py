from django.utils.translation import gettext_lazy as _
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission


class CsrfRequired(BasePermission):
    """
    Enforce CSRF on unsafe methods even for *anonymous* callers (DRF's SessionAuthentication only
    checks authenticated sessions). Protects login/register/reset against login-CSRF.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        check = CSRFCheck(lambda _request: None)
        check.process_request(request._request)
        reason = check.process_view(request._request, None, (), {})
        if reason:
            raise PermissionDenied(f"CSRF Failed: {reason}")
        return True


class IsStaff(BasePermission):
    message = _("Staff access required.")

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_staff)


class HasStaffPermission(BasePermission):
    """
    Django permission check driven by the view: `required_permissions = "accounts.customers_view"`
    or a per-method dict `{"GET": "accounts.customers_view", "POST": "accounts.customers_suspend"}`.
    Methods missing from the dict are denied. Superusers pass via `has_perm`.
    """

    message = _("You do not have permission to perform this action.")

    def has_permission(self, request, view):
        required = getattr(view, "required_permissions", None)
        if isinstance(required, dict):
            required = required.get(request.method)
        if not required:
            return False
        return request.user.has_perm(required)


class IsOwner(BasePermission):
    """Object-level guard (defence in depth: querysets are already filtered by owner)."""

    def has_object_permission(self, request, view, obj):
        return getattr(obj, "user_id", None) == request.user.id
