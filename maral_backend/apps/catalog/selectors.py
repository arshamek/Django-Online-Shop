"""Read-side queries: visibility, filters, sorting, facets. Views and presenters never build ORM filters."""

from django.db.models import Count, Exists, Max, Min, OuterRef, Prefetch, Q, QuerySet
from django.utils import timezone

from apps.brands.models import Brand, BrandTranslation
from apps.categories.models import Category, CategoryTranslation
from apps.product_sources.models import ProductSource

from .context import CatalogContext
from .models import (
    AttributeOption,
    AttributeOptionTranslation,
    AvailabilityStatus,
    Product,
    ProductAttributeValue,
    ProductImage,
    ProductListingSource,
    ProductTranslation,
    ProductVariant,
    ProductVariantOption,
)
from .params import ProductParams
from .search import get_search_backend

SORT_ORDER = {
    "popular": ("-projection__popularity_score", "-pk"),
    "new": ("-projection__new_until", "-projection__popularity_score", "-pk"),
    "price_asc": ("projection__price_final_irr", "-projection__popularity_score", "pk"),
    "price_desc": ("-projection__price_final_irr", "-projection__popularity_score", "-pk"),
    "discount": ("-projection__discount_percent", "-projection__popularity_score", "-pk"),
    "relevance": ("-search_score", "-projection__popularity_score", "-pk"),
}


def visible_products() -> QuerySet:
    return Product.objects.visible()


# ----------------------------------------------------------------------------- filters
def _option_exists(attribute: str, codes: list[str], sellable_only: bool):
    qs = ProductVariantOption.objects.filter(
        variant__product=OuterRef("pk"),
        variant__is_active=True,
        attribute__code=attribute,
        option__code__in=codes,
    )
    if sellable_only:
        qs = qs.filter(variant__availability__status=AvailabilityStatus.IN_STOCK)
    return Exists(qs)


def apply_filters(qs: QuerySet, p: ProductParams, *, exclude: frozenset = frozenset()) -> QuerySet:
    """`exclude` names filter groups to skip (facets are computed under the *other* active filters)."""
    if p.categories and "category" not in exclude:
        paths = list(Category.objects.filter(slug__in=p.categories, is_active=True).values_list("path", flat=True))
        if not paths:
            return qs.none()
        condition = Q()
        for path in paths:
            condition |= Q(projection__category_path__startswith=path)
        qs = qs.filter(condition)
    if p.brands and "brand" not in exclude:
        qs = qs.filter(brand__slug__in=p.brands)
    if p.sources and "source" not in exclude:
        qs = qs.filter(Exists(ProductListingSource.objects.filter(product=OuterRef("pk"), source__code__in=p.sources)))
    if p.sale is not None and "sale" not in exclude:
        qs = qs.filter(projection__discount_percent__gt=0) if p.sale else qs.filter(projection__discount_percent=0)
    if p.available is not None and "available" not in exclude:
        qs = qs.filter(projection__is_available=p.available)
    if p.is_new is not None and "is_new" not in exclude:
        now = timezone.now()
        qs = qs.filter(projection__new_until__gt=now) if p.is_new else qs.exclude(projection__new_until__gt=now)
    if "price" not in exclude:
        if p.price_min_irr is not None:
            qs = qs.filter(projection__price_final_irr__gte=p.price_min_irr)
        if p.price_max_irr is not None:
            qs = qs.filter(projection__price_final_irr__lte=p.price_max_irr)
    sellable_only = bool(p.available)
    if p.sizes and "size" not in exclude:
        qs = qs.filter(_option_exists("size", p.sizes, sellable_only))
    if p.colors and "color" not in exclude:
        qs = qs.filter(_option_exists("color", p.colors, sellable_only))
    if p.ids:
        qs = qs.filter(public_id__in=p.ids)
    if p.q and "q" not in exclude:
        qs = get_search_backend().apply(qs, p.q)
    return qs


def listing_queryset(p: ProductParams) -> QuerySet:
    qs = apply_filters(visible_products(), p)
    sort = p.sort if (p.sort != "relevance" or p.q) else "popular"
    return qs.order_by(*SORT_ORDER[sort])


