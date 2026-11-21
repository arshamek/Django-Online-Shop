"""
Database-backed search: exact + partial matching over localised titles, brand (name, local names, aliases),
category names and SKUs. Every token must match somewhere (AND). Uses IN / EXISTS subqueries so a product is
never duplicated by joins, and relevance is a CASE expression (exact title 100, brand 60, title prefix 50, ...).
"""

from django.conf import settings
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, QuerySet, Value, When

from apps.brands.models import BrandAlias, BrandTranslation
from apps.categories.models import CategoryTranslation

from ..models import ProductTranslation, ProductVariant


def _escape(token: str) -> str:
    return token  # parameters are always bound by the ORM; `contains` escapes % and _ itself


class DatabaseSearchBackend:
    def tokens(self, query: str) -> list[str]:
        return query.split()[: settings.CATALOG_SEARCH_MAX_TOKENS]

    def _token_q(self, token: str) -> Q:
        token = _escape(token)
        return (
            Q(pk__in=ProductTranslation.objects.filter(search_text__contains=token).values("product_id"))
            | Q(brand__name__icontains=token)
            | Q(brand_id__in=BrandTranslation.objects.filter(name_local__icontains=token).values("brand_id"))
            | Q(brand_id__in=BrandAlias.objects.filter(alias_normalised__contains=token).values("brand_id"))
            | Q(category_id__in=CategoryTranslation.objects.filter(name__icontains=token).values("category_id"))
            | Q(pk__in=ProductVariant.objects.filter(sku__iexact=token).values("product_id"))
        )

    def apply(self, queryset: QuerySet, query: str) -> QuerySet:
        tokens = self.tokens(query)
        for token in tokens:
            queryset = queryset.filter(self._token_q(token))
        exact_title = Exists(ProductTranslation.objects.filter(product=OuterRef("pk"), search_text=query))
        prefix_title = Exists(ProductTranslation.objects.filter(product=OuterRef("pk"), search_text__startswith=query))
        brand_exact = Q(brand__name__iexact=query) | Q(
            brand_id__in=BrandAlias.objects.filter(alias_normalised=query).values("brand_id")
        )
        return queryset.annotate(
            search_score=Case(
                When(exact_title, then=Value(100)),
                When(brand_exact, then=Value(60)),
                When(prefix_title, then=Value(50)),
                default=Value(10),
                output_field=IntegerField(),
            )
        )
