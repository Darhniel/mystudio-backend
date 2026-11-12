"""Read queries for bookings and blocked slots.

These functions are pure: they take inputs, return data, and never mutate state.
They are used by the service layer for validation and by the API layer for
public availability endpoints.
"""

from datetime import datetime, time, timedelta

from django.db import models
from django.utils import timezone

from apps.bookings.models import BlockedSlot, Booking, BookingEquipment, BookingStatus
from apps.studios.models import Studio

STUDIO_OPEN_HOUR = 9
STUDIO_CLOSE_HOUR = 18


def get_studio_availability(
    *, studio: Studio, date: datetime.date, duration_hours: int = 1
) -> list[str]:
    """Return a list of available start times (HH:MM) for a given date and duration.

    Operating hours are 09:00 to 18:00. A slot is available if:
    1. The requested duration fits within operating hours (start + duration <= 18:00).
    2. No confirmed or unexpired pending booking overlaps with the requested period.
    3. No administrative block covers the requested period.

    Args:
        studio: The studio to check.
        date: The date to check availability for.
        duration_hours: The requested booking duration in hours (default: 1).

    Returns:
        A list of available start times in "HH:MM" format.
    """
    available_slots = []
    tz = timezone.get_current_timezone()

    # Calculate the latest possible start time that fits the duration
    latest_start_hour = STUDIO_CLOSE_HOUR - duration_hours
    if latest_start_hour < STUDIO_OPEN_HOUR:
        # Duration is too long to fit in operating hours
        return []

    for hour in range(STUDIO_OPEN_HOUR, latest_start_hour + 1):
        slot_start = timezone.make_aware(datetime.combine(date, time(hour, 0)), tz)
        slot_end = slot_start + timedelta(hours=duration_hours)

        has_conflict = (
            Booking.objects.filter(
                studio=studio,
                status__in=[BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED],
                start_time__lt=slot_end,
                end_time__gt=slot_start,
            )
            .filter(
                # Only consider unexpired pending bookings
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

        if not has_conflict:
            available_slots.append(f"{hour:02d}:00")

    return available_slots


def get_overlapping_bookings(
    *, studio: Studio, start_time: datetime, end_time: datetime, exclude_booking_id=None
):
    """Return bookings that overlap with the given time range.

    Only considers bookings that hold inventory: confirmed or unexpired pending.
    Uses the overlap condition: new_start < existing_end AND new_end > existing_start.
    """
    now = timezone.now()
    overlapping_bookings = Booking.objects.filter(
        studio=studio,
        start_time__lt=end_time,
        end_time__gt=start_time,
    ).filter(
        models.Q(status=BookingStatus.CONFIRMED)
        | models.Q(
            status=BookingStatus.PENDING_PAYMENT,
            payment_hold_expires_at__gt=now,
        )
    )
    if exclude_booking_id is not None:
        overlapping_bookings = overlapping_bookings.exclude(id=exclude_booking_id)
    return overlapping_bookings


def get_overlapping_blocks(*, studio: Studio, start_time: datetime, end_time: datetime):
    """Return blocked slots that overlap with the given time range.

    Uses the overlap condition: new_start < existing_end AND new_end > existing_start.
    """
    return BlockedSlot.objects.filter(
        studio=studio,
        start_time__lt=end_time,
        end_time__gt=start_time,
    )


def get_booked_equipment_quantities(
    *,
    equipment_ids: list,
    start_time: datetime,
    end_time: datetime,
    exclude_booking_id=None,
) -> dict:
    """Return a dict of equipment_id -> total booked quantity for the time range.

    Only counts confirmed bookings and unexpired pending holds. Expired or
    cancelled bookings do not reserve stock.
    """
    now = timezone.now()
    equipment_bookings = BookingEquipment.objects.filter(
        equipment_id__in=equipment_ids,
        booking__start_time__lt=end_time,
        booking__end_time__gt=start_time,
    )
    if exclude_booking_id is not None:
        equipment_bookings = equipment_bookings.exclude(
            booking_id=exclude_booking_id
        )

    lines = (
        equipment_bookings
        .filter(
            models.Q(booking__status=BookingStatus.CONFIRMED)
            | models.Q(
                booking__status=BookingStatus.PENDING_PAYMENT,
                booking__payment_hold_expires_at__gt=now,
            )
        )
        .values("equipment_id")
        .annotate(total=models.Sum("quantity"))
    )

    return {str(line["equipment_id"]): line["total"] or 0 for line in lines}
