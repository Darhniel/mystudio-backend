"""URL routes for Equipment, mounted under /api/v1/equipment/."""

from django.urls import path

from apps.equipment.api.views import EquipmentImageAPIView, EquipmentListAPIView

app_name = "equipment"

urlpatterns = [
    path("", EquipmentListAPIView.as_view(), name="equipment-list"),
    path(
        "<uuid:equipment_id>/image/",
        EquipmentImageAPIView.as_view(),
        name="equipment-image",
    ),
]
