"""API views for studios.

Public list/detail views expose active studios without authentication. Image
management is administrative and only owner/manager accounts may use it. The
Cloudinary calls themselves stay inside ``apps.studios.services`` and
``apps.common.cloudinary``.

Administrative routes (including image mutations) authenticate with a JWT
Bearer token only. Django session cookies are deliberately not accepted there,
even for a logged-in staff account.
"""

from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.accounts.api.permissions import CanManageCatalogue, IsOwnerOrManager
from apps.common.api.exceptions import image_service_error
from apps.common.cloudinary import CloudinaryError
from apps.studios import selectors, services
from apps.studios.api.filters import StudioAdminFilter, StudioFilter
from apps.studios.api.serializers import (
    StudioAdminSerializer,
    StudioDetailSerializer,
    StudioImageSerializer,
    StudioImageUpdateSerializer,
    StudioImageUploadSerializer,
    StudioListSerializer,
)
from apps.studios.models import Studio, StudioImage


class StudioListAPIView(ListAPIView):
    """Public, paginated list of active studios."""

    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = StudioListSerializer
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_class = StudioFilter
    search_fields = ("name", "description")
    ordering_fields = ("name", "hourly_rate_kobo", "capacity", "created_at")
    ordering = ("name",)

    def get_queryset(self):
        return selectors.active_studios()

    @extend_schema(
        tags=["studios"],
        summary="List active studios",
        auth=[],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class StudioDetailAPIView(RetrieveAPIView):
    """Public detail page for an active studio; inactive studios are 404."""

    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = StudioDetailSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return selectors.active_studios()

    def get_object(self):
        # Slugs are stored lower-case, but accepting any case in the URL keeps
        # shared links forgiving without creating case-variant rows.
        queryset = self.filter_queryset(self.get_queryset())
        obj = get_object_or_404(
            queryset,
            slug__iexact=self.kwargs[self.lookup_url_kwarg or self.lookup_field],
        )
        self.check_object_permissions(self.request, obj)
        return obj

    @extend_schema(
        tags=["studios"],
        summary="Retrieve an active studio",
        auth=[],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


@extend_schema_view(
    get=extend_schema(
        tags=["admin-studios"],
        summary="List studios (including inactive)",
    ),
    post=extend_schema(
        tags=["admin-studios"],
        summary="Create a studio",
    ),
)
class StudioAdminListCreateAPIView(ListCreateAPIView):
    """Administrative list and creation endpoint for studios.

    Owner/manager accounts may create; staff accounts may read. Only JWT
    authentication is accepted.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [CanManageCatalogue]
    serializer_class = StudioAdminSerializer
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_class = StudioAdminFilter
    search_fields = ("name", "slug", "description")
    ordering_fields = (
        "name",
        "slug",
        "hourly_rate_kobo",
        "capacity",
        "is_active",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)
    queryset = Studio.objects.prefetch_related("images")


@extend_schema_view(
    get=extend_schema(
        tags=["admin-studios"],
        summary="Retrieve a studio",
    ),
    patch=extend_schema(
        tags=["admin-studios"],
        summary="Partially update a studio",
    ),
    put=extend_schema(
        tags=["admin-studios"],
        summary="Replace a studio",
    ),
    delete=extend_schema(
        tags=["admin-studios"],
        summary="Deactivate a studio",
        description=(
            "Deactivates the studio and keeps its row and images for history."
        ),
    ),
)
class StudioAdminDetailAPIView(RetrieveUpdateDestroyAPIView):
    """Administrative retrieve, update and deactivate endpoint for one studio."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [CanManageCatalogue]
    serializer_class = StudioAdminSerializer
    lookup_url_kwarg = "studio_id"
    queryset = Studio.objects.prefetch_related("images")

    def perform_destroy(self, instance):
        services.deactivate_studio(studio=instance)


class StudioImageUploadMixin:
    """Shared upload behaviour for the two studio image collection routes."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsOwnerOrManager]
    studio_lookup_field = "pk"
    studio_url_kwarg = "studio_id"

    def get_studio(self, **kwargs):
        value = kwargs[self.studio_url_kwarg]
        if self.studio_lookup_field == "slug":
            lookup = {"slug__iexact": value}
        else:
            lookup = {self.studio_lookup_field: value}
        return get_object_or_404(Studio, **lookup)

    def _upload(self, request, **kwargs):
        studio = self.get_studio(**kwargs)
        serializer = StudioImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            image = services.add_studio_image(
                studio=studio,
                image=serializer.validated_data["image"],
                alt_text=serializer.validated_data.get("alt_text", ""),
                display_order=serializer.validated_data.get("display_order"),
            )
        except CloudinaryError as exc:
            raise image_service_error(exc) from exc

        return Response(
            StudioImageSerializer(image).data,
            status=status.HTTP_201_CREATED,
        )


class StudioImageDetailMixin:
    """Shared metadata update and deletion for one studio image."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsOwnerOrManager]
    studio_lookup_field = "pk"
    studio_url_kwarg = "studio_id"

    def get_studio(self, **kwargs):
        value = kwargs[self.studio_url_kwarg]
        if self.studio_lookup_field == "slug":
            lookup = {"slug__iexact": value}
        else:
            lookup = {self.studio_lookup_field: value}
        return get_object_or_404(Studio, **lookup)

    def get_image(self, **kwargs):
        studio = self.get_studio(**kwargs)
        return get_object_or_404(
            StudioImage,
            pk=kwargs["image_id"],
            studio=studio,
        )

    def _patch(self, request, **kwargs):
        image = self.get_image(**kwargs)
        serializer = StudioImageUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        image = services.update_studio_image(
            image=image,
            alt_text=serializer.validated_data.get("alt_text"),
            display_order=serializer.validated_data.get("display_order"),
        )
        return Response(StudioImageSerializer(image).data)

    def _delete(self, request, **kwargs):
        image = self.get_image(**kwargs)

        try:
            services.delete_studio_image(image=image)
        except CloudinaryError as exc:
            raise image_service_error(exc) from exc

        return Response(status=status.HTTP_204_NO_CONTENT)


class StudioAdminImageCreateAPIView(StudioImageUploadMixin, APIView):
    """Add an image to a studio via multipart/form-data (administrative)."""

    @extend_schema(
        tags=["admin-studios"],
        summary="Upload a studio image",
        request=StudioImageUploadSerializer,
        responses={201: StudioImageSerializer},
    )
    def post(self, request, studio_id):
        return self._upload(request, studio_id=studio_id)


class StudioAdminImageDetailAPIView(StudioImageDetailMixin, APIView):
    """Update one image's metadata or remove it and its Cloudinary asset."""

    @extend_schema(
        tags=["admin-studios"],
        summary="Update a studio image's metadata",
        request=StudioImageUpdateSerializer,
        responses={200: StudioImageSerializer},
    )
    def patch(self, request, studio_id, image_id):
        return self._patch(request, studio_id=studio_id, image_id=image_id)

    @extend_schema(
        tags=["admin-studios"],
        summary="Delete a studio image",
        responses={204: None},
    )
    def delete(self, request, studio_id, image_id):
        return self._delete(request, studio_id=studio_id, image_id=image_id)


class StudioImageListCreateAPIView(StudioImageUploadMixin, APIView):
    """Add an image to a studio via multipart/form-data."""

    studio_lookup_field = "slug"
    studio_url_kwarg = "slug"

    @extend_schema(
        tags=["studios"],
        summary="Upload a studio image",
        request=StudioImageUploadSerializer,
        responses={201: StudioImageSerializer},
    )
    def post(self, request, slug):
        return self._upload(request, slug=slug)


class StudioImageDetailAPIView(StudioImageDetailMixin, APIView):
    """Update the metadata of one image or remove it and its Cloudinary asset."""

    studio_lookup_field = "slug"
    studio_url_kwarg = "slug"

    @extend_schema(
        tags=["studios"],
        summary="Update a studio image's metadata",
        request=StudioImageUpdateSerializer,
        responses={200: StudioImageSerializer},
    )
    def patch(self, request, slug, image_id):
        return self._patch(request, slug=slug, image_id=image_id)

    @extend_schema(
        tags=["studios"],
        summary="Delete a studio image",
        responses={204: None},
    )
    def delete(self, request, slug, image_id):
        return self._delete(request, slug=slug, image_id=image_id)
