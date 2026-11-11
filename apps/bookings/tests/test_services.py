"""Tests for booking services.

These tests verify the business rules enforced by the service layer: operating
hours, price snapshots, inventory protection, and payment hold expiration.
"""

import threading
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.services import (
    cancel_booking,
    create_blocked_slot,
    create_booking,
    expire_pending_bookings,
)
from apps.studios.tests.factories import make_studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def future_slot():
    """A future 10:00-12:00 window in the project timezone (Africa/Lagos)."""
    tomorrow = timezone.localdate() + timedelta(days=1)
    start = timezone.make_aware(
        timezone.datetime.combine(
            tomorrow, timezone.datetime.min.time().replace(hour=10)
        ),
        timezone.get_current_timezone(),
    )
    return start, start + timedelta(hours=2)


def test_create_booking_success(studio, equipment, future_slot):
    start, end = future_slot
    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment.id), "quantity": 1}],
        customer_full_name="Ada Obi",
        customer_email="ada@example.com",
        customer_phone="+2348012345678",
        terms_accepted_at=timezone.now(),
    )

    assert booking.status == BookingStatus.PENDING_PAYMENT
    assert booking.studio_hourly_price_kobo == studio.hourly_rate_kobo
    assert booking.studio_subtotal_kobo == studio.hourly_rate_kobo * 2
    assert booking.equipment_subtotal_kobo == equipment.fixed_price_kobo
    assert (
        booking.total_amount_kobo
        == booking.studio_subtotal_kobo + booking.equipment_subtotal_kobo
    )
    assert booking.payment_hold_expires_at is not None
    assert booking.equipment_items.count() == 1


def test_create_booking_rejects_past_time(studio, future_slot):
    start, end = future_slot
    past_start = timezone.now() - timedelta(hours=1)
    past_end = past_start + timedelta(hours=1)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=past_start,
            end_time=past_end,
            equipment_items=[],
            customer_full_name="Test",
            customer_email="test@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )
    assert "start_time" in exc_info.value.message_dict


def test_create_booking_rejects_non_hour_time(studio, future_slot):
    start, end = future_slot
    bad_start = start.replace(minute=30)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=bad_start,
            end_time=end,
            equipment_items=[],
            customer_full_name="Test",
            customer_email="test@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )
    assert "start_time" in exc_info.value.message_dict


def test_create_booking_rejects_outside_operating_hours(studio, future_slot):
    start, end = future_slot
    early_start = start.replace(hour=7)
    early_end = early_start + timedelta(hours=1)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=early_start,
            end_time=early_end,
            equipment_items=[],
            customer_full_name="Test",
            customer_email="test@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )
    assert "start_time" in exc_info.value.message_dict


def test_create_booking_rejects_overlap(studio, future_slot):
    start, end = future_slot

    create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="First",
        customer_email="first@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    overlap_start = start + timedelta(hours=1)
    overlap_end = overlap_start + timedelta(hours=2)

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=studio,
            start_time=overlap_start,
            end_time=overlap_end,
            equipment_items=[],
            customer_full_name="Second",
            customer_email="second@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )
    assert "start_time" in exc_info.value.message_dict


def test_create_booking_rejects_equipment_overbooking(
    studio, equipment, future_slot
):
    start, end = future_slot
    second_studio = make_studio(name="Second Studio")
    equipment.stock_quantity = 1
    equipment.save(update_fields=["stock_quantity", "updated_at"])

    create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[{"id": str(equipment.id), "quantity": 1}],
        customer_full_name="First",
        customer_email="first@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    with pytest.raises(ValidationError) as exc_info:
        create_booking(
            studio=second_studio,
            start_time=start,
            end_time=end,
            equipment_items=[{"id": str(equipment.id), "quantity": 1}],
            customer_full_name="Second",
            customer_email="second@example.com",
            customer_phone="123",
            terms_accepted_at=timezone.now(),
        )
    assert "equipment" in exc_info.value.message_dict


def test_expire_pending_bookings(studio, future_slot):
    start, end = future_slot
    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test",
        customer_email="test@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    booking.payment_hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save(update_fields=["payment_hold_expires_at", "updated_at"])

    count = expire_pending_bookings()
    assert count == 1

    booking.refresh_from_db()
    assert booking.status == BookingStatus.EXPIRED


def test_cancel_booking(studio, future_slot):
    start, end = future_slot
    booking = create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test",
        customer_email="test@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    cancel_booking(booking=booking, reason="Admin cancellation")

    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED


def test_create_blocked_slot_success(studio, future_slot):
    start, end = future_slot
    block = create_blocked_slot(
        studio=studio,
        start_time=start,
        end_time=end,
        reason="Maintenance",
    )

    assert block.pk is not None
    assert studio.blocked_slots.count() == 1


def test_create_blocked_slot_rejects_overlap_with_booking(studio, future_slot):
    start, end = future_slot

    create_booking(
        studio=studio,
        start_time=start,
        end_time=end,
        equipment_items=[],
        customer_full_name="Test",
        customer_email="test@example.com",
        customer_phone="123",
        terms_accepted_at=timezone.now(),
    )

    with pytest.raises(ValidationError) as exc_info:
        create_blocked_slot(
            studio=studio,
            start_time=start,
            end_time=end,
            reason="Maintenance",
        )
    assert "start_time" in exc_info.value.message_dict


def test_create_blocked_slot_rejects_overlap_with_block(studio, future_slot):
    start, end = future_slot

    create_blocked_slot(
        studio=studio,
        start_time=start,
        end_time=end,
        reason="First block",
    )

    with pytest.raises(ValidationError) as exc_info:
        create_blocked_slot(
            studio=studio,
            start_time=start,
            end_time=end,
            reason="Second block",
        )
    assert "start_time" in exc_info.value.message_dict
