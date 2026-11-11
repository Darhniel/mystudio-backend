"""Permissions for operational API endpoints."""

from django.conf import settings
from rest_framework.permissions import BasePermission


class ApiDocsAccessPermission(BasePermission):
    """Allow API documentation to the public only when configured.

    ``API_DOCS_PUBLIC`` is true in development and tests. Production defaults
    it to false, so the schema and the Swagger/ReDoc pages require a staff
    account. The check reads settings per request, which keeps it testable and
    lets deployments open the docs without a code change.
    """

    message = "API documentation is restricted to staff accounts."

    def has_permission(self, request, view):
        if settings.API_DOCS_PUBLIC:
            return True
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)
