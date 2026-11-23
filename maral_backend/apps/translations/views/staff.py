"""Translation administration API. Needs `accounts.catalog_manage`. No credential ever appears in a response."""

from django.conf import settings
from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasStaffPermission, IsStaff
from apps.accounts.views.base import NoStoreMixin
from apps.audit import services as audit
from apps.catalog.models import Product, ProductTranslation
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import BusinessRuleViolation
from apps.common.languages import SUPPORTED_LANGUAGES

from ..errors import TranslationValidationError
from ..models import EntityType, JobStatus, TranslationJob, TranslationRecord
from ..providers.fake import FakeProvider
from ..providers.llm import LLMTranslationProvider
from ..services import entities, jobs, translate

PERM = "accounts.catalog_manage"
TAG = ["staff-translations"]


class StaffView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    required_permissions = {"GET": PERM, "POST": PERM, "PUT": PERM}


def job_out(j: TranslationJob) -> dict:
    return {
        "id": j.pk,
        "entity_type": j.entity_type,
        "entity_id": j.entity_id,
        "source_language": j.source_language,
        "target_language": j.target_language,
        "status": j.status,
        "attempts": j.attempts,
        "next_attempt_at": j.next_attempt_at,
        "error_code": j.error_code,
        "error_message": j.error_message,
        "provider": j.provider,
        "model": j.model,
        "units_total": j.units_total,
        "units_from_cache": j.units_from_cache,
        "chars_sent": j.chars_sent,
        "created_at": j.created_at,
        "finished_at": j.finished_at,
    }


class JobListView(StaffView):
    class EnqueueIn(serializers.Serializer):
        product = serializers.UUIDField(required=False)
        entity_type = serializers.CharField(max_length=20, required=False)
        entity_id = serializers.IntegerField(required=False)
        languages = serializers.ListField(child=serializers.ChoiceField(choices=SUPPORTED_LANGUAGES), required=False)
        force = serializers.BooleanField(required=False, default=False)

    @extend_schema(
        tags=TAG,
        operation_id="staff_translations_jobs_list",
        summary="Translation jobs (filter: status, entity_type, language)",
        responses={200: dict},
    )
    def get(self, request):
        qs = TranslationJob.objects.all()
        p = request.query_params
        for field, lookup in (("status", "status"), ("entity_type", "entity_type"), ("language", "target_language")):
            if p.get(field):
                qs = qs.filter(**{lookup: p[field]})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response([job_out(j) for j in page])

    @extend_schema(
        tags=TAG,
        summary="Queue translations (a product, or a category / attribute / attribute value)",
        request=EnqueueIn,
        responses={202: dict},
    )
    def post(self, request):
        data = self.EnqueueIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        if v.get("product"):
            product = Product.objects.filter(public_id=v["product"]).first()
            if product is None:
                raise NotFound()
            entity_type, entity_id = EntityType.PRODUCT, product.pk
        elif v.get("entity_type") and v.get("entity_id"):
            entity_type, entity_id = v["entity_type"], v["entity_id"]
            entities.assert_translatable(entity_type)
        else:
            raise BusinessRuleViolation("Give a product, or entity_type and entity_id.", code="input_required")
        queued = jobs.enqueue(entity_type, entity_id, v.get("languages"), force=v["force"], requested_by=request.user)
        audit.record(
            "translations.queued", request=request, entity_type=entity_type, entity_id=entity_id, jobs=len(queued)
        )
        return Response({"queued": [job_out(j) for j in queued]}, status=202)


class JobDetailView(StaffView):
    @extend_schema(tags=TAG, summary="One job", responses={200: dict})
    def get(self, request, pk):
        job = TranslationJob.objects.filter(pk=pk).first()
        if job is None:
            raise NotFound()
        return Response({**job_out(job), "result": job.result})


class JobRetryView(StaffView):
    @extend_schema(tags=TAG, summary="Re-queue a failed or dead job", request=None, responses={202: dict})
    def post(self, request, pk):
        job = TranslationJob.objects.filter(pk=pk).first()
        if job is None:
            raise NotFound()
        if job.status in (JobStatus.RUNNING,):
            raise BusinessRuleViolation("The job is running.", code="job_running")
        jobs.requeue(job)
        audit.record("translations.retried", request=request, target=("translation_job", job.pk))
        job.refresh_from_db()
        return Response(job_out(job), status=202)


