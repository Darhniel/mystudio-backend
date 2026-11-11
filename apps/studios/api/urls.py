"""URL routes for Studios, mounted under /api/v1/studios/.

The public catalogue routes come first; the detail route is last so that the
more specific image routes are always matched first.
"""

from django.urls import path

from apps.studios.api.views import (
    StudioDetailAPIView,
    StudioImageDetailAPIView,
    StudioImageListCreateAPIView,
    StudioListAPIView,
)

app_name = "studios"

urlpatterns = [
    path("", StudioListAPIView.as_view(), name="studio-list"),
    path(
        "<slug:slug>/images/",
        StudioImageListCreateAPIView.as_view(),
        name="studio-image-list",
    ),
    path(
        "<slug:slug>/images/<uuid:image_id>/",
        StudioImageDetailAPIView.as_view(),
        name="studio-image-detail",
    ),
    path("<slug:slug>/", StudioDetailAPIView.as_view(), name="studio-detail"),
]
