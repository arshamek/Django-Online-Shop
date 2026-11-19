from django.db.models import Q
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.audit.models import AuditLog

from .. import serializers as s
from ..models import User
from ..permissions import HasStaffPermission, IsStaff
from ..services import staff as staff_service
from .base import NoStoreMixin

TAG = ["staff"]
STAFF_PERMS = [IsAuthenticated, IsStaff, HasStaffPermission]


class CustomerQuerysetMixin:
    def get_customer(self, public_id) -> User:
        customer = User.objects.filter(public_id=public_id, is_staff=False).first()
        if customer is None:
            raise NotFound()
        return customer


class StaffCustomerListView(NoStoreMixin, generics.ListAPIView):
    permission_classes = STAFF_PERMS
    required_permissions = {"GET": "accounts.customers_view"}
    serializer_class = s.StaffCustomerSerializer
    search_fields = []  # replaced by explicit `q` handling below (hashed/normalised identifiers)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()
        qs = User.objects.filter(is_staff=False).order_by("-created_at", "-id")
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("q"):
            q = params["q"].strip()
            qs = qs.filter(
                Q(email__icontains=q.lower())
                | Q(phone__contains=q.replace(" ", ""))
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
            )
        return qs

    @extend_schema(tags=TAG, operation_id="staff_customers_list")
    def get(self, request, *args, **kwargs):
        """Customer accounts (needs `accounts.customers_view`). Filters: `status`, `q`."""
        return super().get(request, *args, **kwargs)


class StaffCustomerDetailView(CustomerQuerysetMixin, NoStoreMixin, APIView):
    permission_classes = STAFF_PERMS
    required_permissions = {"GET": "accounts.customers_view"}

    @extend_schema(tags=TAG, operation_id="staff_customers_retrieve", responses=s.StaffCustomerDetailSerializer)
    def get(self, request, id):
        """One customer. Viewing is audited (personal data access)."""
        customer = self.get_customer(id)
        audit.record("staff.customer.viewed", request=request._request, target=customer)
        return Response(s.StaffCustomerDetailSerializer(customer).data)


class _CustomerAction(CustomerQuerysetMixin, NoStoreMixin, APIView):
    permission_classes = STAFF_PERMS


class StaffCustomerSuspendView(_CustomerAction):
    required_permissions = {"POST": "accounts.customers_suspend"}

    @extend_schema(
        tags=TAG,
        operation_id="staff_customers_suspend",
        request=s.SuspendSerializer,
        responses=s.StaffCustomerDetailSerializer,
    )
    def post(self, request, id):
        """Suspend the account and revoke all its sessions."""
        data = s.SuspendSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        customer = staff_service.suspend(
            request._request, request.user, self.get_customer(id), data.validated_data["reason"]
        )
        return Response(s.StaffCustomerDetailSerializer(customer).data)


class StaffCustomerReactivateView(_CustomerAction):
    required_permissions = {"POST": "accounts.customers_suspend"}

    @extend_schema(
        tags=TAG, operation_id="staff_customers_reactivate", request=None, responses=s.StaffCustomerDetailSerializer
    )
    def post(self, request, id):
        customer = staff_service.reactivate(request._request, request.user, self.get_customer(id))
        return Response(s.StaffCustomerDetailSerializer(customer).data)


class StaffCustomerUnlockView(_CustomerAction):
    required_permissions = {"POST": "accounts.customers_unlock"}

    @extend_schema(
        tags=TAG, operation_id="staff_customers_unlock", request=None, responses=s.StaffCustomerDetailSerializer
    )
    def post(self, request, id):
        """Clear the failed-login lockout."""
        customer = staff_service.unlock(request._request, request.user, self.get_customer(id))
        return Response(s.StaffCustomerDetailSerializer(customer).data)


class StaffCustomerRevokeSessionsView(_CustomerAction):
    required_permissions = {"POST": "accounts.customers_suspend"}

    @extend_schema(
        tags=TAG,
        operation_id="staff_customers_revoke_sessions",
        request=None,
        responses={204: OpenApiResponse(description="All sessions revoked.")},
    )
    def post(self, request, id):
        staff_service.revoke_sessions(request._request, request.user, self.get_customer(id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class StaffAuditLogListView(NoStoreMixin, generics.ListAPIView):
    permission_classes = STAFF_PERMS
    required_permissions = {"GET": "accounts.auditlog_view"}
    serializer_class = s.AuditLogSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AuditLog.objects.none()
        qs = AuditLog.objects.all()
        params = self.request.query_params
        if params.get("action"):
            qs = qs.filter(action__startswith=params["action"])
        if params.get("target_id"):
            qs = qs.filter(target_id=params["target_id"])
        return qs

    @extend_schema(tags=TAG, operation_id="staff_audit_logs_list")
    def get(self, request, *args, **kwargs):
        """Audit log (needs `accounts.auditlog_view`, Super Admin by default). Filters: `action` (prefix), `target_id`."""
        return super().get(request, *args, **kwargs)
