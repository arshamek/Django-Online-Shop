"""Staff catalogue management. Requires `accounts.catalog_manage` (Product Manager / Super Admin); audited."""

from django.db import transaction
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status as http
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasStaffPermission, IsStaff
from apps.accounts.views.base import NoStoreMixin
from apps.audit import services as audit
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import Conflict, DomainError
from apps.common.languages import SUPPORTED_LANGUAGES
from apps.product_sources.models import ProductSource

from .. import serializers as s
from ..models import Product, ProductImage, ProductStatus, ProductTranslation, ProductVariant, TranslationStatus
from ..services import images as image_service
from ..services import publishing
from ..services import variants as variant_service

PERM = "accounts.catalog_manage"
TAG = ["staff"]


class StaffCatalogView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    required_permissions = {m: PERM for m in ("GET", "POST", "PUT", "PATCH", "DELETE")}


def _get_product(public_id) -> Product:
    product = Product.objects.select_related("brand", "category").filter(public_id=public_id).first()
    if product is None:
        raise NotFound()
    return product


def _file_url(request, field):
    if not field:
        return None
    url = field.url
    return url if url.startswith("http") else request.build_absolute_uri(url)


def _variant_out(v: ProductVariant) -> dict:
    price = getattr(v, "price", None)
    availability = getattr(v, "availability", None)
    opts = {o.attribute.code: o.option.code for o in v.options.all()}
    return {
        "id": str(v.public_id),
        "sku": v.sku,
        "fulfillment_type": v.fulfillment_type,
        "source": v.source.code,
        "size": opts.get("size"),
        "color": opts.get("color"),
        "is_active": v.is_active,
        "price": None
        if price is None
        else {
            "original_amount": str(price.original_amount),
            "original_currency": price.original_currency,
            "list_irr": str(price.list_irr),
            "final_irr": str(price.final_irr),
            "discount_percent": str(price.discount_percent),
            "origin": price.origin,
        },
        "availability": availability.status if availability else None,
    }


def product_out(request, product: Product) -> dict:
    variants = product.variants.select_related("source", "price", "availability").prefetch_related(
        "options__attribute", "options__option"
    )
    return {
        "id": str(product.public_id),
        "slug": product.slug,
        "status": product.status,
        "brand": product.brand.slug,
        "category": product.category.slug,
        "source_language": product.source_language,
        "new_until": product.new_until,
        "manufacturer_model_code": product.manufacturer_model_code,
        "translations": {
            t.language: {
                "name": t.name,
                "short_description": t.short_description,
                "description": t.description,
                "origin": t.origin,
                "status": t.status,
            }
            for t in product.translations.all()
        },
        "variants": [_variant_out(v) for v in variants],
        "images": [
            {
                "id": str(i.public_id),
                "url": _file_url(request, i.image),
                "thumbnail_url": _file_url(request, i.thumbnail),
                "alt": i.alt,
                "sort_order": i.sort_order,
                "rights": i.rights,
                "source": i.source.code if i.source else None,
                "attribution": i.attribution,
                "is_published": i.is_published,
            }
            for i in product.images.select_related("source").order_by("sort_order", "id")
        ],
        "publish_blockers": publishing.publish_blockers(product) if product.status != ProductStatus.ACTIVE else [],
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


def _upsert_translations(product: Product, translations: dict) -> None:
    for language, data in translations.items():
        if language not in SUPPORTED_LANGUAGES:
            raise DomainError(
                "Unsupported language.",
                code="validation_error",
                fields={"translations": [f"Unsupported language '{language}'."]},
            )
        origin = data.get("origin", "human")
        ProductTranslation.objects.update_or_create(
            product=product,
            language=language,
            defaults={
                **{k: v for k, v in data.items() if k != "origin"},
                "origin": origin,
                "status": TranslationStatus.HUMAN
                if origin == "human"
                else TranslationStatus.MACHINE
                if origin == "machine"
                else TranslationStatus.APPROVED,
            },
        )


class StaffProductListCreateView(StaffCatalogView):
    parser_classes = [JSONParser]

    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_products_list")
    def get(self, request):
        qs = Product.objects.select_related("brand", "category").order_by("-created_at", "-id")
        status = request.query_params.get("status")
        if status:
            if status not in ProductStatus.values:
                raise DomainError("Unknown status.", code="invalid_filter", fields={"status": ["Unknown status."]})
            qs = qs.filter(status=status)
        q = request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(slug__icontains=q.lower())
                | Q(variants__sku__iexact=q)
                | Q(pk__in=ProductTranslation.objects.filter(name__icontains=q).values("product_id"))
            ).distinct()
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = [
            {
                "id": str(p.public_id),
                "slug": p.slug,
                "status": p.status,
                "brand": p.brand.slug,
                "category": p.category.slug,
                "updated_at": p.updated_at,
            }
            for p in page
        ]
        return paginator.get_paginated_response(rows)

    @extend_schema(
        responses=OpenApiTypes.OBJECT,
        tags=TAG,
        request=s.StaffProductWriteSerializer,
        operation_id="staff_products_create",
    )
    @transaction.atomic
    def post(self, request):
        ser = s.StaffProductWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if Product.objects.filter(slug=data["slug"]).exists():
            raise Conflict("Slug already in use.", code="slug_exists", fields={"slug": ["Already in use."]})
        product = Product(
            slug=data["slug"],
            brand=data["brand"],
            category=data["category"],
            source_language=data.get("source_language", "fa"),
            new_until=data.get("new_until"),
            manufacturer_model_code=data.get("manufacturer_model_code", ""),
        )
        from django.core.exceptions import ValidationError

        try:
            product.save()
        except ValidationError as exc:
            raise DomainError("Invalid product.", code="validation_error", fields=exc.message_dict) from exc
        _upsert_translations(product, data.get("translations", {}))
        audit.record("catalog.product.created", request=request, actor=request.user, target=product, slug=product.slug)
        return Response(product_out(request, product), status=http.HTTP_201_CREATED)


