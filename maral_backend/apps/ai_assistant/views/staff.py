"""Staff API for the assistant. Needs `accounts.ai_manage` (Super Admin, Product Manager)."""

from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasStaffPermission, IsStaff
from apps.accounts.views.base import NoStoreMixin
from apps.audit import services as audit
from apps.product_sources.models import DataOrigin, ExternalProduct, ListingStatus, ProductSource

from ..constants import ALL_SOURCES, SOURCE_MARAL, ProviderStatus
from ..models import AssistantConfig, Conversation, PurchaseInquiry, UsageRecord
from ..serializers import ConfigSerializer, StaffInquirySerializer, StaffInquiryUpdateSerializer
from ..services import inquiries
from ..services.providers import anthropic as anthropic_mod
from ..services.providers.base import AIProviderError, AIRequest
from ..services.providers.factory import resolve
from ..services.providers.prompts import build_system_prompt
from ..services.sources import snapshots

PERM = "accounts.ai_manage"
TAG = ["staff-ai-assistant"]


class StaffView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    required_permissions = {"GET": PERM, "POST": PERM, "PATCH": PERM}


class ConfigView(StaffView):
    @extend_schema(tags=TAG, responses=ConfigSerializer, summary="Assistant configuration")
    def get(self, request):
        return Response(ConfigSerializer(AssistantConfig.load()).data)

    @extend_schema(
        tags=TAG, request=ConfigSerializer, responses=ConfigSerializer, summary="Update the assistant configuration"
    )
    def patch(self, request):
        config = AssistantConfig.load()
        ser = ConfigSerializer(config, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        config = ser.save(updated_by=request.user)
        audit.record(
            "ai.config_updated", request=request, actor=request.user, target=config, changed=sorted(ser.validated_data)
        )
        return Response(ConfigSerializer(config).data)


class StatusView(StaffView):
    @extend_schema(tags=TAG, summary="Provider status, source status and usage / error statistics")
    def get(self, request):
        config = AssistantConfig.load()
        choice = resolve(config)
        sources = []
        for code in ALL_SOURCES:
            enabled = code in config.enabled_sources
            if code == SOURCE_MARAL:
                sources.append(
                    {
                        "source": code,
                        "enabled": enabled,
                        "status": ProviderStatus.AVAILABLE if enabled else ProviderStatus.NOT_CONFIGURED,
                        "mode": "internal_catalog",
                        "live_search": "n/a",
                    }
                )
                continue
            src = ProductSource.objects.filter(code=code).first()
            live, detail = snapshots.live_search_status(src)
            rows = (
                ExternalProduct.objects.filter(source=src, status=ListingStatus.ACTIVE)
                if src
                else ExternalProduct.objects.none()
            )
            sources.append(
                {
                    "source": code,
                    "enabled": enabled,
                    "status": ProviderStatus.AVAILABLE
                    if (enabled and src and src.is_active)
                    else ProviderStatus.NOT_CONFIGURED,
                    "mode": "imported_snapshot",
                    "live_search": "disabled",
                    "live_search_status": live,
                    "live_search_detail": detail,
                    "stored_listings": rows.count(),
                    "stored_fixture_listings": rows.filter(data_origin=DataOrigin.FIXTURE).count(),
                    "live_verified": False,
                }
            )
        now = timezone.now()

        def window(hours):
            qs = UsageRecord.objects.filter(created_at__gte=now - timedelta(hours=hours))
            by_status = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}
            agg = qs.aggregate(i=Sum("input_tokens"), o=Sum("output_tokens"))
            return {
                "requests": qs.count(),
                "by_status": by_status,
                "input_tokens": agg["i"] or 0,
                "output_tokens": agg["o"] or 0,
                "flagged_prompt_injection": qs.filter(flagged_content=True).count(),
                "errors_by_code": {
                    r["error_code"]: r["n"]
                    for r in qs.exclude(error_code="").values("error_code").annotate(n=Count("id"))
                },
            }

        return Response(
            {
                "enabled": config.enabled,
                "provider": {
                    **choice.public(),
                    "last_test_at": config.last_provider_test_at,
                    "last_test_ok": config.last_provider_test_ok,
                    "last_test_error": config.last_provider_test_error or None,
                    "live_verified_in_repository": anthropic_mod.LIVE_VERIFIED,
                },
                "sources": sources,
                "usage": {"last_24h": window(24), "last_7d": window(24 * 7)},
                "stored_conversations": Conversation.objects.count(),
                "open_purchase_inquiries": PurchaseInquiry.objects.filter(status__in=inquiries.OPEN).count(),
            }
        )


class ProviderTestView(StaffView):
    @extend_schema(tags=TAG, summary="Make one real call to the configured AI provider and record the result")
    def post(self, request):
        config = AssistantConfig.load()
        choice = resolve(config)
        if choice.demo_mode:
            return Response(
                {
                    "ok": False,
                    "mode": "demo",
                    "detail": "No real AI provider is configured (demo mode). Nothing was tested.",
                    "provider": choice.public(),
                }
            )
        facts = {"language": "en", "intent": "provider_test", "products": [], "source_status": []}
        error = ""
        try:
            reply = choice.provider.generate(
                AIRequest(
                    language="en",
                    system_prompt=build_system_prompt("en"),
                    customer_message="Say hello in one short sentence.",
                    facts=facts,
                    timeout=config.provider_timeout_seconds,
                )
            )
            ok = bool(reply.text.strip())
        except AIProviderError as exc:
            ok, error = False, exc.code
        config.last_provider_test_at, config.last_provider_test_ok, config.last_provider_test_error = (
            timezone.now(),
            ok,
            error,
        )
        config.save(update_fields=["last_provider_test_at", "last_provider_test_ok", "last_provider_test_error"])
        audit.record("ai.provider_tested", request=request, actor=request.user, target=config, ok=ok, error=error)
        return Response({"ok": ok, "mode": "live", "error_code": error or None, "provider": resolve(config).public()})


class InquiryListView(StaffView):
    @extend_schema(tags=TAG, summary="Purchase inquiries (filter ?status=)")
    def get(self, request):
        qs = PurchaseInquiry.objects.select_related("source", "user")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response({"results": StaffInquirySerializer(qs[:200], many=True).data})


class InquiryDetailView(StaffView):
    def _get(self, public_id):
        obj = PurchaseInquiry.objects.select_related("source", "user").filter(public_id=public_id).first()
        if obj is None:
            raise NotFound()
        return obj

    @extend_schema(tags=TAG, responses=StaffInquirySerializer)
    def get(self, request, public_id):
        return Response(StaffInquirySerializer(self._get(public_id)).data)

    @extend_schema(
        tags=TAG,
        request=StaffInquiryUpdateSerializer,
        responses=StaffInquirySerializer,
        summary="Re-check and progress an inquiry",
    )
    def patch(self, request, public_id):
        ser = StaffInquiryUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        inquiry = inquiries.staff_update(request, request.user, self._get(public_id), **ser.validated_data)
        return Response(StaffInquirySerializer(inquiry).data)
