"""Default pagination for list endpoints."""

from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    """Page-number pagination with a bounded client-selectable page size."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