class StaffProductDetailView(StaffCatalogView):
    parser_classes = [JSONParser]

    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_product_detail")
    def get(self, request, id):
        return Response(product_out(request, _get_product(id)))

    @extend_schema(
        responses=OpenApiTypes.OBJECT,
        tags=TAG,
        request=s.StaffProductWriteSerializer,
        operation_id="staff_product_update",
    )
    @transaction.atomic
    def patch(self, request, id):
        product = _get_product(id)
        ser = s.StaffProductWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        for field in ("brand", "category", "source_language", "new_until", "manufacturer_model_code"):
            if field in data:
                setattr(product, field, data[field])
        if "slug" in data and data["slug"] != product.slug:
            if Product.objects.filter(slug=data["slug"]).exclude(pk=product.pk).exists():
                raise Conflict("Slug already in use.", code="slug_exists", fields={"slug": ["Already in use."]})
            product.slug = data["slug"]
        product.save()
        _upsert_translations(product, data.get("translations", {}))
        audit.record(
            "catalog.product.updated", request=request, actor=request.user, target=product, fields=sorted(data)
        )
        return Response(product_out(request, product))


class StaffProductStatusView(StaffCatalogView):
    parser_classes = [JSONParser]

    @extend_schema(
        responses=OpenApiTypes.OBJECT, tags=TAG, request=s.StaffStatusSerializer, operation_id="staff_product_status"
    )
    def post(self, request, id):
        product = _get_product(id)
        ser = s.StaffStatusSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        before = product.status
        publishing.change_status(product, ser.validated_data["status"])
        audit.record(
            "catalog.product.status_changed",
            request=request,
            actor=request.user,
            target=product,
            before=before,
            after=product.status,
        )
        return Response(product_out(request, product))


class StaffProductTranslationView(StaffCatalogView):
    parser_classes = [JSONParser]

    @extend_schema(
        responses=OpenApiTypes.OBJECT,
        tags=TAG,
        request=s.StaffTranslationInputSerializer,
        operation_id="staff_product_translation_put",
    )
    def put(self, request, id, language):
        product = _get_product(id)
        ser = s.StaffTranslationInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        _upsert_translations(product, {language: dict(ser.validated_data)})
        audit.record(
            "catalog.product.translation_saved", request=request, actor=request.user, target=product, language=language
        )
        return Response(product_out(request, product))


