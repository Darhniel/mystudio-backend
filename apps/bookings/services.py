"""Write-side business logic for bookings.

The HTTP layer validates shapes and permissions; this module owns the rules
that must also hold for management commands, admin actions and future workers.
All inventory checks and price calculations happen inside a transaction with
row-level locks to prevent double-booking and overbooking.
"""

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.bookings.models import (
    BlockedSlot,
    Booking,
    BookingEquipment,
    BookingStatus,
)
from apps.bookings.selectors import (
    get_booked_equipment_quantities,
    get_overlapping_blocks,
    get_overlapping_bookings,
)
from apps.equipment.models import Equipment
from apps.studios.models import Studio

logger = logging.getLogger("mystudio.bookings")

STUDIO_OPEN_HOUR = 9
STUDIO_CLOSE_HOUR = 18
PENDING_HOLD_MINUTES = 10
MAX_BOOKING_DURATION_HOURS = 9


def _validate_booking_times(start_time, end_time):
    """Enforce operating hours, whole-hour increments, duration limits, and no past times."""
    now = timezone.now()
    if start_time < now:
        raise ValidationError({"start_time": "Start time cannot be in the past."})

    if start_time.minute != 0 or start_time.second != 0 or start_time.microsecond != 0:
        raise ValidationError({"start_time": "Start time must be exactly on the hour."})
    if end_time.minute != 0 or end_time.second != 0 or end_time.microsecond != 0:
        raise ValidationError({"end_time": "End time must be exactly on the hour."})

    if start_time.hour < STUDIO_OPEN_HOUR or start_time.hour >= STUDIO_CLOSE_HOUR:
        raise ValidationError(
            {
                "start_time": (
                    f"Bookings must start between {STUDIO_OPEN_HOUR:02d}:00 "
                    f"and {STUDIO_CLOSE_HOUR:02d}:00."
                )
            }
        )

    if end_time.hour > STUDIO_CLOSE_HOUR:
        raise ValidationError(
            {"end_time": f"Bookings must end at or before {STUDIO_CLOSE_HOUR:02d}:00."}
        )

    duration_seconds = (end_time - start_time).total_seconds()
    if duration_seconds <= 0 or duration_seconds % 3600 != 0:
        raise ValidationError(
            {"end_time": "Booking duration must be at least one whole hour."}
        )

    duration_hours = int(duration_seconds / 3600)
    if duration_hours > MAX_BOOKING_DURATION_HOURS:
        raise ValidationError(
            {
                "end_time": f"Maximum booking duration is {MAX_BOOKING_DURATION_HOURS} hours."
            }
        )


