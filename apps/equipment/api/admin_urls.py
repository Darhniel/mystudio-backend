"""Administrative equipment routes, mounted under /api/v1/admin/equipment/."""

from django.urls import path

from apps.equipment.api.views import (
    EquipmentAdminDetailAPIView,
    EquipmentAdminListCreateAPIView,
)

app_name = "equipment-admin"

urlpatterns = [
    path("", EquipmentAdminListCreateAPIView.as_view(), name="equipment-list"),
    path(
        "<uuid:equipment_id>/",
        EquipmentAdminDetailAPIView.as_view(),
        name="equipment-detail",
    ),
]
