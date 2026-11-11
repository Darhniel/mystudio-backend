"""JSON error pages for the API.

Django's default handlers return HTML. Clients calling ``/api/...`` expect the
same JSON envelope whether the failure comes from DRF, from a missing route or
from an unhandled exception outside a DRF view.
"""

import logging

from django.http import JsonResponse
from django.views.defaults import page_not_found
from django.views.defaults import server_error as server_error_default

logger = logging.getLogger("mystudio.errors")

NOT_FOUND_DETAIL = "Not found."
SERVER_ERROR_DETAIL = "An unexpected error occurred. Please try again later."


def _is_api_request(request) -> bool:
    return request.path.startswith("/api/")


def not_found(request, exception=None):
    """``handler404``: JSON for API paths, Django's page elsewhere."""
    if _is_api_request(request):
        return JsonResponse({"detail": NOT_FOUND_DETAIL}, status=404)
    return page_not_found(request, exception)


def server_error(request):
    """``handler500``: JSON for API paths, Django's template elsewhere."""
    if _is_api_request(request):
        logger.error("Unhandled error outside a DRF view: %s", request.path)
        return JsonResponse({"detail": SERVER_ERROR_DETAIL}, status=500)
    return server_error_default(request)
