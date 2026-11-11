"""Tests for equipment inventory availability calculations.

Equipment is a global pool shared across all studios. When a customer books
equipment, it reserves units from that pool for the booking's time window.
The availability calculation must account for confirmed bookings and unexpired
pending payment holds, but not expired pending bookings.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import Booking, BookingEquipment, BookingStatus
from apps.bookings.selectors import get_booked_equipment_quantities
from apps.bookings.services import create_booking
from apps.bookings.utils import make_aware_datetime
from apps.equipment.models import Equipment
from apps.studios.tests.factories import make_studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


@pytest.fixture
def equipment_stock():
    """Create equipment with stock_quantity = 10."""
    return Equipment.objects.create(
        name="LED Light",
        description="Professional LED light",
        fixed_price_kobo=500_000,  # ₦5,000
        stock_quantity=10,
    )


@pytest.fixture
def equipment_tripod():
    """Create a second equipment item for multi-item tests."""
    return Equipment.objects.create(
        name="Tripod",
        description="Professional tripod",
        fixed_price_kobo=200_000,  # ₦2,000
        stock_quantity=5,
    )


# ---------------------------------------------------------------------------
# Available stock calculation
# ---------------------------------------------------------------------------
def test_available_stock_no_bookings(equipment_stock, tomorrow):
    """All stock is available when no bookings exist."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booked = get_booked_equipment_quantities(
        equipment_ids=[str(equipment_stock.id)],
        start_time=start,
        end_time=end,
    )

    assert booked.get(str(equipment_stock.id), 0) == 0


def test_available_stock_with_confirmed_reservations(equipment_stock, tomorrow):
    """Confirmed bookings reduce available stock."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    studio = make_studio(name="Confirmed reservation studio")
    booking1 = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 3}],
        customer_full_name="User A",
        customer_email="a@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )
    booking1.status = BookingStatus.CONFIRMED
    booking1.save(update_fields=["status", "updated_at"])

    booked = get_booked_equipment_quantities(
        equipment_ids=[str(equipment_stock.id)],
        start_time=start,
        end_time=end,
    )

    assert booked[str(equipment_stock.id)] == 3


def test_available_stock_with_pending_reservations(equipment_stock, tomorrow):
    """Unexpired pending bookings reduce available stock."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    studio = make_studio(name="Pending reservation studio")

    # Create pending booking with 4 units
    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 4}],
        customer_full_name="User B",
        customer_email="b@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )
    # Status is PENDING_PAYMENT by default

    booked = get_booked_equipment_quantities(
        equipment_ids=[str(equipment_stock.id)],
        start_time=start,
        end_time=end,
    )

    assert booked[str(equipment_stock.id)] == 4


def test_expired_pending_reservations_do_not_reserve(equipment_stock, tomorrow):
    """Expired pending bookings do not reduce available stock."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    studio = make_studio(name="Combined confirmed reservation studio")
    pending_studio = make_studio(name="Combined pending reservation studio")

    # Create pending booking with 4 units
    booking = create_booking(
        studio=pending_studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 4}],
        customer_full_name="User B",
        customer_email="b@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    # Expire it
    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["payment_hold_expires_at", "updated_at"])

    booked = get_booked_equipment_quantities(
        equipment_ids=[str(equipment_stock.id)],
        start_time=start,
        end_time=end,
    )

    # Expired booking should not count
    assert booked.get(str(equipment_stock.id), 0) == 0


def test_combined_confirmed_and_pending_reservations(equipment_stock, tomorrow):
    """Both confirmed and pending reservations reduce available stock."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    studio = make_studio(name="Combined confirmed reservation studio")
    pending_studio = make_studio(name="Combined pending reservation studio")

    # Create confirmed booking with 3 units
    booking1 = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 3}],
        customer_full_name="User A",
        customer_email="a@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )
    booking1.status = BookingStatus.CONFIRMED
    booking1.save(update_fields=["status", "updated_at"])

    # Create pending booking with 4 units
    booking2 = create_booking(
        studio=pending_studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 4}],
        customer_full_name="User B",
        customer_email="b@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    booked = get_booked_equipment_quantities(
        equipment_ids=[str(equipment_stock.id)],
        start_time=start,
        end_time=end,
    )

    assert booked[str(equipment_stock.id)] == 7  # 3 + 4


