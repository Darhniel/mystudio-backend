"""Comprehensive tests for the studio availability engine.

Tests cover all business rules: operating hours, duration limits, overlap detection,
expired pending bookings, blocked slots, and duration-aware availability.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import BlockedSlot, Booking, BookingStatus
from apps.bookings.selectors import get_studio_availability
from apps.bookings.services import create_booking, create_blocked_slot
from apps.bookings.utils import make_aware_datetime

pytestmark = pytest.mark.django_db


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


# ---------------------------------------------------------------------------
# Operating hours validation
# ---------------------------------------------------------------------------
def test_valid_09_00_start(studio, tomorrow):
    """09:00 to 10:00 is valid."""
    start = make_aware_datetime(tomorrow, 9)
    end = make_aware_datetime(tomorrow, 10)

    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    assert booking.start_time.hour == 9
    assert booking.end_time.hour == 10


def test_valid_18_00_end(studio, tomorrow):
    """17:00 to 18:00 is valid."""
    start = make_aware_datetime(tomorrow, 17)
    end = make_aware_datetime(tomorrow, 18)

    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    assert booking.start_time.hour == 17
    assert booking.end_time.hour == 18


def test_invalid_09_30_start(studio, tomorrow):
    """09:30 start is rejected (not on the hour)."""
    start = make_aware_datetime(tomorrow, 9, 30)
    end = make_aware_datetime(tomorrow, 10, 30)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name="Test User",
            customer_email="test@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "start_time" in exc_info.value.message_dict


def test_invalid_08_00_start(studio, tomorrow):
    """08:00 start is rejected (before operating hours)."""
    start = make_aware_datetime(tomorrow, 8)
    end = make_aware_datetime(tomorrow, 9)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name="Test User",
            customer_email="test@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "start_time" in exc_info.value.message_dict


def test_invalid_19_00_end(studio, tomorrow):
    """Booking ending after 18:00 is rejected."""
    start = make_aware_datetime(tomorrow, 17)
    end = make_aware_datetime(tomorrow, 19)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name="Test User",
            customer_email="test@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "end_time" in exc_info.value.message_dict


def test_invalid_18_00_to_19_00(studio, tomorrow):
    """18:00 to 19:00 is rejected (outside operating hours)."""
    start = make_aware_datetime(tomorrow, 18)
    end = make_aware_datetime(tomorrow, 19)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name="Test User",
            customer_email="test@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "start_time" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Duration limits
# ---------------------------------------------------------------------------
def test_minimum_duration_one_hour(studio, tomorrow):
    """Minimum duration is one hour."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 11)

    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    assert booking.duration_hours == 1


def test_maximum_duration_nine_hours(studio, tomorrow):
    """Maximum duration is nine hours (09:00 to 18:00)."""
    start = make_aware_datetime(tomorrow, 9)
    end = make_aware_datetime(tomorrow, 18)

    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    assert booking.duration_hours == 9


def test_duration_exceeding_nine_hours_rejected(studio, tomorrow):
    """Duration exceeding nine hours is rejected."""
    start = make_aware_datetime(tomorrow, 8)
    end = make_aware_datetime(tomorrow, 18)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name="Test User",
            customer_email="test@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "start_time" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Past dates and times
# ---------------------------------------------------------------------------
def test_past_date_rejected(studio):
    """Past dates are rejected."""
    yesterday = timezone.localdate() - timedelta(days=1)
    start = make_aware_datetime(yesterday, 10)
    end = make_aware_datetime(yesterday, 11)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name="Test User",
            customer_email="test@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "start_time" in exc_info.value.message_dict


def test_past_start_time_rejected(studio, tomorrow):
    """Past start times on today's date are rejected."""
    now = timezone.now()
    past_hour = now.hour - 1 if now.hour > 0 else 0
    start = make_aware_datetime(now.date(), past_hour)
    end = make_aware_datetime(now.date(), past_hour + 1)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name="Test User",
            customer_email="test@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "start_time" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Overlapping bookings
