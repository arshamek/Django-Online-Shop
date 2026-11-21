"""Public storefront endpoints. Anonymous, GET-only, cacheable (Cache-Control + ETag + server-side cache)."""

import hashlib
import json
import uuid

from django.conf import settings
from django.db.models import Count, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.brands.models import Brand
from apps.categories.models import Category
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import InvalidFilter
from apps.product_sources.models import ProductSource

from .. import presenters as pr
from .. import selectors
from ..cache import cached
from ..context import CatalogContext, get_context
from ..models import Product, ProductListingProjection
from ..params import parse_product_params
from ..serializers import ProductCardSerializer, ProductDetailSerializer

TAG = ["storefront"]
CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=300"


class PublicCatalogView(APIView):
    authentication_classes: list = []  # public, user-independent responses => cacheable by shared caches
    permission_classes = [AllowAny]

    def dispatch(self, request, *args, **kwargs):
        self._url_version = kwargs.pop("version", None)
        return super().dispatch(request, *args, **kwargs)

    def determine_version(self, request, *args, **kwargs):
        if getattr(self, "_url_version", None) is not None:
            kwargs["version"] = self._url_version
        return super().determine_version(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if request.method == "GET" and response.status_code == 200 and isinstance(response.data, (dict, list)):
            digest = hashlib.md5(json.dumps(response.data, sort_keys=True, default=str).encode()).hexdigest()  # noqa: S324 - cache validator only
            etag = f'"{digest}"'
            response["ETag"] = etag
            response["Cache-Control"] = CACHE_CONTROL
            response["Vary"] = "Accept-Language, Accept-Currency"
            if request.headers.get("If-None-Match") == etag:
                response.status_code = 304
                response.data = None
                response.content = b""
        return response

    def context(self, request) -> CatalogContext:
        return get_context(request)


class CatalogPagination(StandardPagination):
    def paginate_queryset(self, queryset, request, view=None):
        size = self.get_page_size(request)
        try:
            page = int(request.query_params.get(self.page_query_param, 1))
        except (TypeError, ValueError):
            page = 1
        if page * size > settings.CATALOG_MAX_DEEP_PAGE_ITEMS:
            raise InvalidFilter(
                fields={
                    "page": [
                        f"Paging is limited to the first {settings.CATALOG_MAX_DEEP_PAGE_ITEMS} results; refine the filters."
                    ]
                }
            )
        return super().paginate_queryset(queryset, request, view)


def _paged_cards(view, request, ctx, queryset, key_parts) -> dict:
    def build():
        paginator = CatalogPagination()
        page = paginator.paginate_queryset(selectors.with_card_data(queryset, ctx), request, view=view)
        data = [pr.build_card(p, ctx) for p in page]
        return paginator.get_paginated_response(data).data

    return cached(
        "list",
        {**key_parts, **ctx.cache_parts, "host": request.get_host(), "qs": sorted(request.query_params.lists())},
        build,
    )


FILTER_DOC = [
    OpenApiParameter(
        "category", str, description="Category slug(s); includes sub-categories. Repeat or comma-separate."
    ),
    OpenApiParameter("brand", str, description="Brand slug(s)."),
    OpenApiParameter("source", str, description="Source code(s), e.g. trendyol, namshi, maral."),
    OpenApiParameter("size", str, description="Size option code(s)."),
    OpenApiParameter("color", str, description="Colour option code(s)."),
    OpenApiParameter("price_min", float, description="Minimum final price in the display currency."),
    OpenApiParameter("price_max", float, description="Maximum final price in the display currency."),
    OpenApiParameter("sale", bool),
    OpenApiParameter("available", bool),
    OpenApiParameter("is_new", bool),
    OpenApiParameter("q", str, description="Search text (2-100 chars)."),
    OpenApiParameter("sort", str, enum=["popular", "new", "price_asc", "price_desc", "discount", "relevance"]),
    OpenApiParameter("lang", str, enum=["fa", "en", "ar", "tr"]),
    OpenApiParameter("currency", str, enum=["IRT", "IRR"]),
]


class ProductListView(PublicCatalogView):
    @extend_schema(
        tags=TAG, parameters=FILTER_DOC, responses=ProductCardSerializer(many=True), operation_id="products_list"
    )
    def get(self, request, **kwargs):
        ctx = self.context(request)
        params = parse_product_params(request, ctx)
        return Response(
            _paged_cards(
                self, request, ctx, selectors.listing_queryset(params), {"view": "list", "p": params.cache_parts()}
            )
        )


class ProductSearchView(PublicCatalogView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "catalog_search"

    @extend_schema(
        tags=TAG, parameters=FILTER_DOC, responses=ProductCardSerializer(many=True), operation_id="products_search"
    )
    def get(self, request, **kwargs):
        ctx = self.context(request)
        params = parse_product_params(request, ctx, require_q=True)
        return Response(
            _paged_cards(
                self, request, ctx, selectors.listing_queryset(params), {"view": "search", "p": params.cache_parts()}
            )
        )


class ProductFiltersView(PublicCatalogView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, parameters=FILTER_DOC, operation_id="products_filters")
    def get(self, request, **kwargs):
        ctx = self.context(request)
        params = parse_product_params(request, ctx)

        def build():
            raw = selectors.facet_data(params)
            chain = list(ctx.chain)
            cats = selectors.categories_by_slug([r["category__slug"] for r in raw["categories"]], chain)
            brands = selectors.brands_by_slug([r["brand__slug"] for r in raw["brands"]], chain)
            sources = selectors.sources_by_code([r["source__code"] for r in raw["sources"]])
            sizes = selectors.options_by_code("size", [r["option__code"] for r in raw["sizes"]], chain)
            colors = selectors.options_by_code("color", [r["option__code"] for r in raw["colors"]], chain)
            price = raw["price"]
            return {
                "categories": [
                    {**pr.category_ref(cats[r["category__slug"]], ctx), "count": r["n"]}
                    for r in raw["categories"]
                    if r["category__slug"] in cats
                ],
                "brands": [
                    {**pr.brand_ref(brands[r["brand__slug"]], ctx), "count": r["n"]}
                    for r in raw["brands"]
                    if r["brand__slug"] in brands
                ],
                "sources": [
                    {**pr.source_ref(sources[r["source__code"]]), "count": r["n"]}
                    for r in raw["sources"]
                    if r["source__code"] in sources
                ],
                "sizes": [
                    {"code": c, "name": pr.option_name(sizes[c], ctx), "count": r["n"]}
                    for r in raw["sizes"]
                    if (c := r["option__code"]) in sizes
                ],
                "colors": [
                    {
                        "code": c,
                        "name": pr.option_name(colors[c], ctx),
                        "value": colors[c].swatch or None,
                        "count": r["n"],
                    }
                    for r in raw["colors"]
                    if (c := r["option__code"]) in colors
                ],
                "price_range": {
                    "currency": ctx.currency,
                    "min": pr.money(price["lo"], ctx.currency)["amount"] if price["lo"] is not None else None,
                    "max": pr.money(price["hi"], ctx.currency)["amount"] if price["hi"] is not None else None,
                },
                "flags": raw["flags"],
            }

        payload = cached("facets", {**ctx.cache_parts, "p": params.cache_parts()}, build)
        return Response(payload)


class ProductDetailView(PublicCatalogView):
    @extend_schema(
        tags=TAG, parameters=FILTER_DOC[-2:], responses=ProductDetailSerializer, operation_id="products_detail"
    )
    def get(self, request, slug, **kwargs):
        ctx = self.context(request)

        def build():
            product = _get_visible(slug, ctx, detail=True)
            related = selectors.with_card_data(selectors.related_products(product, 4), ctx)
            return pr.build_detail(product, ctx, [pr.build_card(p, ctx) for p in related])

        return Response(cached("detail", {"slug": slug, **ctx.cache_parts, "host": request.get_host()}, build))


def _get_visible(ref: str, ctx, *, detail=False) -> Product:
    qs = selectors.visible_products()
    try:
        qs = qs.filter(public_id=uuid.UUID(ref))
    except ValueError:
        qs = qs.filter(slug=ref)
    qs = selectors.with_card_data(qs, ctx)
    if detail:
        qs = qs.prefetch_related(selectors.spec_values_prefetch(list(ctx.chain)))
    product = qs.first()
    if product is None:
        raise NotFound()
    return product


class ProductRelatedView(PublicCatalogView):
    @extend_schema(
        tags=TAG,
        parameters=[OpenApiParameter("limit", int)],
        responses=ProductCardSerializer(many=True),
        operation_id="products_related",
    )
    def get(self, request, ref, **kwargs):
        ctx = self.context(request)
        raw_limit = request.query_params.get("limit", "4")
        if not raw_limit.isdigit() or not 1 <= int(raw_limit) <= 12:
            raise InvalidFilter(fields={"limit": ["Use a number between 1 and 12."]})
        limit = int(raw_limit)

        def build():
            product = selectors.visible_products().filter(**_ref_filter(ref)).first()
            if product is None:
                raise NotFound()
            items = selectors.with_card_data(selectors.related_products(product, limit), ctx)
            results = [pr.build_card(p, ctx) for p in items]
            return {
                "count": len(results),
                "page": 1,
                "page_size": limit,
                "next": None,
                "previous": None,
                "results": results,
            }

        return Response(
            cached("related", {"ref": ref, "limit": limit, **ctx.cache_parts, "host": request.get_host()}, build)
        )


def _ref_filter(ref: str) -> dict:
    try:
        return {"public_id": uuid.UUID(ref)}
    except ValueError:
        return {"slug": ref}


class RecentlyViewedView(PublicCatalogView):
    """
    The existing UI keeps its recently-viewed ids client-side (docs/frontend-analysis), so this endpoint
    hydrates them: `?ids=<uuid>,<uuid>` (max 50) -> visible products, in the order given. The server stores
    no browsing history (a privacy decision recorded in docs/api/CATALOG_API.md).
    """

    @extend_schema(
        tags=TAG,
        parameters=[OpenApiParameter("ids", str, required=True)],
        responses=ProductCardSerializer(many=True),
        operation_id="products_recently_viewed",
    )
    def get(self, request, **kwargs):
        ctx = self.context(request)
        allowed = {"ids", "lang", "currency"}
        extra = sorted(set(request.query_params) - allowed)
        if extra:
            raise InvalidFilter(fields={name: ["Unknown filter."] for name in extra})
        params = parse_product_params(request, ctx)
        if not params.ids:
            return Response({"count": 0, "page": 1, "page_size": 0, "next": None, "previous": None, "results": []})

        def build():
            items = {
                p.public_id: p
                for p in selectors.with_card_data(selectors.visible_products().filter(public_id__in=params.ids), ctx)
            }
            results = [pr.build_card(items[i], ctx) for i in params.ids if i in items]
            return {
                "count": len(results),
                "page": 1,
                "page_size": len(params.ids),
                "next": None,
                "previous": None,
                "results": results,
            }

        return Response(
            cached(
                "recent", {"ids": [str(i) for i in params.ids], **ctx.cache_parts, "host": request.get_host()}, build
            )
        )


# ------------------------------------------------------------------ categories / brands / homepage
def _category_row(c: Category, ctx, counts: dict, parent_slug) -> dict:
    return {
        **pr.category_ref(c, ctx),
        "parent": parent_slug,
        "depth": c.depth,
        "image_url": pr.file_url(ctx, c.image),
        "description": (t.description if (t := pr.pick(list(c.translations.all()), ctx.chain)) else ""),
        "product_count": counts.get(c.pk, 0),
    }


def _category_counts() -> dict:
    """Visible products per category *including sub-categories* (projection rows exist only for visible products)."""
    direct = dict(ProductListingProjection.objects.values_list("category_id").annotate(n=Count("pk")))
    paths = dict(Category.objects.filter(is_active=True).values_list("pk", "path"))
    return {
        pk: sum(n for cid, n in direct.items() if paths.get(cid, "").startswith(path)) for pk, path in paths.items()
    }


class CategoryListView(PublicCatalogView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="categories_list")
    def get(self, request, **kwargs):
        ctx = self.context(request)

        def build():
            from django.db.models import Prefetch

            from apps.categories.models import CategoryTranslation

            cats = list(
                Category.objects.filter(is_active=True)
                .select_related("parent")
                .prefetch_related(
                    Prefetch("translations", queryset=CategoryTranslation.objects.filter(language__in=list(ctx.chain)))
                )
                .order_by("path", "sort_order", "id")
            )
            counts = _category_counts()
            active = {c.pk for c in cats}
            rows = [
                _category_row(c, ctx, counts, c.parent.slug if c.parent_id else None)
                for c in cats
                if not c.parent_id or c.parent_id in active
            ]
            return {"count": len(rows), "results": rows}

        return Response(cached("categories", {**ctx.cache_parts, "host": request.get_host()}, build))


class CategoryDetailView(PublicCatalogView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="categories_detail")
    def get(self, request, slug, **kwargs):
        ctx = self.context(request)

        def build():
            from django.db.models import Prefetch

            from apps.categories.models import CategoryTranslation

            prefetch = Prefetch(
                "translations", queryset=CategoryTranslation.objects.filter(language__in=list(ctx.chain))
            )
            category = (
                Category.objects.filter(slug=slug, is_active=True)
                .select_related("parent")
                .prefetch_related(prefetch)
                .first()
            )
            if category is None:
                raise NotFound()
            counts = _category_counts()
            children = category.children.filter(is_active=True).prefetch_related(prefetch).order_by("sort_order", "id")
            return {
                **_category_row(category, ctx, counts, category.parent.slug if category.parent_id else None),
                "breadcrumb": pr._breadcrumb(category, ctx),
                "children": [_category_row(c, ctx, counts, category.slug) for c in children],
            }

        return Response(cached("category", {"slug": slug, **ctx.cache_parts, "host": request.get_host()}, build))


def _brand_row(b: Brand, ctx, count: int) -> dict:
    return {**pr.brand_ref(b, ctx), "logo_url": pr.file_url(ctx, b.logo), "product_count": count}


class BrandListView(PublicCatalogView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="brands_list")
    def get(self, request, **kwargs):
        ctx = self.context(request)

        def build():
            from django.db.models import Prefetch

            from apps.brands.models import BrandTranslation

            brands = (
                Brand.objects.filter(is_active=True)
                .annotate(n=Count("products", filter=Q(products__projection__isnull=False)))
                .filter(n__gt=0)
                .prefetch_related(
                    Prefetch("translations", queryset=BrandTranslation.objects.filter(language__in=list(ctx.chain)))
                )
                .order_by("name", "id")
            )
            rows = [_brand_row(b, ctx, b.n) for b in brands]
            return {"count": len(rows), "results": rows}

        return Response(cached("brands", {**ctx.cache_parts, "host": request.get_host()}, build))


class BrandDetailView(PublicCatalogView):
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="brands_detail")
    def get(self, request, slug, **kwargs):
        ctx = self.context(request)

        def build():
            from django.db.models import Prefetch

            from apps.brands.models import BrandTranslation

            brand = (
                Brand.objects.filter(slug=slug, is_active=True)
                .annotate(n=Count("products", filter=Q(products__projection__isnull=False)))
                .prefetch_related(
                    Prefetch("translations", queryset=BrandTranslation.objects.filter(language__in=list(ctx.chain)))
                )
                .first()
            )
            if brand is None:
                raise NotFound()
            return _brand_row(brand, ctx, brand.n)

        return Response(cached("brand", {"slug": slug, **ctx.cache_parts, "host": request.get_host()}, build))


class HomepageView(PublicCatalogView):
    """Data-driven sections only; hero/banner copy is static frontend content (open question Q-cms)."""

    @extend_schema(responses=OpenApiTypes.OBJECT, tags=TAG, operation_id="storefront_homepage")
    def get(self, request, **kwargs):
        ctx = self.context(request)

        def build():
            from django.utils import timezone

            top_categories = (
                Category.objects.filter(is_active=True, parent__isnull=True)
                .prefetch_related("translations")
                .order_by("sort_order", "id")
            )
            counts = _category_counts()
            brands = (
                Brand.objects.filter(is_active=True)
                .annotate(n=Count("products", filter=Q(products__projection__isnull=False)))
                .filter(n__gt=0)
                .prefetch_related("translations")
                .order_by("-n", "name")[:12]
            )
            source_counts = dict(
                Product.objects.visible()
                .values_list("listing_sources__source__code")
                .annotate(n=Count("pk", distinct=True))
            )
            sources = ProductSource.objects.filter(is_active=True, code__in=[c for c in source_counts if c]).order_by(
                "sort_order", "id"
            )
            base = selectors.visible_products()
            trending = selectors.with_card_data(
                base.filter(projection__discount_percent__gt=0).order_by("-projection__popularity_score", "-pk")[:8],
                ctx,
            )
            fresh = selectors.with_card_data(
                base.filter(projection__new_until__gt=timezone.now()).order_by("-projection__new_until", "-pk")[:8], ctx
            )
            return {
                "language": ctx.lang,
                "currency": ctx.currency,
                "categories": [_category_row(c, ctx, counts, None) for c in top_categories],
                "brands": [_brand_row(b, ctx, b.n) for b in brands],
                "sources": [{**pr.source_ref(s), "product_count": source_counts.get(s.code, 0)} for s in sources],
                "trending": [pr.build_card(p, ctx) for p in trending],
                "new_arrivals": [pr.build_card(p, ctx) for p in fresh],
            }

        return Response(cached("homepage", {**ctx.cache_parts, "host": request.get_host()}, build))
