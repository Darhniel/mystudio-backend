"""Root URL configuration.

All business endpoints live under ``/api/v1/``. The OpenAPI schema, Swagger UI
and ReDoc are exposed at ``/api/schema/``, ``/api/docs/`` and ``/api/redoc/``;
production restricts them to staff accounts (see ``API_DOCS_PUBLIC``).
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.common.api.permissions import ApiDocsAccessPermission
from apps.common.api.views import APIRootAPIView, HealthCheckAPIView

api_v1_patterns = [
    path("auth/", include("apps.accounts.api.urls")),
    path("studios/", include("apps.studios.api.urls")),
    path("equipment/", include("apps.equipment.api.urls")),
    path("admin/studios/", include("apps.studios.api.admin_urls")),
    path("admin/equipment/", include("apps.equipment.api.admin_urls")),
    path("bookings/", include("apps.bookings.api.urls")),
    path("payments/", include("apps.payments.api.urls")),
    path("dashboard/", include("apps.dashboard.api.urls")),
]

docs_permissions = [ApiDocsAccessPermission]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", APIRootAPIView.as_view(), name="api-root"),
    path("api/v1/health/", HealthCheckAPIView.as_view(), name="health-check"),
    path("api/v1/", include((api_v1_patterns, "api-v1"))),
    path(
        "api/schema/",
        SpectacularAPIView.as_view(permission_classes=docs_permissions),
        name="schema",
    ),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(
            url_name="schema", permission_classes=docs_permissions
        ),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(
            url_name="schema", permission_classes=docs_permissions
        ),
        name="redoc",
    ),
]

# JSON error bodies for /api/... so clients never receive an HTML error page.
handler404 = "apps.common.api.handlers.not_found"
handler500 = "apps.common.api.handlers.server_error"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
