"""API views for bookings and blocked slots."""

import secrets
from datetime import datetime, time, timedelta

from django.db import models
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes

from apps.accounts.api.permissions import IsOwnerOrManager
from apps.bookings import selectors, services
from apps.bookings.api.serializers import (
    AdminBookingSerializer,
    BlockedSlotSerializer,
    BookingCreateSerializer,
    BookingResponseSerializer,
)
from apps.bookings.models import BlockedSlot, Booking, BookingStatus
from apps.studios.models import Studio


@extend_schema(
    parameters=[
        OpenApiParameter(
            name="date",
            description="YYYY-MM-DD",
            required=True,
            type=OpenApiTypes.DATE,
        ),
        OpenApiParameter(
            name="duration_hours",
            description="Integer between 1 and 9",
            required=False,
            type=OpenApiTypes.INT,
        ),
    ],
    responses={200: OpenApiTypes.OBJECT},
    auth=[],
)
class PublicStudioAvailabilityAPIView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    lookup_field = "slug"
    serializer_class = None

    def get(self, request, slug, *args, **kwargs):
        try:
            studio = Studio.objects.get(slug=slug, is_active=True)
        except Studio.DoesNotExist:
            return Response(
                {"detail": "Studio not found or inactive."},
                status=status.HTTP_404_NOT_FOUND,
            )

        date_str = request.query_params.get("date")
        duration_str = request.query_params.get("duration_hours", "1")

        if not date_str:
            return Response(
                {"detail": "Date query parameter is required (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"detail": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            duration_hours = int(duration_str)
            if duration_hours < 1 or duration_hours > 9:
                raise ValueError()
        except ValueError:
            return Response(
                {"detail": "duration_hours must be an integer between 1 and 9."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tz = timezone.get_current_timezone()
        latest_start_hour = 18 - duration_hours
        results = []

        for hour in range(9, latest_start_hour + 1):
            slot_start = timezone.make_aware(
                datetime.combine(target_date, time(hour, 0)), tz
            )
            slot_end = slot_start + timedelta(hours=duration_hours)

            has_conflict = (
                Booking.objects.filter(
                    studio=studio,
                    start_time__lt=slot_end,
                    end_time__gt=slot_start,
                )
                .filter(
                    models.Q(status=BookingStatus.CONFIRMED)
                    | models.Q(
                        status=BookingStatus.PENDING_PAYMENT,
                        payment_hold_expires_at__gt=timezone.now(),
                    )
                )
                .exists()
                or BlockedSlot.objects.filter(
                    studio=studio,
                    start_time__lt=slot_end,
                    end_time__gt=slot_start,
                ).exists()
            )

            results.append(
                {
                    "start_time": f"{hour:02d}:00",
                    "end_time": f"{hour + duration_hours:02d}:00",
                    "available": not has_conflict,
                }
            )

        return Response(
            {"date": date_str, "duration_hours": duration_hours, "slots": results}
        )


@extend_schema(auth=[])
class PublicBookingCreateAPIView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = BookingCreateSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        response_serializer = BookingResponseSerializer(booking)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(
    parameters=[
        OpenApiParameter(
            name="token",
            description="High-entropy access token",
            required=True,
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
        ),
    ],
    auth=[],
)
class PublicBookingRetrieveAPIView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = BookingResponseSerializer
    lookup_field = "reference"
    lookup_url_kwarg = "reference"

    def get_queryset(self):
        return Booking.objects.select_related("studio").prefetch_related(
            "equipment_items__equipment"
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        token = request.query_params.get("token")

        if not token or not secrets.compare_digest(token, instance.access_token):
            return Response(
                {"detail": "Invalid or missing access token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class AdminBookingListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    serializer_class = AdminBookingSerializer
    queryset = Booking.objects.select_related("studio").order_by("-created_at")


class AdminBookingDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    serializer_class = AdminBookingSerializer
    queryset = Booking.objects.select_related("studio")
    lookup_field = "id"
    lookup_url_kwarg = "booking_id"


class AdminBookingCancelAPIView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    serializer_class = AdminBookingSerializer
    queryset = Booking.objects.select_related("studio")
    lookup_field = "id"
    lookup_url_kwarg = "booking_id"

    def post(self, request, *args, **kwargs):
        booking = self.get_object()
        services.cancel_booking(
            booking=booking,
            reason=request.data.get("reason", ""),
            cancelled_by=request.user,
        )
        return Response(self.get_serializer(booking).data)


class AdminBlockedSlotListCreateAPIView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    serializer_class = BlockedSlotSerializer
    queryset = BlockedSlot.objects.select_related("studio", "created_by").order_by(
        "-start_time"
    )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class AdminBlockedSlotDetailAPIView(generics.RetrieveDestroyAPIView):
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    serializer_class = BlockedSlotSerializer
    queryset = BlockedSlot.objects.select_related("studio", "created_by")
    lookup_field = "id"
    lookup_url_kwarg = "slot_id"
