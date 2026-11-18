"""Filtering configuration. Unknown or invalid filters are rejected, never silently ignored."""

from django_filters.rest_framework import DjangoFilterBackend

from apps.common.exceptions import InvalidFilter

# Query parameters understood by every list endpoint (pagination, sorting, search, localisation).
RESERVED_QUERY_PARAMS = frozenset({"page", "page_size", "cursor", "sort", "q", "lang", "currency", "format"})


class StrictDjangoFilterBackend(DjangoFilterBackend):
    """
    On views that declare a filterset, unknown query params => 400 `invalid_filter`
    and invalid filter values => 400 `invalid_filter` with per-field messages.
    Views may whitelist extra params via `extra_query_params = {...}`.
    """

    def filter_queryset(self, request, queryset, view):
        filterset_class = self.get_filterset_class(view, queryset)
        if filterset_class is None:
            return queryset

        allowed = (
            set(filterset_class.base_filters) | RESERVED_QUERY_PARAMS | set(getattr(view, "extra_query_params", ()))
        )
        unknown = sorted(set(request.query_params) - allowed)
        if unknown:
            raise InvalidFilter(
                detail=f"Unknown filter parameter(s): {', '.join(unknown)}.",
                fields={name: ["Unknown filter."] for name in unknown},
            )

        filterset = self.get_filterset(request, queryset, view)
        if not filterset.is_valid():
            raise InvalidFilter(
                fields={name: [str(error) for error in errors] for name, errors in filterset.errors.items()}
            )
        return filterset.qs
