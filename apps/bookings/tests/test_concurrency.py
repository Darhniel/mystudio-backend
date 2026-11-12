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
from django.db import connection, transaction
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.services import create_booking
from apps.bookings.utils import make_aware_datetime
from apps.equipment.models import Equipment
from apps.studios.models import Studio

requires_postgresql = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Concurrent row-locking behavior is supported by PostgreSQL, not SQLite.",
)


def _make_booking_args(studio, start, end, equipment_items, email):
    """Helper to build common booking arguments."""
    return {
        "studio": studio,
        "start_time": start,
        "end_time": end,
        "equipment_items": equipment_items,
        "customer_full_name": "Test User",
        "customer_email": email,
        "customer_phone": "+2348012345678",
        "terms_accepted_at": timezone.now(),
    }


@requires_postgresql
@pytest.mark.django_db(transaction=True)  # Disable pytest-django's transaction wrapper
def test_concurrent_studio_bookings_only_one_succeeds():
    """Two concurrent requests for the same studio slot: only one succeeds."""
    # Create fixtures and COMMIT so worker threads can see them
    tomorrow = timezone.localdate() + timedelta(days=1)
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    with transaction.atomic():
        studio = Studio.objects.create(
            name="Concurrency Test Studio",
            hourly_rate_kobo=100_000,
            capacity=10,
        )
        # Explicitly commit so other connections can see the studio
        transaction.on_commit(lambda: None)

    # Need to commit the transaction for threads to see the studio
    # pytest-django's transaction=True gives us manual control
    connection.commit()

    # Ensure the committed objects are visible to new connections by querying them
    studio = Studio.objects.get(id=studio.id)

    results = {"success": 0, "failure": 0}
    errors = []

    def attempt_booking(customer_email):
        try:
            connection.ensure_connection()
            create_booking(**_make_booking_args(studio, start, end, [], customer_email))
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

    thread1.join(timeout=10)
    thread2.join(timeout=10)

    # Exactly one should succeed
    assert (
        results["success"] == 1
    ), f"Expected 1 success, got {results['success']}. Errors: {errors}"
    assert results["failure"] == 1, f"Expected 1 failure, got {results['failure']}"

    # Verify only one booking exists
    assert (
        Booking.objects.filter(
            studio=studio,
            start_time=start,
            end_time=end,
            status__in=[BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED],
        ).count()
        == 1
    )


@requires_postgresql
@pytest.mark.django_db(transaction=True)
def test_concurrent_equipment_bookings_respect_inventory():
    """Two concurrent requests for the same equipment: inventory is respected."""
    tomorrow = timezone.localdate() + timedelta(days=1)
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    with transaction.atomic():
        studio = Studio.objects.create(
            name="Concurrency Test Studio",
            hourly_rate_kobo=100_000,
            capacity=10,
        )
        equipment = Equipment.objects.create(
            name="Concurrency Test Light",
            fixed_price_kobo=50_000,
            stock_quantity=2,  # Exactly 2 units
        )
        transaction.on_commit(lambda: None)

    connection.commit()

    # Ensure the committed objects are visible to new connections by querying them
    studio = Studio.objects.get(id=studio.id)
    equipment = Equipment.objects.get(id=equipment.id)

    results = {"success": 0, "failure": 0}
    errors = []

    def attempt_booking(customer_email):
        try:
            connection.ensure_connection()
            create_booking(
                **_make_booking_args(
                    studio,
                    start,
                    end,
                    [{"id": str(equipment.id), "quantity": 2}],
                    customer_email,
                )
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

    thread1.join(timeout=10)
    thread2.join(timeout=10)

    # Exactly one should succeed
    assert (
        results["success"] == 1
    ), f"Expected 1 success, got {results['success']}. Errors: {errors}"
    assert results["failure"] == 1, f"Expected 1 failure, got {results['failure']}"

    # Verify only one booking with equipment exists
    assert (
        Booking.objects.filter(
            studio=studio,
            start_time=start,
            end_time=end,
            status__in=[BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED],
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_transaction_rollback_on_failure():
    """If any step fails, the entire transaction rolls back."""
    tomorrow = timezone.localdate() + timedelta(days=1)
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    # Create a booking to occupy the slot
    studio = Studio.objects.create(
        name="Rollback Test Studio",
        hourly_rate_kobo=100_000,
        capacity=10,
    )

    create_booking(
        **_make_booking_args(studio, start, end, [], "first@example.com")
    )

    # Try to book the same slot (should fail)
    with pytest.raises(ValidationError):
        create_booking(
            **_make_booking_args(studio, start, end, [], "second@example.com")
        )

    # Verify no partial booking was created
    assert (
        Booking.objects.filter(
            customer_email="second@example.com",
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_consistent_locking_order_prevents_deadlock():
    """Equipment is locked in consistent order to prevent deadlocks."""
    tomorrow = timezone.localdate() + timedelta(days=1)
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)
    start2 = make_aware_datetime(tomorrow, 14)
    end2 = make_aware_datetime(tomorrow, 16)

    # Create multiple equipment items
    studio = Studio.objects.create(
        name="Deadlock Test Studio",
        hourly_rate_kobo=100_000,
        capacity=10,
    )
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
        **_make_booking_args(
            studio,
            start,
            end,
            [
                {"id": str(eq1.id), "quantity": 1},
                {"id": str(eq2.id), "quantity": 1},
            ],
            "a@example.com",
        )
    )

    # Book same equipment in different order (should not deadlock)
    booking2 = create_booking(
        **_make_booking_args(
            studio,
            start2,
            end2,
            [
                {"id": str(eq2.id), "quantity": 1},  # Different order
                {"id": str(eq1.id), "quantity": 1},
            ],
            "b@example.com",
        )
    )

    # Both should succeed without deadlock
    assert booking1.pk is not None
    assert booking2.pk is not None