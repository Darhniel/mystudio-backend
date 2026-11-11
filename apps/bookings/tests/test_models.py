"""Model-level tests for bookings.

These assert the database contract: constraints, snapshots, deletion behaviour
and identifier generation. Availability, pricing rules and concurrency are
service-layer concerns and are tested separately.

Every fixture is module-local and prefixed so it cannot shadow the shared
fixtures in the project's root conftest.py.
"""

from datetime import datetime, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.bookings import models as booking_models
from apps.bookings.models import BlockedSlot, Booking, BookingEquipment, BookingStatus
from apps.bookings.utils import BOOKING_REFERENCE_ALPHABET, BOOKING_REFERENCE_PREFIX
from apps.equipment.models import Equipment
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def booking_studio():
    return Studio.objects.create(
        name="Models Test Studio",
        hourly_rate_kobo=500_000,
        capacity=8,
    )


@pytest.fixture
def booking_equipment():
    return Equipment.objects.create(
        name="Models Test Light",
        fixed_price_kobo=150_000,
        stock_quantity=4,
    )


@pytest.fixture
def booking_admin():
    # No password: these tests never authenticate, they only exercise FKs.
    return User.objects.create(email="models-admin@mystudio.ng", role=Role.STAFF)


@pytest.fixture
def slot():
    """A future 10:00-12:00 window in the project timezone (Africa/Lagos)."""
    tomorrow = timezone.localdate() + timedelta(days=1)
    start = timezone.make_aware(
        datetime.combine(tomorrow, time(10, 0)),
        timezone.get_current_timezone(),
    )
    return start, start + timedelta(hours=2)


def booking_payload(studio, slot, **overrides):
    start, end = slot
    subtotal = studio.hourly_rate_kobo * 2
    payload = {
        "studio": studio,
        "customer_full_name": "Ada Obi",
        "customer_email": "ada@example.com",
        "customer_phone": "+2348012345678",
        "start_time": start,
        "end_time": end,
        "duration_hours": 2,
        "studio_hourly_price_kobo": studio.hourly_rate_kobo,
        "studio_subtotal_kobo": subtotal,
        "equipment_subtotal_kobo": 0,
        "total_amount_kobo": subtotal,
        "status": BookingStatus.PENDING_PAYMENT,
        "payment_hold_expires_at": timezone.now() + timedelta(minutes=10),
        "terms_accepted_at": timezone.now(),
    }
    payload.update(overrides)
    return payload


def line_payload(booking, equipment, **overrides):
    payload = {
        "booking": booking,
        "equipment": equipment,
        "quantity": 2,
        "unit_price_kobo": equipment.fixed_price_kobo,
        "subtotal_kobo": equipment.fixed_price_kobo * 2,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Identifiers and normalisation
# ---------------------------------------------------------------------------
def test_booking_gets_uuid_and_reference(booking_studio, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))

    assert booking.pk is not None
    assert booking.reference.startswith(f"{BOOKING_REFERENCE_PREFIX}-")
    suffix = booking.reference.split("-", 1)[1]
    assert len(suffix) == 10
    assert set(suffix) <= set(BOOKING_REFERENCE_ALPHABET)
    assert booking.created_at is not None
    assert booking.updated_at is not None


def test_references_are_unique_across_bookings(booking_studio, slot):
    references = {
        Booking.objects.create(**booking_payload(booking_studio, slot)).reference
        for _ in range(15)
    }
    assert len(references) == 15