# ---------------------------------------------------------------------------
# Insufficient stock
# ---------------------------------------------------------------------------
def test_insufficient_stock_rejected(equipment_stock, tomorrow):
    """Requesting more than available stock is rejected."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    studio = make_studio(name="Insufficient stock first studio")
    pending_studio = make_studio(name="Insufficient stock second studio")
    attempted_studio = make_studio(name="Insufficient stock attempted studio")

    # Reserve 7 units (3 confirmed + 4 pending)
    booking1 = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 3}],
        customer_full_name="User A",
        customer_email="a@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )
    booking1.status = BookingStatus.CONFIRMED
    booking1.save(update_fields=["status", "updated_at"])

    booking2 = create_booking(
        studio=pending_studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 4}],
        customer_full_name="User B",
        customer_email="b@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    # Try to book 5 more (only 3 available: 10 - 7 = 3)
    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=attempted_studio,
            start_time=start,
            end_time=end,
            equipment_items=[{"id": str(equipment_stock.id), "quantity": 5}],
            customer_full_name="User C",
            customer_email="c@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )

    assert "equipment" in exc_info.value.message_dict
    assert "Only 3 units" in str(exc_info.value)


def test_exact_remaining_stock_allowed(equipment_stock, tomorrow):
    """Requesting exactly the remaining stock is allowed."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    studio = make_studio(name="Exact stock first studio")
    remaining_stock_studio = make_studio(name="Exact stock second studio")

    # Reserve 7 units
    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 7}],
        customer_full_name="User A",
        customer_email="a@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    # Book remaining 3 units (should succeed)
    booking2 = create_booking(
        studio=remaining_stock_studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 3}],
        customer_full_name="User B",
        customer_email="b@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    assert booking2.equipment_items.first().quantity == 3


# ---------------------------------------------------------------------------
# Multiple equipment items
# ---------------------------------------------------------------------------
def test_multiple_equipment_items(equipment_stock, equipment_tripod, tomorrow):
    """Booking multiple equipment items reserves each independently."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    studio = make_studio(name="Multiple equipment studio")

    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[
            {"id": str(equipment_stock.id), "quantity": 2},
            {"id": str(equipment_tripod.id), "quantity": 1},
        ],
        customer_full_name="User A",
        customer_email="a@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    booked = get_booked_equipment_quantities(
        equipment_ids=[str(equipment_stock.id), str(equipment_tripod.id)],
        start_time=start,
        end_time=end,
    )

    assert booked[str(equipment_stock.id)] == 2
    assert booked[str(equipment_tripod.id)] == 1


# ---------------------------------------------------------------------------
# Inactive equipment
# ---------------------------------------------------------------------------
def test_inactive_equipment_rejected(tomorrow):
    """Inactive equipment cannot be booked."""
    equipment = Equipment.objects.create(
        name="Inactive Light",
        fixed_price_kobo=500_000,
        stock_quantity=10,
        is_active=False,
    )

    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    from apps.studios.models import Studio

    studio = Studio.objects.create(
        name="Test Studio",
        hourly_rate_kobo=100_000,
        capacity=5,
    )

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[{"id": str(equipment.id), "quantity": 1}],
            customer_full_name="User A",
            customer_email="a@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )

    assert "equipment" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Invalid quantities
# ---------------------------------------------------------------------------
def test_zero_quantity_rejected(equipment_stock, tomorrow):
    """Zero quantity is rejected."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    from apps.studios.models import Studio

    studio = Studio.objects.create(
        name="Test Studio",
        hourly_rate_kobo=100_000,
        capacity=5,
    )

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[{"id": str(equipment_stock.id), "quantity": 0}],
            customer_full_name="User A",
            customer_email="a@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )

    assert "equipment" in exc_info.value.message_dict


