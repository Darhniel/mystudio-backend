"""Tests for booking hold expiration.

These tests verify that:
1. Expired pending bookings are marked as EXPIRED.
2. Expired bookings no longer block studio availability.
3. Expired bookings no longer reserve equipment stock.
4. The expiration process is idempotent (safe to run repeatedly).
5. CONFIRMED, COMPLETED, and CANCELLED bookings are not affected.
6. Batch processing works correctly.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.selectors import (
    get_booked_equipment_quantities,
    get_studio_availability,
)
from apps.bookings.services import create_booking, expire_pending_bookings
from apps.bookings.utils import make_aware_datetime
from apps.equipment.models import Equipment
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def expiration_studio():
    return Studio.objects.create(
        name="Expiration Test Studio",
        hourly_rate_kobo=100_000,
        capacity=10,
    )


@pytest.fixture
def expiration_equipment():
    return Equipment.objects.create(
        name="Expiration Test Light",
        fixed_price_kobo=50_000,
        stock_quantity=5,
    )


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


# ---------------------------------------------------------------------------
# Basic expiration
# ---------------------------------------------------------------------------
def test_expired_booking_is_marked_as_expired(expiration_studio, tomorrow):
    """Expired pending bookings are marked as EXPIRED."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    assert booking.status == BookingStatus.PENDING_PAYMENT

    # Expire it
    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["payment_hold_expires_at", "updated_at"])

    count = expire_pending_bookings()

    assert count == 1

    booking.refresh_from_db()
    assert booking.status == BookingStatus.EXPIRED


def test_unexpired_pending_booking_is_not_expired(expiration_studio, tomorrow):
    """Unexpired pending bookings remain PENDING_PAYMENT."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    # payment_hold_expires_at is in the future (set by create_booking)
    count = expire_pending_bookings()

    assert count == 0

    booking.refresh_from_db()
    assert booking.status == BookingStatus.PENDING_PAYMENT


# ---------------------------------------------------------------------------
# Expired bookings don't block availability
# ---------------------------------------------------------------------------
def test_expired_booking_does_not_block_studio_availability(
    expiration_studio, tomorrow
):
    """Expired bookings no longer block studio availability."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    # Create and expire a booking
    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["payment_hold_expires_at", "updated_at"])
    expire_pending_bookings()

    # Check availability
    available = get_studio_availability(
        studio=expiration_studio, date=tomorrow, duration_hours=2
    )

    # 10:00 should be available again
    assert "10:00" in available


def test_expired_booking_does_not_reserve_equipment(
    expiration_studio, expiration_equipment, tomorrow
):
    """Expired bookings no longer reserve equipment stock."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    # Create and expire a booking with equipment
    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(expiration_equipment.id), "quantity": 3}],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["payment_hold_expires_at", "updated_at"])
    expire_pending_bookings()

    # Check equipment availability
    booked = get_booked_equipment_quantities(
        equipment_ids=[str(expiration_equipment.id)],
        start_time=start,
        end_time=end,
    )

    # Expired booking should not reserve equipment
    assert booked.get(str(expiration_equipment.id), 0) == 0


# ---------------------------------------------------------------------------
# Idempotency (safe to run repeatedly)
# ---------------------------------------------------------------------------
def test_expiration_is_idempotent(expiration_studio, tomorrow):
    """Running expiration multiple times does not cause errors."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["payment_hold_expires_at", "updated_at"])

    # Run expiration multiple times
    count1 = expire_pending_bookings()
    count2 = expire_pending_bookings()
    count3 = expire_pending_bookings()

    # Only the first run should expire the booking
    assert count1 == 1
    assert count2 == 0
    assert count3 == 0

    booking.refresh_from_db()
    assert booking.status == BookingStatus.EXPIRED


# ---------------------------------------------------------------------------
# Preservation of other statuses
# ---------------------------------------------------------------------------
def test_confirmed_bookings_are_not_expired(expiration_studio, tomorrow):
    """CONFIRMED bookings are not affected by expiration."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    booking.status = BookingStatus.CONFIRMED
    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["status", "payment_hold_expires_at", "updated_at"])

    count = expire_pending_bookings()

    assert count == 0

    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED


def test_completed_bookings_are_not_expired(expiration_studio, tomorrow):
    """COMPLETED bookings are not affected by expiration."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    booking.status = BookingStatus.COMPLETED
    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["status", "payment_hold_expires_at", "updated_at"])

    count = expire_pending_bookings()

    assert count == 0

    booking.refresh_from_db()
    assert booking.status == BookingStatus.COMPLETED


def test_cancelled_bookings_are_not_expired(expiration_studio, tomorrow):
    """CANCELLED bookings are not affected by expiration."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    booking.status = BookingStatus.CANCELLED
    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["status", "payment_hold_expires_at", "updated_at"])

    count = expire_pending_bookings()

    assert count == 0

    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------
def test_batch_processing(expiration_studio, tomorrow):
    """Expiration processes bookings in batches."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    # Create multiple expired bookings
    for i in range(5):
        booking = create_booking(
            studio=expiration_studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name=f"User {i}",
            customer_email=f"user{i}@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )
        booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
        booking.save(update_fields=["payment_hold_expires_at", "updated_at"])

    # Process with batch size of 2
    count = expire_pending_bookings(batch_size=2)

    assert count == 5

    # Verify all are expired
    assert Booking.objects.filter(status=BookingStatus.EXPIRED).count() == 5


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------
def test_management_command_expires_bookings(expiration_studio, tomorrow, capsys):
    """The management command expires bookings and outputs the count."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )
    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["payment_hold_expires_at", "updated_at"])

    # Call the management command
    call_command("expire_pending_bookings")

    captured = capsys.readouterr()
    assert "Successfully expired 1 pending bookings" in captured.out

    booking.refresh_from_db()
    assert booking.status == BookingStatus.EXPIRED


def test_management_command_with_custom_batch_size(expiration_studio, tomorrow, capsys):
    """The management command accepts a custom batch size."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    # Create multiple expired bookings
    for i in range(3):
        booking = create_booking(
            studio=expiration_studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name=f"User {i}",
            customer_email=f"user{i}@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )
        booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
        booking.save(update_fields=["payment_hold_expires_at", "updated_at"])

    # Call with custom batch size
    call_command("expire_pending_bookings", batch_size=1)

    captured = capsys.readouterr()
    assert "Successfully expired 3 pending bookings" in captured.out


# ---------------------------------------------------------------------------
# Timing edge cases
# ---------------------------------------------------------------------------
def test_booking_expiring_exactly_now_is_expired(expiration_studio, tomorrow):
    """A booking with expires_at exactly equal to now is expired."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    # Set expires_at to exactly now
    now = timezone.now()
    booking.payment_hold_expires_at = now
    booking.save(update_fields=["payment_hold_expires_at", "updated_at"])

    count = expire_pending_bookings()

    assert count == 1

    booking.refresh_from_db()
    assert booking.status == BookingStatus.EXPIRED


def test_booking_expiring_one_second_in_future_is_not_expired(
    expiration_studio, tomorrow
):
    """A booking with expires_at one second in the future is not expired."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = create_booking(
        studio=expiration_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    # Set expires_at to one second in the future
    booking.payment_hold_expires_at = timezone.now() + timedelta(seconds=1)
    booking.save(update_fields=["payment_hold_expires_at", "updated_at"])

    count = expire_pending_bookings()

    assert count == 0

    booking.refresh_from_db()
    assert booking.status == BookingStatus.PENDING_PAYMENT
