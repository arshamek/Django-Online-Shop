from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    """`{count, page, page_size, next, previous, results}`; default 24, max 100 (API conventions section 6)."""

    page_size = 24
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "page": self.page.number,
                "page_size": self.get_page_size(self.request),
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )

    def get_paginated_response_schema(self, schema):
        nullable_url = {"type": "string", "format": "uri", "nullable": True}
        return {
            "type": "object",
            "required": ["count", "page", "page_size", "results"],
            "properties": {
                "count": {"type": "integer", "example": 125},
                "page": {"type": "integer", "example": 1},
                "page_size": {"type": "integer", "example": 24},
                "next": {**nullable_url, "example": "https://api.example.com/api/v1/items?page=2"},
                "previous": nullable_url,
                "results": schema,
            },
        }