def test_negative_quantity_rejected(equipment_stock, tomorrow):
    """Negative quantity is rejected."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    from apps.studios.models import Studio

    studio = Studio.objects.create(
        name="Test Studio",
        hourly_rate_kobo=100_000,
        capacity=5,
    )

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=start,
            end_time=end,
            equipment_items=[{"id": str(equipment_stock.id), "quantity": -1}],
            customer_full_name="User A",
            customer_email="a@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )

    assert "equipment" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Equipment pricing is fixed per booking
# ---------------------------------------------------------------------------
def test_equipment_price_not_multiplied_by_duration(equipment_stock, tomorrow):
    """Equipment price is fixed per booking, not multiplied by duration."""
    start_1hr = make_aware_datetime(tomorrow, 10)
    end_1hr = make_aware_datetime(tomorrow, 11)

    start_5hr = make_aware_datetime(tomorrow, 10)
    end_5hr = make_aware_datetime(tomorrow, 15)

    studio = make_studio(name="Equipment price first studio")
    longer_booking_studio = make_studio(name="Equipment price second studio")

    # Book for 1 hour
    booking1 = create_booking(
        studio=studio,
        start_time=start_1hr,
        end_time=end_1hr,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 1}],
        customer_full_name="User A",
        customer_email="a@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    # Book for 5 hours
    booking2 = create_booking(
        studio=longer_booking_studio,
        start_time=start_5hr,
        end_time=end_5hr,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 1}],
        customer_full_name="User B",
        customer_email="b@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    # Equipment price should be the same regardless of duration
    assert booking1.equipment_subtotal_kobo == equipment_stock.fixed_price_kobo
    assert booking2.equipment_subtotal_kobo == equipment_stock.fixed_price_kobo


# ---------------------------------------------------------------------------
# Overlapping booking periods
# ---------------------------------------------------------------------------
def test_non_overlapping_periods_do_not_conflict(equipment_stock, tomorrow):
    """Bookings in non-overlapping time periods do not conflict."""
    start1 = make_aware_datetime(tomorrow, 10)
    end1 = make_aware_datetime(tomorrow, 12)

    start2 = make_aware_datetime(tomorrow, 14)
    end2 = make_aware_datetime(tomorrow, 16)

    studio = make_studio(name="Non-overlapping inventory studio")

    # Book 10:00-12:00 with 8 units
    booking1 = create_booking(
        studio=studio,
        start_time=start1,
        end_time=end1,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 8}],
        customer_full_name="User A",
        customer_email="a@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    # Book 14:00-16:00 with 5 units (should succeed, no overlap)
    booking2 = create_booking(
        studio=studio,
        start_time=start2,
        end_time=end2,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 5}],
        customer_full_name="User B",
        customer_email="b@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    assert booking2.equipment_items.first().quantity == 5


def test_overlapping_periods_do_conflict(equipment_stock, tomorrow):
    """Bookings in overlapping time periods share the inventory pool."""
    start1 = make_aware_datetime(tomorrow, 10)
    end1 = make_aware_datetime(tomorrow, 12)

    start2 = make_aware_datetime(tomorrow, 11)
    end2 = make_aware_datetime(tomorrow, 13)

    studio = make_studio(name="Overlapping inventory first studio")
    overlapping_studio = make_studio(name="Overlapping inventory second studio")

    # Book 10:00-12:00 with 8 units
    booking1 = create_booking(
        studio=studio,
        start_time=start1,
        end_time=end1,
        equipment_items=[{"id": str(equipment_stock.id), "quantity": 8}],
        customer_full_name="User A",
        customer_email="a@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    # Try to book 11:00-13:00 with 5 units (should fail, only 2 available)
    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=overlapping_studio,
            start_time=start2,
            end_time=end2,
            equipment_items=[{"id": str(equipment_stock.id), "quantity": 5}],
            customer_full_name="User B",
            customer_email="b@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )

    assert "equipment" in exc_info.value.message_dict
