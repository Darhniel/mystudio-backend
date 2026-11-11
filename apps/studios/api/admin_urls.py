"""Administrative studio routes, mounted under /api/v1/admin/studios/."""

from django.urls import path

from apps.studios.api.views import (
    StudioAdminDetailAPIView,
    StudioAdminImageCreateAPIView,
    StudioAdminImageDetailAPIView,
    StudioAdminListCreateAPIView,
)

app_name = "studios-admin"

urlpatterns = [
    path("", StudioAdminListCreateAPIView.as_view(), name="studio-list"),
    path(
        "<uuid:studio_id>/images/",
        StudioAdminImageCreateAPIView.as_view(),
        name="studio-image-list",
    ),
    path(
        "<uuid:studio_id>/images/<uuid:image_id>/",
        StudioAdminImageDetailAPIView.as_view(),
        name="studio-image-detail",
    ),
    path(
        "<uuid:studio_id>/",
        StudioAdminDetailAPIView.as_view(),
        name="studio-detail",
    ),
]