def test_reference_is_not_regenerated_on_update(booking_studio, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    original = booking.reference

    booking.status = BookingStatus.CONFIRMED
    booking.save(update_fields=["status", "updated_at"])
    booking.refresh_from_db()

    assert booking.reference == original


def test_explicit_duplicate_reference_rejected(booking_studio, slot):
    existing = Booking.objects.create(**booking_payload(booking_studio, slot))

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(
                **booking_payload(booking_studio, slot, reference=existing.reference)
            )


def test_reference_collision_is_retried(monkeypatch, booking_studio, slot):
    existing = Booking.objects.create(**booking_payload(booking_studio, slot))
    candidates = iter([existing.reference, "MS-RETRIED001"])
    monkeypatch.setattr(
        booking_models, "generate_booking_reference", lambda: next(candidates)
    )

    booking = Booking.objects.create(**booking_payload(booking_studio, slot))

    assert booking.reference == "MS-RETRIED001"
    assert Booking.objects.count() == 2


def test_customer_email_is_normalised(booking_studio, slot):
    booking = Booking.objects.create(
        **booking_payload(booking_studio, slot, customer_email="  Ada@Example.COM ")
    )
    booking.refresh_from_db()
    assert booking.customer_email == "ada@example.com"


def test_str_includes_reference(booking_studio, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    assert str(booking) == f"{booking.reference} ({booking_studio.name})"


def test_valid_booking_passes_full_clean(booking_studio, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    booking.full_clean()


# ---------------------------------------------------------------------------
# Datetime and value constraints
# ---------------------------------------------------------------------------
def test_end_before_start_rejected(booking_studio, slot):
    start, end = slot
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(
                **booking_payload(booking_studio, slot, start_time=end, end_time=start)
            )


def test_end_equal_to_start_rejected(booking_studio, slot):
    start, _ = slot
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(
                **booking_payload(booking_studio, slot, end_time=start)
            )


def test_zero_duration_rejected(booking_studio, slot):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(
                **booking_payload(booking_studio, slot, duration_hours=0)
            )


def test_zero_hourly_price_rejected(booking_studio, slot):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(
                **booking_payload(booking_studio, slot, studio_hourly_price_kobo=0)
            )


def test_negative_equipment_subtotal_rejected(booking_studio, slot):
    payload = booking_payload(booking_studio, slot, equipment_subtotal_kobo=-1)
    payload["total_amount_kobo"] = payload["studio_subtotal_kobo"] - 1
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(**payload)


def test_total_must_equal_subtotals(booking_studio, slot):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(
                **booking_payload(booking_studio, slot, total_amount_kobo=1)
            )


def test_invalid_status_rejected(booking_studio, slot):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(
                **booking_payload(booking_studio, slot, status="refunded")
            )


@pytest.mark.parametrize(
    "field", ["customer_full_name", "customer_email", "customer_phone"]
)
def test_empty_customer_field_rejected(booking_studio, slot, field):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(
                **booking_payload(booking_studio, slot, **{field: ""})
            )


# ---------------------------------------------------------------------------
# Payment hold contract
# ---------------------------------------------------------------------------
def test_pending_booking_requires_hold_expiry(booking_studio, slot):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Booking.objects.create(
                **booking_payload(booking_studio, slot, payment_hold_expires_at=None)
            )


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.CONFIRMED,
        BookingStatus.EXPIRED,
        BookingStatus.COMPLETED,
        BookingStatus.CANCELLED,
    ],
)
def test_non_pending_booking_may_omit_hold_expiry(booking_studio, slot, status):
    booking = Booking.objects.create(
        **booking_payload(
            booking_studio, slot, status=status, payment_hold_expires_at=None
        )
    )
    assert booking.status == status


def test_clean_requires_hold_expiry_for_pending(booking_studio, slot):
    booking = Booking(
        **booking_payload(booking_studio, slot, payment_hold_expires_at=None)
    )
    with pytest.raises(ValidationError) as exc:
        booking.full_clean()
    assert "payment_hold_expires_at" in exc.value.message_dict


# ---------------------------------------------------------------------------
# clean() rules the database cannot express safely
# ---------------------------------------------------------------------------
def test_clean_rejects_partial_hours(booking_studio, slot):
    start, end = slot
    booking = Booking(
        **booking_payload(booking_studio, slot, end_time=end + timedelta(minutes=30))
    )
    with pytest.raises(ValidationError) as exc:
        booking.clean()
    assert "end_time" in exc.value.message_dict


def test_clean_rejects_duration_mismatch(booking_studio, slot):
    booking = Booking(**booking_payload(booking_studio, slot, duration_hours=3))
    with pytest.raises(ValidationError) as exc:
        booking.clean()
    assert "duration_hours" in exc.value.message_dict


def test_clean_rejects_studio_subtotal_mismatch(booking_studio, slot):
    booking = Booking(**booking_payload(booking_studio, slot, studio_subtotal_kobo=1))
    with pytest.raises(ValidationError) as exc:
        booking.clean()
    assert "studio_subtotal_kobo" in exc.value.message_dict


# ---------------------------------------------------------------------------
# Deletion behaviour and history preservation
# ---------------------------------------------------------------------------
def test_studio_delete_deactivates_and_keeps_booking(booking_studio, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))

    booking_studio.delete()
    booking_studio.refresh_from_db()

    assert booking_studio.is_active is False
    assert Booking.objects.filter(pk=booking.pk).exists()


def test_studio_hard_delete_is_protected(booking_studio, slot):
    Booking.objects.create(**booking_payload(booking_studio, slot))
    with pytest.raises(ProtectedError):
        Studio.objects.filter(pk=booking_studio.pk).hard_delete()


