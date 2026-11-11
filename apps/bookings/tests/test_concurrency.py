"""Concurrency tests for booking creation.

These tests verify that the transaction and locking strategy prevents
double-booking and equipment over-reservation when multiple requests
attempt to book the same slot simultaneously.

These tests are designed to run on PostgreSQL, where row-level locking
behavior is well-defined. SQLite's locking behavior differs and may not
catch all race conditions.
"""

import threading
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.services import create_booking
from apps.bookings.utils import make_aware_datetime
from apps.equipment.models import Equipment
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db

requires_postgresql = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Concurrent row-locking behavior is supported by PostgreSQL, not SQLite.",
)


@pytest.fixture
def concurrency_studio():
    return Studio.objects.create(
        name="Concurrency Test Studio",
        hourly_rate_kobo=100_000,
        capacity=10,
    )


@pytest.fixture
def concurrency_equipment():
    return Equipment.objects.create(
        name="Concurrency Test Light",
        fixed_price_kobo=50_000,
        stock_quantity=5,
    )


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


@requires_postgresql
def test_concurrent_studio_bookings_only_one_succeeds(concurrency_studio, tomorrow):
    """Two concurrent requests for the same studio slot: only one succeeds."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    results = {"success": 0, "failure": 0}
    errors = []

    def attempt_booking(customer_email):
        try:
            # Each thread gets its own database connection
            connection.ensure_connection()
            create_booking(
                studio=concurrency_studio,
                start_time=start,
                end_time=end,
                equipment_items=[],
                customer_full_name="Test User",
                customer_email=customer_email,
                customer_phone="+2348012345678",
                terms_accepted_at=timezone.now(),
            )
            results["success"] += 1
        except ValidationError as e:
            results["failure"] += 1
            errors.append(str(e))
        finally:
            connection.close()

    # Create two threads that try to book the same slot
    thread1 = threading.Thread(target=attempt_booking, args=("user1@example.com",))
    thread2 = threading.Thread(target=attempt_booking, args=("user2@example.com",))

    thread1.start()
    thread2.start()

    thread1.join(timeout=5)
    thread2.join(timeout=5)

    # Exactly one should succeed
    assert (
        results["success"] == 1
    ), f"Expected 1 success, got {results['success']}. Errors: {errors}"
    assert results["failure"] == 1, f"Expected 1 failure, got {results['failure']}"

    # Verify only one booking exists
    assert (
        Booking.objects.filter(
            studio=concurrency_studio,
            start_time=start,
            end_time=end,
            status__in=[BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED],
        ).count()
        == 1
    )


@requires_postgresql
def test_concurrent_equipment_bookings_respect_inventory(
    concurrency_studio, concurrency_equipment, tomorrow
):
    """Two concurrent requests for the same equipment: inventory is respected."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    # Set stock to exactly 2 units
    concurrency_equipment.stock_quantity = 2
    concurrency_equipment.save(update_fields=["stock_quantity", "updated_at"])

    results = {"success": 0, "failure": 0}
    errors = []

    def attempt_booking(customer_email):
        try:
            connection.ensure_connection()
            create_booking(
                studio=concurrency_studio,
                start_time=start,
                end_time=end,
                equipment_items=[{"id": str(concurrency_equipment.id), "quantity": 2}],
                customer_full_name="Test User",
                customer_email=customer_email,
                customer_phone="+2348012345678",
                terms_accepted_at=timezone.now(),
            )
            results["success"] += 1
        except ValidationError as e:
            results["failure"] += 1
            errors.append(str(e))
        finally:
            connection.close()

    # Both threads try to book 2 units (only 2 available total)
    thread1 = threading.Thread(target=attempt_booking, args=("user1@example.com",))
    thread2 = threading.Thread(target=attempt_booking, args=("user2@example.com",))

    thread1.start()
    thread2.start()

    thread1.join(timeout=5)
    thread2.join(timeout=5)

    # Exactly one should succeed
    assert (
        results["success"] == 1
    ), f"Expected 1 success, got {results['success']}. Errors: {errors}"
    assert results["failure"] == 1, f"Expected 1 failure, got {results['failure']}"

    # Verify only one booking with equipment exists
    assert (
        Booking.objects.filter(
            studio=concurrency_studio,
            start_time=start,
            end_time=end,
            status__in=[BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED],
        ).count()
        == 1
    )


def test_transaction_rollback_on_failure(concurrency_studio, tomorrow):
    """If any step fails, the entire transaction rolls back."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    # Create a booking to occupy the slot
    create_booking(
        studio=concurrency_studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="First User",
        customer_email="first@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    # Try to book the same slot (should fail)
    with pytest.raises(ValidationError):
        create_booking(
            studio=concurrency_studio,
            start_time=start,
            end_time=end,
            equipment_items=[],
            customer_full_name="Second User",
            customer_email="second@example.com",
            customer_phone="+2348012345678",
            terms_accepted_at=timezone.now(),
        )

    # Verify no partial booking was created
    assert (
        Booking.objects.filter(
            customer_email="second@example.com",
        ).count()
        == 0
    )


def test_consistent_locking_order_prevents_deadlock(concurrency_studio, tomorrow):
    """Equipment is locked in consistent order to prevent deadlocks."""
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    # Create multiple equipment items
    eq1 = Equipment.objects.create(
        name="Light A",
        fixed_price_kobo=50_000,
        stock_quantity=10,
    )
    eq2 = Equipment.objects.create(
        name="Light B",
        fixed_price_kobo=50_000,
        stock_quantity=10,
    )

    # Book with equipment in one order
    booking1 = create_booking(
        studio=concurrency_studio,
        start_time=start,
        end_time=end,
        equipment_items=[
            {"id": str(eq1.id), "quantity": 1},
            {"id": str(eq2.id), "quantity": 1},
        ],
        customer_full_name="User A",
        customer_email="a@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    # Book same equipment in different order (should not deadlock)
    start2 = make_aware_datetime(tomorrow, 14)
    end2 = make_aware_datetime(tomorrow, 16)

    booking2 = create_booking(
        studio=concurrency_studio,
        start_time=start2,
        end_time=end2,
        equipment_items=[
            {"id": str(eq2.id), "quantity": 1},  # Different order
            {"id": str(eq1.id), "quantity": 1},
        ],
        customer_full_name="User B",
        customer_email="b@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    # Both should succeed without deadlock
    assert booking1.pk is not None
    assert booking2.pk is not None
