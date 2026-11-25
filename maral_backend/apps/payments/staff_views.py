"""Staff payment administration: provider configuration, failed-payment review, verification, reconciliation."""

from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasStaffPermission, IsStaff
from apps.accounts.views.base import NoStoreMixin
from apps.audit import services as audit
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import BusinessRuleViolation

from . import reconciliation, services
from .models import EligibilityStatus, Payment, PaymentProviderConfig, PaymentStatus, ProviderMode, ReconciliationRun
from .presenters import payment_out, refund_out, transaction_out
from .providers import registry

TAG = ["staff"]


class StaffPaymentBase(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    required_permissions = {"GET": "accounts.payments_view", "POST": "accounts.payments_manage"}


def _payment(public_id) -> Payment:
    payment = Payment.objects.select_related("checkout", "user").filter(public_id=public_id).first()
    if payment is None:
        raise NotFound()
    return payment


# ------------------------------------------------------------------------------------------------ provider configuration
class PaymentProviderConfigSerializer(serializers.ModelSerializer):
    adapter_registered = serializers.SerializerMethodField()
    adapter_currencies = serializers.SerializerMethodField()
    is_mock = serializers.SerializerMethodField()

    class Meta:
        model = PaymentProviderConfig
        fields = [
            "key", "names", "is_enabled", "mode", "eligibility_status", "eligibility_notes", "merchant_country", "supported_currencies",
            "default_charge_currency", "customer_countries", "settlement_currency", "settlement_notes", "legal_notes", "min_amount_irr",
            "max_amount_irr", "sandbox_verified_at", "sort_order", "updated_at", "adapter_registered", "adapter_currencies", "is_mock",
        ]  # fmt: skip
        read_only_fields = ["updated_at"]
        # No credential field exists on purpose: secrets live in the environment (PAYMENT_PROVIDER_CREDENTIALS).

    def _adapter(self, key):
        return registry.adapters().get(key)

    @extend_schema_field(serializers.BooleanField())
    def get_adapter_registered(self, obj):
        return self._adapter(obj.key) is not None

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_adapter_currencies(self, obj):
        adapter = self._adapter(obj.key)
        return list(adapter.supported_currencies()) if adapter else []

    @extend_schema_field(serializers.BooleanField())
    def get_is_mock(self, obj):
        adapter = self._adapter(obj.key)
        return bool(adapter and adapter.capabilities.is_mock)

    def validate(self, attrs):
        current = (
            {f: getattr(self.instance, f) for f in self.Meta.fields if hasattr(self.instance, f)}
            if self.instance
            else {}
        )
        merged = {**current, **attrs}
        key = merged.get("key") or (self.instance.key if self.instance else "")
        adapter = self._adapter(key)
        if (
            merged.get("eligibility_status") == EligibilityStatus.VERIFIED
            and not str(merged.get("eligibility_notes", "")).strip()
        ):
            raise serializers.ValidationError(
                {"eligibility_notes": "Record who verified eligibility and on what evidence."}
            )
        if adapter is not None:
            allowed = set(adapter.supported_currencies())
            extra = [c for c in merged.get("supported_currencies") or [] if c not in allowed]
            if extra:
                raise serializers.ValidationError({"supported_currencies": f"The adapter does not support: {extra}"})
        if merged.get("is_enabled"):
            if adapter is None:
                raise serializers.ValidationError(
                    {"is_enabled": "No adapter with this key is installed; it cannot be enabled."}
                )
            if not adapter.capabilities.is_mock:
                if merged.get("eligibility_status") != EligibilityStatus.VERIFIED:
                    raise serializers.ValidationError(
                        {"is_enabled": "Merchant eligibility must be verified before a real provider can be enabled."}
                    )
                if merged.get("mode") == ProviderMode.LIVE and not merged.get("sandbox_verified_at"):
                    raise serializers.ValidationError({"mode": "Live mode requires a recorded sandbox verification."})
        return attrs


class PaymentProviderConfigViewSet(NoStoreMixin, viewsets.ModelViewSet):
    queryset = PaymentProviderConfig.objects.all()
    serializer_class = PaymentProviderConfigSerializer
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    lookup_field = "key"
    lookup_value_regex = "[-a-z0-9_]+"
    http_method_names = ["get", "post", "patch", "head", "options"]  # a provider is disabled, never deleted (history)
    required_permissions = {
        "GET": "accounts.payments_view",
        "POST": "accounts.payments_manage",
        "PATCH": "accounts.payments_manage",
    }

    @extend_schema(tags=TAG, summary="Payment provider configuration (no credentials are ever stored or returned)")
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        obj = serializer.save(updated_by=self.request.user)
        audit.record("payment_provider.created", request=self.request, target=obj, key=obj.key)

    def perform_update(self, serializer):
        before = {f: getattr(serializer.instance, f) for f in ("is_enabled", "mode", "eligibility_status")}
        obj = serializer.save(updated_by=self.request.user)
        audit.record("payment_provider.updated", request=self.request, target=obj, key=obj.key, before={k: str(v) for k, v in before.items()},
                     after={k: str(getattr(obj, k)) for k in before})  # fmt: skip


# ------------------------------------------------------------------------------------------------ payments
class StaffPaymentsView(StaffPaymentBase):
    @extend_schema(
        tags=TAG,
        summary="Payments; ?problem=1 = failed, expired, cancelled or flagged for review",
        responses={200: dict},
    )
    def get(self, request):
        qs = Payment.objects.select_related("checkout", "user")
        params = request.query_params
        if params.get("status") in PaymentStatus.values:
            qs = qs.filter(status=params["status"])
        if params.get("provider"):
            qs = qs.filter(provider=params["provider"])
        if params.get("problem") in ("1", "true"):
            qs = qs.filter(
                Q(status__in=(PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELLED))
                | Q(needs_review=True)
            )
        if params.get("needs_review") in ("1", "true"):
            qs = qs.filter(needs_review=True)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response([payment_out(p, staff=True) for p in page])


class StaffPaymentView(StaffPaymentBase):
    @extend_schema(
        tags=TAG, summary="Payment with its full ledger, refunds and callback receipts", responses={200: dict}
    )
    def get(self, request, payment_id):
        payment = _payment(payment_id)
        return Response(
            {
                **payment_out(payment, staff=True),
                "transactions": [transaction_out(t) for t in payment.transactions.all()],
                "refunds": [refund_out(r) for r in payment.refunds.all()],
                "callbacks": [
                    {"outcome": c.outcome, "reason": c.reason, "event_id": c.event_id, "received_at": c.received_at}
                    for c in payment.callbacks.all()[:50]
                ],
            }
        )


class StaffPaymentVerifyView(StaffPaymentBase):
    @extend_schema(
        tags=TAG,
        summary="Verify the payment with the provider server-side and apply the verified result",
        request=None,
        responses={200: dict},
    )
    def post(self, request, payment_id):
        payment = _payment(payment_id)
        result = services.verify_payment(payment, actor=request.user, apply=True)
        payment.refresh_from_db()
        return Response({**result, "payment": payment_out(payment, staff=True)})


class ResolveIn(serializers.Serializer):
    note = serializers.CharField(max_length=300)


class StaffPaymentResolveView(StaffPaymentBase):
    @extend_schema(
        tags=TAG,
        summary="Clear a review flag after finance looked at it (note required)",
        request=ResolveIn,
        responses={200: dict},
    )
    def post(self, request, payment_id):
        data = ResolveIn(data=request.data)
        data.is_valid(raise_exception=True)
        payment = _payment(payment_id)
        if not payment.needs_review:
            raise BusinessRuleViolation("This payment is not flagged.", code="not_flagged")
        Payment.objects.filter(pk=payment.pk).update(needs_review=False, review_reason="")
        audit.record(
            "payment.review_resolved",
            request=request,
            target=payment,
            note=data.validated_data["note"],
            reason_was=payment.review_reason,
        )
        payment.refresh_from_db()
        return Response(payment_out(payment, staff=True))


class StaffReconciliationView(StaffPaymentBase):
    @extend_schema(tags=TAG, summary="Reconciliation runs (latest first)", responses={200: dict})
    def get(self, request):
        runs = ReconciliationRun.objects.all()[:50]
        return Response([_run_out(r) for r in runs])

    @extend_schema(
        tags=TAG, summary="Run a reconciliation now (optionally for one provider)", request=None, responses={201: dict}
    )
    def post(self, request):
        provider = str(request.data.get("provider", "")) if hasattr(request.data, "get") else ""
        run = reconciliation.reconcile(provider=provider, actor=request.user)
        audit.record(
            "payment.reconciled",
            request=request,
            target=("reconciliation", run.pk),
            scanned=run.scanned,
            applied=run.applied,
        )
        return Response(_run_out(run), status=201)


def _run_out(r: ReconciliationRun) -> dict:
    return {
        "id": r.pk,
        "provider": r.provider,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "scanned": r.scanned,
        "applied": r.applied,
        "errors": r.errors,
        "discrepancies": r.discrepancies,
    }
