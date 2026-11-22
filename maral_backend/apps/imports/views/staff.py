"""
Import dashboard API (staff only). Every endpoint needs an authenticated staff user holding the named
permission: `accounts.imports_manage` (run / watch / preview / publish) or `accounts.imports_review_matches`
(approve / reject match decisions). State-changing calls are audited.
"""

from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework import status as http
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasStaffPermission, IsStaff
from apps.accounts.views.base import NoStoreMixin
from apps.audit import services as audit
from apps.brands.models import Brand
from apps.catalog.models import AttributeOption, Product
from apps.categories.models import Category
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import DomainError
from apps.product_sources.models import (
    ExternalProduct,
    MatchStatus,
    PipelineStatus,
    ProductSource,
)
from apps.product_sources.providers.authorization import authorization_problems
from apps.product_sources.providers.registry import get_provider

from ..models import ImportJob, ImportJobItem, ImportLog, ItemStatus, JobStatus, JobType, LogLevel
from ..services import jobs, pipeline, preview
from ..services.publication import publish

MANAGE = "accounts.imports_manage"
REVIEW = "accounts.imports_review_matches"
TAG = ["staff-imports"]


class ImportsView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    required_permissions = {"GET": MANAGE, "POST": MANAGE}


class ReviewView(ImportsView):
    required_permissions = {"POST": REVIEW}


def _job(public_id) -> ImportJob:
    job = ImportJob.objects.select_related("source", "requested_by").filter(public_id=public_id).first()
    if job is None:
        raise NotFound()
    return job


def _listing(public_id) -> ExternalProduct:
    ext = ExternalProduct.objects.select_related("source", "canonical_product").filter(public_id=public_id).first()
    if ext is None:
        raise NotFound()
    return ext