def test_equipment_hard_delete_is_protected(booking_studio, booking_equipment, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    BookingEquipment.objects.create(**line_payload(booking, booking_equipment))

    with pytest.raises(ProtectedError):
        Equipment.objects.filter(pk=booking_equipment.pk).hard_delete()


def test_equipment_delete_deactivates_and_keeps_line(
    booking_studio, booking_equipment, slot
):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    line = BookingEquipment.objects.create(**line_payload(booking, booking_equipment))

    booking_equipment.delete()
    booking_equipment.refresh_from_db()

    assert booking_equipment.is_active is False
    assert BookingEquipment.objects.filter(pk=line.pk).exists()


def test_creator_is_nulled_when_account_removed(booking_studio, booking_admin, slot):
    booking = Booking.objects.create(
        **booking_payload(booking_studio, slot, created_by=booking_admin)
    )
    booking_admin.delete()
    booking.refresh_from_db()

    assert booking.created_by is None
    assert Booking.objects.filter(pk=booking.pk).exists()


def test_booking_delete_removes_its_lines(booking_studio, booking_equipment, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    BookingEquipment.objects.create(**line_payload(booking, booking_equipment))

    booking.delete()

    assert BookingEquipment.objects.count() == 0


# ---------------------------------------------------------------------------
# Equipment line items
# ---------------------------------------------------------------------------
def test_line_item_snapshots_survive_catalogue_price_change(
    booking_studio, booking_equipment, slot
):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    line = BookingEquipment.objects.create(**line_payload(booking, booking_equipment))
    original_unit = line.unit_price_kobo
    original_subtotal = line.subtotal_kobo

    booking_equipment.fixed_price_kobo = 999_999
    booking_equipment.save(update_fields=["fixed_price_kobo", "updated_at"])
    line.refresh_from_db()

    assert line.unit_price_kobo == original_unit
    assert line.subtotal_kobo == original_subtotal


def test_line_item_rejects_duplicate_equipment(booking_studio, booking_equipment, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    BookingEquipment.objects.create(**line_payload(booking, booking_equipment))

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BookingEquipment.objects.create(**line_payload(booking, booking_equipment))


def test_line_item_rejects_zero_quantity(booking_studio, booking_equipment, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BookingEquipment.objects.create(
                **line_payload(booking, booking_equipment, quantity=0, subtotal_kobo=0)
            )


def test_line_item_rejects_zero_unit_price(booking_studio, booking_equipment, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BookingEquipment.objects.create(
                **line_payload(booking, booking_equipment, unit_price_kobo=0)
            )


def test_line_item_subtotal_cannot_be_below_unit_price(
    booking_studio, booking_equipment, slot
):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BookingEquipment.objects.create(
                **line_payload(booking, booking_equipment, subtotal_kobo=1)
            )


def test_line_item_clean_rejects_subtotal_mismatch(
    booking_studio, booking_equipment, slot
):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    line = BookingEquipment(
        **line_payload(booking, booking_equipment, subtotal_kobo=1_000_000)
    )
    with pytest.raises(ValidationError) as exc:
        line.clean()
    assert "subtotal_kobo" in exc.value.message_dict


def test_line_item_str(booking_studio, booking_equipment, slot):
    booking = Booking.objects.create(**booking_payload(booking_studio, slot))
    line = BookingEquipment.objects.create(**line_payload(booking, booking_equipment))
    assert str(line) == f"2 x {booking_equipment.name} on {booking.reference}"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------
def test_bookings_are_ordered_newest_first(booking_studio, slot):
    first = Booking.objects.create(**booking_payload(booking_studio, slot))
    second = Booking.objects.create(**booking_payload(booking_studio, slot))
    assert list(Booking.objects.all()) == [second, first]


# ---------------------------------------------------------------------------
# Blocked slots
# ---------------------------------------------------------------------------
def test_blocked_slot_creation(booking_studio, booking_admin, slot):
    start, end = slot
    block = BlockedSlot.objects.create(
        studio=booking_studio,
        start_time=start,
        end_time=end,
        reason="Annual maintenance",
        created_by=booking_admin,
    )

    assert block.pk is not None
    assert block.created_at is not None
    assert booking_studio.blocked_slots.count() == 1
    assert "blocked" in str(block)


def test_blocked_slot_end_must_follow_start(booking_studio, slot):
    start, end = slot
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BlockedSlot.objects.create(
                studio=booking_studio,
                start_time=end,
                end_time=start,
                reason="Invalid",
            )


def test_blocked_slot_clean_rejects_equal_times(booking_studio, slot):
    start, _ = slot
    block = BlockedSlot(
        studio=booking_studio, start_time=start, end_time=start, reason="Invalid"
    )
    with pytest.raises(ValidationError) as exc:
        block.clean()
    assert "end_time" in exc.value.message_dict


def test_blocked_slot_creator_is_nulled(booking_studio, booking_admin, slot):
    start, end = slot
    block = BlockedSlot.objects.create(
        studio=booking_studio,
        start_time=start,
        end_time=end,
        reason="Maintenance",
        created_by=booking_admin,
    )
    booking_admin.delete()
    block.refresh_from_db()
    assert block.created_by is None


def test_blocked_slots_are_ordered_chronologically(booking_studio, slot):
    start, end = slot
    later = BlockedSlot.objects.create(
        studio=booking_studio,
        start_time=start + timedelta(days=1),
        end_time=end + timedelta(days=1),
        reason="Later",
    )
    earlier = BlockedSlot.objects.create(
        studio=booking_studio, start_time=start, end_time=end, reason="Earlier"
    )
    assert list(BlockedSlot.objects.all()) == [earlier, later]