def create_booking(
    *,
    studio: Studio,
    start_time,
    end_time,
    equipment_items: list[dict],
    customer_full_name: str,
    customer_email: str,
    customer_phone: str,
    terms_accepted_at,
    created_by=None,
) -> Booking:
    """Create a booking with concurrency protection and price snapshots.

    Prices are always read from the locked studio and equipment rows, never
    trusted from the frontend. The transaction holds row-level locks on the
    studio and all requested equipment to prevent overlapping bookings and
    equipment overbooking.

    Concurrency strategy:
    1. Lock the studio row first (single row, always locked first).
    2. Lock equipment rows in ID order (consistent ordering prevents deadlocks).
    3. Recheck availability inside the transaction (frontend checks are not trusted).
    4. Calculate prices from locked rows (never trust frontend).
    5. Create booking and equipment lines atomically.
    6. Set payment hold expiration.

    If any step fails, the entire transaction rolls back.
    """
    _validate_booking_times(start_time, end_time)
    duration_hours = int((end_time - start_time).total_seconds() / 3600)

    equipment_ids = [str(item["id"]) for item in equipment_items]

    with transaction.atomic():
        # STEP 1: Lock the studio row to prevent concurrent bookings for the same slot.
        locked_studio = Studio.objects.select_for_update().get(id=studio.id)
        if not locked_studio.is_active:
            raise ValidationError({"studio": "This studio is no longer active."})

        # STEP 2: Lock all requested equipment rows in consistent order (by ID).
        # Consistent ordering prevents deadlocks when multiple transactions try to
        # lock the same equipment in different orders.
        locked_equipment = {}
        if equipment_ids:
            eqs = (
                Equipment.objects.select_for_update()
                .filter(id__in=equipment_ids)
                .order_by("id")  # CRITICAL: consistent locking order
            )
            if len(eqs) != len(equipment_ids):
                raise ValidationError(
                    {"equipment": "One or more equipment items not found."}
                )
            for eq in eqs:
                locked_equipment[str(eq.id)] = eq

        # STEP 3: Recheck studio availability inside the transaction.
        # Frontend availability checks are not trusted because another request
        # could have booked the slot between the frontend check and this transaction.
        overlapping_bookings = get_overlapping_bookings(
            studio=locked_studio, start_time=start_time, end_time=end_time
        )
        if overlapping_bookings.exists():
            raise ValidationError({"start_time": "This time slot is already booked."})

        overlapping_blocks = get_overlapping_blocks(
            studio=locked_studio, start_time=start_time, end_time=end_time
        )
        if overlapping_blocks.exists():
            raise ValidationError(
                {"start_time": "This time slot is blocked by administration."}
            )

        # STEP 4: Calculate prices from locked rows (never trust frontend).
        studio_hourly_price_kobo = locked_studio.hourly_rate_kobo
        studio_subtotal_kobo = studio_hourly_price_kobo * duration_hours
        equipment_subtotal_kobo = 0
        booking_equipment_data = []

        # STEP 5: Recheck equipment availability inside the transaction.
        booked_quantities = get_booked_equipment_quantities(
            equipment_ids=equipment_ids, start_time=start_time, end_time=end_time
        )

        for item in equipment_items:
            eq_id = str(item["id"])
            quantity = item["quantity"]

            if not isinstance(quantity, int) or quantity < 1:
                raise ValidationError(
                    {"equipment": "Equipment quantity must be a positive integer."}
                )

            eq = locked_equipment[eq_id]
            if not eq.is_active:
                raise ValidationError(
                    {"equipment": f"Equipment '{eq.name}' is no longer active."}
                )

            booked_qty = booked_quantities.get(eq_id, 0)
            if booked_qty + quantity > eq.stock_quantity:
                raise ValidationError(
                    {
                        "equipment": f"Only {eq.stock_quantity - booked_qty} units of '{eq.name}' available for this time slot."
                    }
                )

            equipment_subtotal_kobo += eq.fixed_price_kobo * quantity
            booking_equipment_data.append(
                {
                    "equipment": eq,
                    "quantity": quantity,
                    "unit_price_kobo": eq.fixed_price_kobo,
                    "subtotal_kobo": eq.fixed_price_kobo * quantity,
                }
            )

        # STEP 6: Calculate total and set payment hold expiration.
        total_amount_kobo = studio_subtotal_kobo + equipment_subtotal_kobo
        payment_hold_expires_at = timezone.now() + timedelta(
            minutes=PENDING_HOLD_MINUTES
        )

        # STEP 7: Create booking record.
        booking = Booking.objects.create(
            studio=locked_studio,
            customer_full_name=customer_full_name,
            customer_email=customer_email,
            customer_phone=customer_phone,
            start_time=start_time,
            end_time=end_time,
            duration_hours=duration_hours,
            studio_hourly_price_kobo=studio_hourly_price_kobo,
            studio_subtotal_kobo=studio_subtotal_kobo,
            equipment_subtotal_kobo=equipment_subtotal_kobo,
            total_amount_kobo=total_amount_kobo,
            status=BookingStatus.PENDING_PAYMENT,
            payment_hold_expires_at=payment_hold_expires_at,
            terms_accepted_at=terms_accepted_at,
            created_by=created_by,
        )

        # STEP 8: Create equipment line items.
        for be_data in booking_equipment_data:
            BookingEquipment.objects.create(
                booking=booking,
                equipment=be_data["equipment"],
                quantity=be_data["quantity"],
                unit_price_kobo=be_data["unit_price_kobo"],
                subtotal_kobo=be_data["subtotal_kobo"],
            )

        return booking