def job_out(job: ImportJob) -> dict:
    return {
        "id": str(job.public_id),
        "source": job.source.code,
        "job_type": job.job_type,
        "trigger": job.trigger,
        "status": job.status,
        "provider_mode": job.provider_mode,
        "params": job.params,
        "progress": {
            "percent": job.progress_percent,
            "processed": job.n_processed,
            "total": job.n_total,
            "seen": job.n_seen,
            "created": job.n_created,
            "updated": job.n_updated,
            "unchanged": job.n_unchanged,
            "skipped": job.n_skipped,
            "failed": job.n_failed,
        },
        "cancel_requested": job.cancel_requested,
        "attempt": job.attempt,
        "retry_count": job.retry_count,
        "resume_at": job.resume_at,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
        "summary": job.summary,
        "requested_by": str(job.requested_by.public_id)
        if job.requested_by and hasattr(job.requested_by, "public_id")
        else None,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _listing_row(ext: ExternalProduct) -> dict:
    return {
        "id": str(ext.public_id),
        "source": ext.source.code,
        "external_id": ext.external_id,
        "title": ext.raw_title,
        "brand": ext.raw_brand,
        "pipeline_status": ext.pipeline_status,
        "pipeline_reason": ext.pipeline_reason,
        "match_status": ext.match_status,
        "listing_status": ext.status,
        "data_origin": ext.data_origin,
        "product": str(ext.canonical_product.public_id) if ext.canonical_product else None,
        "last_seen_at": ext.last_seen_at,
    }


def _paginate(view, request, qs, row):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(qs, request, view=view)
    return paginator.get_paginated_response([row(x) for x in page])


class StartSerializer(serializers.Serializer):
    source = serializers.SlugRelatedField(slug_field="code", queryset=ProductSource.objects.filter(is_active=True))
    job_type = serializers.ChoiceField(choices=JobType.choices)
    external_ids = serializers.ListField(child=serializers.CharField(max_length=120), required=False, allow_empty=False)
    category = serializers.CharField(max_length=200, required=False)
    since = serializers.DateTimeField(required=False)
    repair_of = serializers.IntegerField(required=False)
    idempotency_key = serializers.CharField(max_length=100, required=False, allow_blank=True)


class SourcesView(ImportsView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_sources")
    def get(self, request):
        rows = []
        for source in ProductSource.objects.order_by("sort_order", "id"):
            try:
                provider = get_provider(source)
            except DomainError:
                continue
            except Exception:  # no provider registered for this source (e.g. Maral's own stock)
                continue
            caps = provider.capabilities
            problems = authorization_problems(source)
            rows.append(
                {
                    "code": source.code,
                    "name": source.name,
                    "provider_mode": provider.mode,
                    "live_verified": provider.LIVE_VERIFIED,
                    "live_authorised": not problems,
                    "live_blockers": problems,
                    "catalog_scope": caps.catalog_scope,
                    "capabilities": {
                        "category_import": caps.category_filter,
                        "incremental": caps.incremental,
                        "trusted_gtin": caps.gtin_trusted,
                    },
                    "rate_limit_per_min": source.rate_limit_per_min,
                }
            )
        return Response({"results": rows})


class JobListCreateView(ImportsView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_jobs_list")
    def get(self, request):
        qs = ImportJob.objects.select_related("source", "requested_by")
        for param, field in (("status", "status"), ("source", "source__code"), ("job_type", "job_type")):
            value = request.query_params.get(param)
            if value:
                qs = qs.filter(**{field: value})
        return _paginate(self, request, qs, job_out)

    @extend_schema(request=StartSerializer, responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_start")
    def post(self, request):
        ser = StartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        key = data.get("idempotency_key") or request.headers.get("Idempotency-Key", "")
        params = {
            k: (v.isoformat() if k == "since" else v)
            for k, v in data.items()
            if k in ("external_ids", "category", "since", "repair_of")
        }
        job, created = jobs.create_job(
            source=data["source"],
            job_type=data["job_type"],
            requested_by=request.user,
            params=params,
            idempotency_key=f"api:{key}"[:120] if key else "",
        )
        if created:
            audit.record(
                "imports.job.started",
                request=request,
                actor=request.user,
                target=("importjob", job.public_id),
                source=job.source.code,
                job_type=job.job_type,
            )
        return Response(job_out(job), status=http.HTTP_201_CREATED if created else http.HTTP_200_OK)


class JobDetailView(ImportsView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_job")
    def get(self, request, id):
        return Response(job_out(_job(id)))


class JobCancelView(ImportsView):
    required_permissions = {"POST": MANAGE}

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_cancel")
    def post(self, request, id):
        job = jobs.request_cancel(_job(id), request.user)
        audit.record(
            "imports.job.cancel_requested", request=request, actor=request.user, target=("importjob", job.public_id)
        )
        return Response(job_out(job), status=http.HTTP_202_ACCEPTED)


class JobRetryView(ImportsView):
    @extend_schema(request=None, responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_retry")
    def post(self, request, id):
        job = jobs.retry_job(_job(id), request.user)
        audit.record(
            "imports.job.retried",
            request=request,
            actor=request.user,
            target=("importjob", job.public_id),
            retry_count=job.retry_count,
        )
        return Response(job_out(job), status=http.HTTP_202_ACCEPTED)


class JobLogsView(ImportsView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_logs")
    def get(self, request, id):
        job = _job(id)
        qs = ImportLog.objects.filter(job=job).select_related("item")
        level = request.query_params.get("level")
        if level:
            if level not in LogLevel.values:
                raise DomainError("Unknown level.", code="invalid_filter", fields={"level": ["Unknown level."]})
            qs = qs.filter(level=level)
        after = request.query_params.get("after_id")
        if after and after.isdigit():
            qs = qs.filter(pk__gt=int(after))  # tail the log by passing the last id seen
        return _paginate(
            self,
            request,
            qs,
            lambda x: {
                "id": x.pk,
                "level": x.level,
                "message": x.message,
                "context": x.context,
                "external_id": x.item.external_id if x.item else None,
                "created_at": x.created_at,
            },
        )


class JobItemsView(ImportsView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_items")
    def get(self, request, id):
        qs = ImportJobItem.objects.filter(job=_job(id)).order_by("id")
        status = request.query_params.get("status")
        if status:
            if status not in ItemStatus.values:
                raise DomainError("Unknown status.", code="invalid_filter", fields={"status": ["Unknown status."]})
            qs = qs.filter(status=status)
        return _paginate(
            self,
            request,
            qs,
            lambda x: {
                "external_id": x.external_id,
                "action": x.action,
                "status": x.status,
                "error_code": x.error_code,
                "error_message": x.error_message,
                "attempts": x.attempts,
                "processed_at": x.processed_at,
            },
        )


class ProductListView(ImportsView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_products")
    def get(self, request):
        qs = ExternalProduct.objects.select_related("source", "canonical_product").order_by("-updated_at", "-id")
        for param, field, allowed in (
            ("pipeline_status", "pipeline_status", PipelineStatus.values),
            ("match_status", "match_status", MatchStatus.values),
        ):
            value = request.query_params.get(param)
            if value:
                if value not in allowed:
                    raise DomainError(f"Unknown {param}.", code="invalid_filter", fields={param: ["Unknown value."]})
                qs = qs.filter(**{field: value})
        if request.query_params.get("source"):
            qs = qs.filter(source__code=request.query_params["source"])
        q = request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(Q(external_id__iexact=q) | Q(raw_title__icontains=q) | Q(raw_brand__icontains=q))
        return _paginate(self, request, qs, _listing_row)


class ProductDetailView(ImportsView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_product_preview")
    def get(self, request, id):
        return Response(preview.build_preview(_listing(id)))


class OptionMappingSerializer(serializers.Serializer):
    attribute = serializers.ChoiceField(choices=["size", "color"])
    raw = serializers.CharField(max_length=80)
    option = serializers.CharField(max_length=40)


class ApproveSerializer(serializers.Serializer):
    product = serializers.UUIDField(required=False)
    create_new = serializers.BooleanField(required=False, default=False)
    brand = serializers.SlugRelatedField(slug_field="slug", queryset=Brand.objects.all(), required=False)
    category = serializers.SlugRelatedField(slug_field="slug", queryset=Category.objects.all(), required=False)
    option_mappings = OptionMappingSerializer(many=True, required=False)


class ProductApproveView(ReviewView):
    @extend_schema(
        request=ApproveSerializer, responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_approve"
    )
    def post(self, request, id):
        ext = _listing(id)
        ser = ApproveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        mappings = []
        for m in data.get("option_mappings", []):
            option = AttributeOption.objects.filter(attribute__code=m["attribute"], code=m["option"].lower()).first()
            if option is None:
                raise DomainError(
                    "Unknown option.",
                    code="validation_error",
                    fields={"option_mappings": [f"No {m['attribute']} option '{m['option']}'."]},
                )
            mappings.append({"attribute": m["attribute"], "raw": m["raw"], "option": option})
        product = None
        if data.get("product"):
            product = Product.objects.filter(public_id=data["product"]).first()
            if product is None:
                raise DomainError("Unknown product.", code="validation_error", fields={"product": ["Unknown product."]})
        ext = pipeline.approve(
            ext,
            request.user,
            product=product,
            create_new=data["create_new"],
            brand=data.get("brand"),
            category=data.get("category"),
            option_mappings=mappings,
        )
        audit.record(
            "imports.product.approved",
            request=request,
            actor=request.user,
            target=("externalproduct", ext.public_id),
            pipeline_status=ext.pipeline_status,
        )
        return Response(preview.build_preview(ext))


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")


class ProductRejectView(ReviewView):
    @extend_schema(
        request=RejectSerializer, responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_reject"
    )
    def post(self, request, id):
        ser = RejectSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ext = pipeline.reject(_listing(id), request.user, ser.validated_data["reason"])
        audit.record(
            "imports.product.rejected", request=request, actor=request.user, target=("externalproduct", ext.public_id)
        )
        return Response(preview.build_preview(ext))


class ProductPublishView(ImportsView):
    required_permissions = {"POST": MANAGE}

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_import_publish")
    def post(self, request, id):
        ext = publish(_listing(id), request.user)
        audit.record(
            "imports.product.published",
            request=request,
            actor=request.user,
            target=("externalproduct", ext.public_id),
            product=str(ext.canonical_product.public_id),
        )
        return Response(preview.build_preview(ext))


__all__ = ["JobStatus"]