class StaffVariantCreateView(StaffCatalogView):
    parser_classes = [JSONParser]

    @extend_schema(
        responses=OpenApiTypes.OBJECT,
        tags=TAG,
        request=s.StaffVariantWriteSerializer,
        operation_id="staff_variant_create",
    )
    def post(self, request, id):
        product = _get_product(id)
        ser = s.StaffVariantWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        variant = variant_service.create_variant(product, ser.validated_data)
        audit.record("catalog.variant.created", request=request, actor=request.user, target=product, sku=variant.sku)
        return Response(product_out(request, product), status=http.HTTP_201_CREATED)


class StaffVariantDetailView(StaffCatalogView):
    parser_classes = [JSONParser]

    @extend_schema(
        responses=OpenApiTypes.OBJECT,
        tags=TAG,
        request=s.StaffVariantWriteSerializer,
        operation_id="staff_variant_update",
    )
    def patch(self, request, id):
        variant = ProductVariant.objects.select_related("product", "source").filter(public_id=id).first()
        if variant is None:
            raise NotFound()
        ser = s.StaffVariantWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        variant_service.update_variant(variant, ser.validated_data)
        audit.record(
            "catalog.variant.updated",
            request=request,
            actor=request.user,
            target=variant.product,
            sku=variant.sku,
            fields=sorted(ser.validated_data),
        )
        return Response(product_out(request, _get_product(variant.product.public_id)))


class StaffImageUploadView(StaffCatalogView):
    parser_classes = [MultiPartParser, FormParser]
    throttle_scope = None

    @extend_schema(
        responses=OpenApiTypes.OBJECT, tags=TAG, request=s.StaffImageUploadSerializer, operation_id="staff_image_upload"
    )
    def post(self, request, id):
        product = _get_product(id)
        ser = s.StaffImageUploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        source = None
        if data.get("source"):
            source = ProductSource.objects.filter(code=data["source"]).first()
            if source is None:
                raise DomainError("Unknown source.", code="validation_error", fields={"source": ["Unknown source."]})
        upload = data["file"]
        image = image_service.add_product_image(
            product,
            upload.read(),
            rights=data["rights"],
            alt=data["alt"],
            attribution=data["attribution"],
            source=source,
            is_published=data["is_published"],
        )
        audit.record(
            "catalog.image.added",
            request=request,
            actor=request.user,
            target=product,
            image=str(image.public_id),
            rights=image.rights,
        )
        from django.core.cache import cache  # noqa: F401

        return Response(product_out(request, product), status=http.HTTP_201_CREATED)


class StaffImageReorderView(StaffCatalogView):
    parser_classes = [JSONParser]

    @extend_schema(
        responses=OpenApiTypes.OBJECT,
        tags=TAG,
        request=s.StaffImageReorderSerializer,
        operation_id="staff_image_reorder",
    )
    def post(self, request, id):
        product = _get_product(id)
        ser = s.StaffImageReorderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        image_service.reorder_images(product, ser.validated_data["order"])
        from .. import cache

        cache.bump_version()
        audit.record("catalog.image.reordered", request=request, actor=request.user, target=product)
        return Response(product_out(request, product))


class StaffImageDetailView(StaffCatalogView):
    parser_classes = [JSONParser]

    def _image(self, id) -> ProductImage:
        image = ProductImage.objects.select_related("product").filter(public_id=id).first()
        if image is None:
            raise NotFound()
        return image

    @extend_schema(
        request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="staff_image_update"
    )
    def patch(self, request, id):
        image = self._image(id)
        allowed = {"alt": str, "attribution": str, "is_published": bool}
        for key, kind in allowed.items():
            if key in request.data:
                value = request.data[key]
                if not isinstance(value, kind):
                    raise DomainError("Invalid value.", code="validation_error", fields={key: ["Invalid type."]})
                setattr(image, key, value)
        image.save()
        audit.record(
            "catalog.image.updated",
            request=request,
            actor=request.user,
            target=image.product,
            image=str(image.public_id),
        )
        return Response(product_out(request, image.product))

    @extend_schema(responses={204: None}, tags=TAG, operation_id="staff_image_delete")
    def delete(self, request, id):
        image = self._image(id)
        product = image.product
        storage_names = [n for n in (image.image.name, image.thumbnail.name if image.thumbnail else None) if n]
        storage = image.image.storage
        image.delete()
        for name in storage_names:
            storage.delete(name)
        audit.record("catalog.image.deleted", request=request, actor=request.user, target=product)
        return Response(status=http.HTTP_204_NO_CONTENT)