class SummaryView(StaffView):
    @extend_schema(tags=TAG, summary="Queue counts and provider status (never the key)", responses={200: dict})
    def get(self, request):
        counts = dict(TranslationJob.objects.values_list("status").annotate(n=Count("id")))
        ai = settings.AI_SETTINGS
        provider = settings.TRANSLATION_PROVIDER
        configured = (
            (bool(ai.get("translation_api_key") or ai.get("api_key")) and bool(ai.get("model")))
            if provider == "llm"
            else (provider == "fake" and settings.TRANSLATION_ALLOW_FAKE_PROVIDER)
        )
        return Response(
            {
                "jobs": {s: counts.get(s, 0) for s in JobStatus.values},
                "provider": {
                    "key": provider,
                    "model": ai.get("model") if provider == "llm" else FakeProvider.model,
                    "configured": configured,
                    "live_verified": LLMTranslationProvider.LIVE_VERIFIED,
                },
                "records_by_status": dict(TranslationRecord.objects.values_list("status").annotate(n=Count("id"))),
            }
        )


def _product(public_id) -> Product:
    product = Product.objects.filter(public_id=public_id).first()
    if product is None:
        raise NotFound()
    return product


class ProductStatusView(StaffView):
    @extend_schema(tags=TAG, summary="Translation status of a product, per language", responses={200: dict})
    def get(self, request, id):
        product = _product(id)
        adapter = entities.ADAPTERS[EntityType.PRODUCT]
        loaded = adapter.load(product.pk)
        out = {}
        for language in SUPPORTED_LANGUAGES:
            if loaded and language == loaded.source_language:
                out[language] = {"role": "source"}
                continue
            states = adapter.states(product.pk, language)
            row = ProductTranslation.objects.filter(product=product, language=language).first()
            out[language] = {
                "role": "translation",
                "groups": {
                    g: (None if s is None else {"origin": s.origin, "status": s.status}) for g, s in states.items()
                },
                "provider": row.provider if row else "",
                "model": row.model if row else "",
                "job": next(
                    (
                        {"status": j.status, "error_code": j.error_code}
                        for j in TranslationJob.objects.filter(
                            entity_type="product", entity_id=product.pk, target_language=language
                        )[:1]
                    ),
                    None,
                ),
            }
        return Response(
            {
                "product_id": str(product.public_id),
                "source_language": loaded.source_language if loaded else None,
                "languages": out,
            }
        )


class CorrectionView(StaffView):
    class CorrectionIn(serializers.Serializer):
        group = serializers.CharField(max_length=20, default="row")
        fields = serializers.DictField(child=serializers.CharField(allow_blank=True, max_length=20000))

    @extend_schema(
        tags=TAG,
        summary="Save a manual correction (always stored as human text)",
        request=CorrectionIn,
        responses={200: dict},
    )
    def put(self, request, id, language):
        if language not in SUPPORTED_LANGUAGES:
            raise NotFound()
        product = _product(id)
        data = self.CorrectionIn(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            translate.save_manual_correction(
                "product",
                product.pk,
                language,
                data.validated_data["group"],
                data.validated_data["fields"],
                user=request.user,
            )
        except TranslationValidationError as exc:
            raise BusinessRuleViolation(exc.message, code=exc.code) from None
        audit.record(
            "translations.corrected",
            request=request,
            target=product,
            language=language,
            group=data.validated_data["group"],
        )
        return Response({"saved": True})


class ApproveView(StaffView):
    class ApproveIn(serializers.Serializer):
        group = serializers.CharField(max_length=20, default="row")

    @extend_schema(
        tags=TAG, summary="Approve a machine translation after review", request=ApproveIn, responses={200: dict}
    )
    def post(self, request, id, language):
        product = _product(id)
        data = self.ApproveIn(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            translate.approve("product", product.pk, language, data.validated_data["group"], user=request.user)
        except TranslationValidationError as exc:
            raise BusinessRuleViolation(exc.message, code=exc.code) from None
        audit.record("translations.approved", request=request, target=product, language=language)
        return Response({"approved": True})
