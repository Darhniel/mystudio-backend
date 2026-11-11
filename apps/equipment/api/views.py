"""API views for equipment.

The public list exposes active catalogue items without authentication. Image
management stays administrative and delegates every Cloudinary operation to
``apps.equipment.services``.

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
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.accounts.api.permissions import CanManageCatalogue, IsOwnerOrManager
from apps.common.api.exceptions import image_service_error
from apps.common.cloudinary import CloudinaryError
from apps.equipment import selectors, services
from apps.equipment.api.filters import EquipmentAdminFilter, EquipmentFilter
from apps.equipment.api.serializers import (
    EquipmentAdminSerializer,
    EquipmentImageSerializer,
    EquipmentImageUploadSerializer,
    EquipmentPublicSerializer,
)
from apps.equipment.models import Equipment


class EquipmentListAPIView(ListAPIView):
    """Public, paginated list of active equipment."""

    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = EquipmentPublicSerializer
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_class = EquipmentFilter
    search_fields = ("name", "description")
    ordering_fields = ("name", "fixed_price_kobo", "created_at")
    ordering = ("name",)

    def get_queryset(self):
        return selectors.active_equipment()

    @extend_schema(
        tags=["equipment"],
        summary="List active equipment",
        auth=[],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


@extend_schema_view(
    get=extend_schema(
        tags=["admin-equipment"],
        summary="List equipment (including inactive)",
    ),
    post=extend_schema(
        tags=["admin-equipment"],
        summary="Create an equipment item",
    ),
)
class EquipmentAdminListCreateAPIView(ListCreateAPIView):
    """Administrative list and creation endpoint for equipment.

    Owner/manager accounts may create; staff accounts may read. Creation
    accepts JSON or multipart/form-data when an ``image`` is supplied. Only JWT
    authentication is accepted.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [CanManageCatalogue]
    serializer_class = EquipmentAdminSerializer
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_class = EquipmentAdminFilter
    search_fields = ("name", "description")
    ordering_fields = (
        "name",
        "fixed_price_kobo",
        "stock_quantity",
        "is_active",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)
    queryset = Equipment.objects.all()

    def perform_create(self, serializer):
        try:
            serializer.save()
        except CloudinaryError as exc:
            raise image_service_error(exc) from exc


@extend_schema_view(
    get=extend_schema(
        tags=["admin-equipment"],
        summary="Retrieve an equipment item",
    ),
    patch=extend_schema(
        tags=["admin-equipment"],
        summary="Partially update an equipment item",
    ),
    put=extend_schema(
        tags=["admin-equipment"],
        summary="Replace an equipment item",
    ),
    delete=extend_schema(
        tags=["admin-equipment"],
        summary="Deactivate an equipment item",
        description=(
            "Deactivates the item and keeps its row for booking history."
        ),
    ),
)
class EquipmentAdminDetailAPIView(RetrieveUpdateDestroyAPIView):
    """Administrative retrieve, update and deactivate endpoint for one item."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [CanManageCatalogue]
    serializer_class = EquipmentAdminSerializer
    lookup_url_kwarg = "equipment_id"
    queryset = Equipment.objects.all()

    def perform_update(self, serializer):
        try:
            serializer.save()
        except CloudinaryError as exc:
            raise image_service_error(exc) from exc

    def perform_destroy(self, instance):
        services.deactivate_equipment(equipment=instance)


class EquipmentImageAPIView(APIView):
    """Upload, replace or remove the image of one equipment item.

    This endpoint is retained for backwards compatibility. The administrative
    create/update endpoints also accept image operations via the serializer.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsOwnerOrManager]

    @staticmethod
    def _get_equipment(equipment_id):
        return get_object_or_404(Equipment, pk=equipment_id)

    @extend_schema(
        tags=["equipment"],
        summary="Upload or replace an equipment image",
        request=EquipmentImageUploadSerializer,
        responses={200: EquipmentImageSerializer},
    )
    def post(self, request, equipment_id):
        return self._save(request, equipment_id)

    @extend_schema(
        tags=["equipment"],
        summary="Replace an equipment image",
        request=EquipmentImageUploadSerializer,
        responses={200: EquipmentImageSerializer},
    )
    def put(self, request, equipment_id):
        return self._save(request, equipment_id)

    def _save(self, request, equipment_id):
        equipment = self._get_equipment(equipment_id)
        serializer = EquipmentImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            equipment = services.set_equipment_image(
                equipment=equipment,
                image=serializer.validated_data["image"],
            )
        except CloudinaryError as exc:
            raise image_service_error(exc) from exc

        return Response(EquipmentImageSerializer(equipment).data)

    @extend_schema(
        tags=["equipment"],
        summary="Remove an equipment image",
        responses={204: None},
    )
    def delete(self, request, equipment_id):
        equipment = self._get_equipment(equipment_id)

        try:
            services.remove_equipment_image(equipment=equipment)
        except CloudinaryError as exc:
            raise image_service_error(exc) from exc

        return Response(status=status.HTTP_204_NO_CONTENT)