# ---------------------------------------------------------------------------
def test_overlapping_confirmed_booking_rejected(studio, tomorrow):
    """Overlapping with a confirmed booking is rejected."""
    # Create first booking: 10:00 to 12:00
    start1 = make_aware_datetime(tomorrow, 10)
    end1 = make_aware_datetime(tomorrow, 12)
    booking1 = create_booking(
        studio=studio,
        start_time=start1,
        end_time=end1,
        equipment_items=[],
        customer_full_name="First User",
        customer_email="first@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    booking1.status = BookingStatus.CONFIRMED
    booking1.save(update_fields=["status", "updated_at"])

    # Try to book 11:00 to 13:00 (overlaps)
    start2 = make_aware_datetime(tomorrow, 11)
    end2 = make_aware_datetime(tomorrow, 13)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start2,
            end_time=end2,
            equipment_items=[],
            customer_full_name="Second User",
            customer_email="second@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "start_time" in exc_info.value.message_dict


def test_contiguous_bookings_allowed(studio, tomorrow):
    """Bookings that end exactly when another starts are allowed."""
    # Create first booking: 10:00 to 12:00
    start1 = make_aware_datetime(tomorrow, 10)
    end1 = make_aware_datetime(tomorrow, 12)
    booking1 = create_booking(
        studio=studio,
        start_time=start1,
        end_time=end1,
        equipment_items=[],
        customer_full_name="First User",
        customer_email="first@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    booking1.status = BookingStatus.CONFIRMED
    booking1.save(update_fields=["status", "updated_at"])

    # Book 12:00 to 14:00 (contiguous, should be allowed)
    start2 = make_aware_datetime(tomorrow, 12)
    end2 = make_aware_datetime(tomorrow, 14)

    booking2 = create_booking(
        studio=studio,
        start_time=start2,
        end_time=end2,
        equipment_items=[],
        customer_full_name="Second User",
        customer_email="second@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    assert booking2.start_time.hour == 12
    assert booking2.end_time.hour == 14


# ---------------------------------------------------------------------------
# Pending payment bookings
# ---------------------------------------------------------------------------
def test_active_pending_payment_hold_blocks_availability(studio, tomorrow):
    """Unexpired pending payment bookings block availability."""
    # Create pending booking: 10:00 to 12:00
    start1 = make_aware_datetime(tomorrow, 10)
    end1 = make_aware_datetime(tomorrow, 12)
    booking1 = create_booking(
        studio=studio,
        start_time=start1,
        end_time=end1,
        equipment_items=[],
        customer_full_name="First User",
        customer_email="first@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    # Status is PENDING_PAYMENT by default, expires_at is set

    # Try to book 11:00 to 13:00 (should be blocked)
    start2 = make_aware_datetime(tomorrow, 11)
    end2 = make_aware_datetime(tomorrow, 13)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start2,
            end_time=end2,
            equipment_items=[],
            customer_full_name="Second User",
            customer_email="second@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "start_time" in exc_info.value.message_dict


def test_expired_pending_booking_does_not_block(studio, tomorrow):
    """Expired pending bookings do not block availability."""
    # Create pending booking: 10:00 to 12:00
    start1 = make_aware_datetime(tomorrow, 10)
    end1 = make_aware_datetime(tomorrow, 12)
    booking1 = create_booking(
        studio=studio,
        start_time=start1,
        end_time=end1,
        equipment_items=[],
        customer_full_name="First User",
        customer_email="first@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    # Expire it
    booking1.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking1.save(update_fields=["payment_hold_expires_at", "updated_at"])

    # Book 10:00 to 12:00 again (should be allowed)
    start2 = make_aware_datetime(tomorrow, 10)
    end2 = make_aware_datetime(tomorrow, 12)

    booking2 = create_booking(
        studio=studio,
        start_time=start2,
        end_time=end2,
        equipment_items=[],
        customer_full_name="Second User",
        customer_email="second@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    assert booking2.start_time.hour == 10


# ---------------------------------------------------------------------------
# Blocked slots
# ---------------------------------------------------------------------------
def test_blocked_slot_blocks_availability(studio, tomorrow):
    """Blocked slots prevent bookings."""
    # Create blocked slot: 10:00 to 12:00
    start_block = make_aware_datetime(tomorrow, 10)
    end_block = make_aware_datetime(tomorrow, 12)
    create_blocked_slot(
        studio=studio,
        start_time=start_block,
        end_time=end_block,
        reason="Maintenance",
    )

    # Try to book 11:00 to 13:00 (should be blocked)
    start_booking = make_aware_datetime(tomorrow, 11)
    end_booking = make_aware_datetime(tomorrow, 13)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start_booking,
            end_time=end_booking,
            equipment_items=[],
            customer_full_name="Test User",
            customer_email="test@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    assert "start_time" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Duration-aware availability
# ---------------------------------------------------------------------------
def test_availability_for_one_hour_duration(studio, tomorrow):
    """Availability check for 1-hour duration returns all available hours."""
    # Block 10:00 to 12:00
    start_block = make_aware_datetime(tomorrow, 10)
    end_block = make_aware_datetime(tomorrow, 12)
    create_blocked_slot(
        studio=studio,
        start_time=start_block,
        end_time=end_block,
        reason="Maintenance",
    )

    available = get_studio_availability(studio=studio, date=tomorrow, duration_hours=1)

    assert "09:00" in available
    assert "10:00" not in available
    assert "11:00" not in available
    assert "12:00" in available
    assert "17:00" in available


def test_availability_for_three_hour_duration(studio, tomorrow):
    """Availability check for 3-hour duration returns only valid start times."""
    # Block 10:00 to 12:00
    start_block = make_aware_datetime(tomorrow, 10)
    end_block = make_aware_datetime(tomorrow, 12)
    create_blocked_slot(
        studio=studio,
        start_time=start_block,
        end_time=end_block,
        reason="Maintenance",
    )

    available = get_studio_availability(studio=studio, date=tomorrow, duration_hours=3)

    # 09:00 to 12:00 would overlap with block, so 09:00 is not available
    assert "09:00" not in available
    # 10:00 to 13:00 would overlap with block
    assert "10:00" not in available
    # 11:00 to 14:00 would overlap with block
    assert "11:00" not in available
    # 12:00 to 15:00 is available
    assert "12:00" in available
    # 15:00 to 18:00 is available
    assert "15:00" in available
    # 16:00 to 19:00 would exceed operating hours
    assert "16:00" not in available


def test_availability_for_nine_hour_duration(studio, tomorrow):
    """Availability check for 9-hour duration returns only 09:00 if available."""
    available = get_studio_availability(studio=studio, date=tomorrow, duration_hours=9)

    # Only 09:00 to 18:00 fits
    assert available == ["09:00"]


def test_availability_for_ten_hour_duration_returns_empty(studio, tomorrow):
    """Availability check for 10-hour duration returns empty (exceeds operating hours)."""
    available = get_studio_availability(studio=studio, date=tomorrow, duration_hours=10)

    assert available == []