# ----------------------------------------------------------------------------- prefetching
def with_card_data(qs: QuerySet, ctx: CatalogContext) -> QuerySet:
    """Everything a product card needs in a constant number of queries (no N+1)."""
    chain = list(ctx.chain)
    option_names = Prefetch(
        "option__translations", queryset=AttributeOptionTranslation.objects.filter(language__in=chain)
    )
    variants = (
        ProductVariant.objects.filter(is_active=True)
        .select_related("availability", "source", "price")
        .prefetch_related(
            Prefetch(
                "options",
                queryset=ProductVariantOption.objects.select_related("attribute", "option").prefetch_related(
                    option_names
                ),
            )
        )
    )
    return qs.select_related("brand", "category", "projection", "projection__display_source").prefetch_related(
        Prefetch("translations", queryset=ProductTranslation.objects.all()),
        Prefetch("brand__translations", queryset=BrandTranslation.objects.filter(language__in=chain)),
        Prefetch("category__translations", queryset=CategoryTranslation.objects.filter(language__in=chain)),
        Prefetch(
            "images", queryset=ProductImage.objects.published().order_by("sort_order", "id"), to_attr="published_images"
        ),
        Prefetch("variants", queryset=variants, to_attr="active_variants"),
    )


def spec_values_prefetch(chain: list[str]) -> Prefetch:
    return Prefetch(
        "attribute_values",
        queryset=ProductAttributeValue.objects.select_related("attribute", "option")
        .prefetch_related(
            "attribute__translations",
            Prefetch("option__translations", queryset=AttributeOptionTranslation.objects.filter(language__in=chain)),
        )
        .order_by("sort_order", "id"),
    )


# ----------------------------------------------------------------------------- related
def related_products(product: Product, limit: int) -> QuerySet:
    """Same category first, then same brand; best sellers first. Never the product itself."""
    from django.db.models import Case, IntegerField, Value, When

    return (
        visible_products()
        .exclude(pk=product.pk)
        .filter(Q(category_id=product.category_id) | Q(brand_id=product.brand_id))
        .annotate(
            related_score=Case(
                When(Q(category_id=product.category_id) & Q(brand_id=product.brand_id), then=Value(3)),
                When(category_id=product.category_id, then=Value(2)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("-related_score", "-projection__popularity_score", "-pk")[:limit]
    )


# ----------------------------------------------------------------------------- facets
def facet_data(p: ProductParams) -> dict:
    """Raw facet counts; names are resolved by the presenter. Each group ignores its own filter."""
    base = visible_products()
    out: dict = {}

    cat_qs = apply_filters(base, p, exclude=frozenset({"category"}))
    out["categories"] = list(cat_qs.values("category__slug").annotate(n=Count("pk")).order_by("-n", "category__slug"))
    brand_qs = apply_filters(base, p, exclude=frozenset({"brand"}))
    out["brands"] = list(brand_qs.values("brand__slug").annotate(n=Count("pk")).order_by("-n", "brand__slug"))

    source_pks = apply_filters(base, p, exclude=frozenset({"source"})).values("pk")
    out["sources"] = list(
        ProductListingSource.objects.filter(product__in=source_pks)
        .values("source__code")
        .annotate(n=Count("product"))
        .order_by("-n", "source__code")
    )
    for axis, group in (("size", "size"), ("color", "color")):
        pks = apply_filters(base, p, exclude=frozenset({group})).values("pk")
        out[f"{axis}s"] = list(
            ProductVariantOption.objects.filter(variant__is_active=True, variant__product__in=pks, attribute__code=axis)
            .values("option__code")
            .annotate(n=Count("variant__product", distinct=True), order=Min("option__sort_order"))
            .order_by("order", "option__code")
        )
    price_qs = apply_filters(base, p, exclude=frozenset({"price"}))
    out["price"] = price_qs.aggregate(lo=Min("projection__price_final_irr"), hi=Max("projection__price_final_irr"))
    now = timezone.now()
    flag_qs = apply_filters(base, p, exclude=frozenset({"sale", "available", "is_new"}))
    out["flags"] = flag_qs.aggregate(
        on_sale=Count("pk", filter=Q(projection__discount_percent__gt=0)),
        available=Count("pk", filter=Q(projection__is_available=True)),
        is_new=Count("pk", filter=Q(projection__new_until__gt=now)),
        total=Count("pk"),
    )
    return out


def options_by_code(attribute: str, codes: list[str], chain: list[str]) -> dict:
    options = AttributeOption.objects.filter(attribute__code=attribute, code__in=codes).prefetch_related(
        Prefetch("translations", queryset=AttributeOptionTranslation.objects.filter(language__in=chain))
    )
    return {o.code: o for o in options}


def brands_by_slug(slugs, chain):
    return {
        b.slug: b
        for b in Brand.objects.filter(slug__in=slugs).prefetch_related(
            Prefetch("translations", queryset=BrandTranslation.objects.filter(language__in=chain))
        )
    }


def categories_by_slug(slugs, chain):
    return {
        c.slug: c
        for c in Category.objects.filter(slug__in=slugs).prefetch_related(
            Prefetch("translations", queryset=CategoryTranslation.objects.filter(language__in=chain))
        )
    }


def sources_by_code(codes):
    return {s.code: s for s in ProductSource.objects.filter(code__in=codes)}
