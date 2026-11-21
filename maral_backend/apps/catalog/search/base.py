from typing import Protocol

from django.db.models import QuerySet


class SearchBackend(Protocol):
    """
    Port for product text search. `apply` narrows `queryset` (of visible Products) to matches for the
    already-normalised `query` and annotates `search_score` (higher = better). A dedicated engine
    (OpenSearch, Meilisearch...) can replace DatabaseSearchBackend through settings.CATALOG_SEARCH_BACKEND
    without touching views, selectors or serializers.
    """

    def apply(self, queryset: QuerySet, query: str) -> QuerySet: ...
