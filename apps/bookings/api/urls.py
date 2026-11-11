"""URL routes for Bookings, mounted under /api/v1/bookings/."""

from django.urls import path

from apps.bookings.api.views import (
    AdminBookingCancelAPIView,
    AdminBookingDetailAPIView,
    AdminBookingListAPIView,
    AdminBlockedSlotDetailAPIView,
    AdminBlockedSlotListCreateAPIView,
    PublicBookingCreateAPIView,
    PublicBookingRetrieveAPIView,
    PublicStudioAvailabilityAPIView,
)

app_name = "bookings"

urlpatterns = [
    # Public endpoints
    path(
        "studios/<slug:slug>/availability/",
        PublicStudioAvailabilityAPIView.as_view(),
        name="studio-availability",
    ),
    path("", PublicBookingCreateAPIView.as_view(), name="booking-create"),
    # Administrative endpoints
    path("admin/", AdminBookingListAPIView.as_view(), name="admin-booking-list"),
    path(
        "admin/<uuid:booking_id>/",
        AdminBookingDetailAPIView.as_view(),
        name="admin-booking-detail",
    ),
    path(
        "admin/<uuid:booking_id>/cancel/",
        AdminBookingCancelAPIView.as_view(),
        name="admin-booking-cancel",
    ),
    path(
        "admin/blocked-slots/",
        AdminBlockedSlotListCreateAPIView.as_view(),
        name="admin-blocked-slot-list",
    ),
    path(
        "admin/blocked-slots/<uuid:slot_id>/",
        AdminBlockedSlotDetailAPIView.as_view(),
        name="admin-blocked-slot-detail",
    ),
    path(
        "<str:reference>/",
        PublicBookingRetrieveAPIView.as_view(),
        name="booking-retrieve",
    ),
]