def expire_pending_bookings() -> int:
    """Expire pending bookings past their payment hold expiry time.

    This function is designed to be called by a scheduled task (cron, Celery beat,
    or a management command). It returns the number of bookings expired.
    """
    now = timezone.now()
    count = Booking.objects.filter(
        status=BookingStatus.PENDING_PAYMENT,
        payment_hold_expires_at__lte=now,
    ).update(status=BookingStatus.EXPIRED)

    if count > 0:
        logger.info(f"Expired {count} pending bookings.")
    return count


def cancel_booking(*, booking: Booking, reason: str = "", cancelled_by=None) -> Booking:
    """Cancel a booking administratively.

    This does not trigger any refund logic. Refunds are handled separately in
    the payments app (Milestone 4+).
    """
    if booking.status in [BookingStatus.EXPIRED, BookingStatus.CANCELLED]:
        raise ValidationError(
            {"status": "This booking is already cancelled or expired."}
        )

    booking.status = BookingStatus.CANCELLED
    booking.save(update_fields=["status", "updated_at"])
    logger.info(f"Booking {booking.reference} cancelled. Reason: {reason}")
    return booking


def create_blocked_slot(
    *,
    studio: Studio,
    start_time,
    end_time,
    reason: str,
    created_by=None,
) -> BlockedSlot:
    """Create an administrative blocked slot for a studio.

    Raises ValidationError if the slot overlaps with existing confirmed/pending
    bookings or other blocked slots.
    """
    if start_time >= end_time:
        raise ValidationError({"end_time": "End time must be after start time."})

    with transaction.atomic():
        locked_studio = Studio.objects.select_for_update().get(id=studio.id)
        if not locked_studio.is_active:
            raise ValidationError({"studio": "This studio is no longer active."})

        overlapping_bookings = get_overlapping_bookings(
            studio=locked_studio, start_time=start_time, end_time=end_time
        )
        if overlapping_bookings.exists():
            raise ValidationError(
                {
                    "start_time": "Cannot block a time slot that has existing confirmed or pending bookings."
                }
            )

        overlapping_blocks = get_overlapping_blocks(
            studio=locked_studio, start_time=start_time, end_time=end_time
        )
        if overlapping_blocks.exists():
            raise ValidationError({"start_time": "This time slot is already blocked."})

        return BlockedSlot.objects.create(
            studio=locked_studio,
            start_time=start_time,
            end_time=end_time,
            reason=reason,
            created_by=created_by,
        )


def expire_pending_bookings(batch_size: int = 100) -> int:
    """Expire pending bookings past their payment hold expiry time.

    This function is designed to be called by a scheduled task (cron, Celery beat,
    or a management command). It processes bookings in batches to avoid long-running
    transactions and memory issues.

    Returns the total number of bookings expired.

    Safety guarantees:
    - Only expires PENDING_PAYMENT bookings past their expiry time.
    - Never touches CONFIRMED, COMPLETED, or CANCELLED bookings.
    - Safe to run repeatedly (idempotent).
    - Uses transactions to ensure atomicity.
    """
    now = timezone.now()
    total_expired = 0

    while True:
        # Fetch a batch of expired bookings
        expired_batch = list(
            Booking.objects.filter(
                status=BookingStatus.PENDING_PAYMENT,
                payment_hold_expires_at__lte=now,
            )[:batch_size]
        )

        if not expired_batch:
            break

        # Process each booking in a transaction
        for booking in expired_batch:
            try:
                with transaction.atomic():
                    # Re-fetch with lock to prevent race conditions
                    locked_booking = (
                        Booking.objects.select_for_update()
                        .filter(id=booking.id)
                        .first()
                    )

                    if (
                        locked_booking
                        and locked_booking.status == BookingStatus.PENDING_PAYMENT
                        and locked_booking.payment_hold_expires_at <= now
                    ):
                        locked_booking.status = BookingStatus.EXPIRED
                        locked_booking.save(update_fields=["status", "updated_at"])
                        total_expired += 1
            except Exception as e:
                logger.error(
                    f"Failed to expire booking {booking.reference}: {e}",
                    exc_info=True,
                )
                # Continue processing other bookings
                continue

    if total_expired > 0:
        logger.info(f"Expired {total_expired} pending bookings.")

    return total_expired
