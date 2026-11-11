"""Database models for bookings, equipment line items and studio blocks.

Business rules live in ``services.py``; read queries live in ``selectors.py``.
These models publish the database contract: validation, constraints, indexes
and the deletion behaviour that protects booking history.

Money is integer kobo, never a float. Every price stored on a booking is a
snapshot taken at creation time, so a later catalogue price change cannot
rewrite what a customer agreed to pay.

Customers are guests: a booking carries contact details, never a user account.
``created_by`` records the administrator only when staff book on someone's
behalf.
"""

import secrets
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction

from apps.bookings.utils import (
    BOOKING_REFERENCE_MAX_LENGTH,
    REFERENCE_GENERATION_ATTEMPTS,
    generate_booking_reference,
    is_reference_conflict,
)
from apps.common.constants import MAX_DATABASE_INTEGER
from apps.common.models import UUIDModel, UUIDTimeStampedModel
from apps.equipment.models import Equipment
from apps.studios.models import Studio

SECONDS_PER_HOUR = 3600


class BookingStatus(models.TextChoices):
    """Lifecycle of a booking.

    ``PENDING_PAYMENT`` is a ten-minute inventory hold, ``CONFIRMED`` is paid,
    ``EXPIRED`` is an unpaid hold that was released, ``COMPLETED`` is a paid
    booking whose session has finished and ``CANCELLED`` is an administrative
    cancellation (which never implies a refund).
    """

    PENDING_PAYMENT = "pending_payment", "Pending payment"
    CONFIRMED = "confirmed", "Confirmed"
    EXPIRED = "expired", "Expired"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class Booking(UUIDTimeStampedModel):
    """A guest's reservation of one studio plus optional equipment."""

    # Expose the choices on the model so callers can use ``Booking.Status``.
    Status = BookingStatus

    access_token = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        help_text="High-entropy token for secure guest retrieval of booking details.",
    )

    reference = models.CharField(
        max_length=BOOKING_REFERENCE_MAX_LENGTH,
        unique=True,
        editable=False,
        help_text="Public identifier, generated on creation and never reused.",
    )
    studio = models.ForeignKey(
        Studio,
        on_delete=models.PROTECT,
        related_name="bookings",
        help_text="Studios deactivate instead of deleting, so history survives.",
    )

    customer_full_name = models.CharField(max_length=200)
    customer_email = models.EmailField(
        help_text="Stored lower-case so guest lookups are case-insensitive.",
    )
    customer_phone = models.CharField(max_length=20)

    start_time = models.DateTimeField(
        help_text="Timezone-aware, on the hour, inside studio operating hours.",
    )
    end_time = models.DateTimeField(
        help_text="Timezone-aware, on the hour, after start_time.",
    )
    duration_hours = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Whole hours between start_time and end_time.",
    )

    # --- Money snapshots (integer kobo) -----------------------------------
    # Unit prices mirror the catalogue columns exactly. Derived totals use
    # bigint: nine hours at MAX_DATABASE_INTEGER kobo overflows int4.
    studio_hourly_price_kobo = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_DATABASE_INTEGER),
        ],
        help_text="Snapshot of Studio.hourly_rate_kobo at booking time.",
    )
    studio_subtotal_kobo = models.PositiveBigIntegerField(
        validators=[MinValueValidator(1)],
        help_text="studio_hourly_price_kobo x duration_hours.",
    )
    equipment_subtotal_kobo = models.PositiveBigIntegerField(
        default=0,
        help_text="Sum of the equipment line subtotals; fixed per booking.",
    )
    total_amount_kobo = models.PositiveBigIntegerField(
        help_text="studio_subtotal_kobo + equipment_subtotal_kobo. 100% upfront.",
    )

    status = models.CharField(
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING_PAYMENT,
    )
    payment_hold_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When an unpaid hold lapses. Required while the booking is "
            "pending payment so the expiration sweep can always find it."
        ),
    )
    terms_accepted_at = models.DateTimeField(
        help_text="When the customer accepted the terms. No refunds, no self-cancellation.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings_created",
        help_text="Administrator who created the booking, if booked on behalf of a guest.",
    )

    class Meta:
        ordering = ("-created_at",)
        get_latest_by = "created_at"
        indexes = [
            # Overlap checks and per-studio calendars.
            models.Index(
                fields=["studio", "start_time", "end_time"],
                name="booking_studio_time_idx",
            ),
            # Administrative lists and availability by status.
            models.Index(
                fields=["status", "start_time"],
                name="booking_status_start_idx",
            ),
            # The unpaid-hold expiration sweep.
            models.Index(
                fields=["status", "payment_hold_expires_at"],
                name="booking_hold_expiry_idx",
            ),
            # Guest history / support lookup, newest first.
            models.Index(
                fields=["customer_email", "-created_at"],
                name="booking_customer_email_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="booking_end_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_hours__gte=1),
                name="booking_duration_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=BookingStatus.values),
                name="booking_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(studio_hourly_price_kobo__gte=1),
                name="booking_hourly_price_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(studio_subtotal_kobo__gte=1),
                name="booking_studio_subtotal_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(equipment_subtotal_kobo__gte=0),
                name="booking_equipment_subtotal_non_neg",
            ),
            # Overflow-safe (bigint addition). The hourly x duration equality
            # is checked in clean()/services instead: int4 * int4 can overflow
            # in PostgreSQL before the comparison runs.
            models.CheckConstraint(
                condition=models.Q(
                    total_amount_kobo=(
                        models.F("studio_subtotal_kobo")
                        + models.F("equipment_subtotal_kobo")
                    )
                ),
                name="booking_total_equals_subtotals",
            ),
            # A pending booking without an expiry could hold stock forever.
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=BookingStatus.PENDING_PAYMENT)
                    | models.Q(payment_hold_expires_at__isnull=False)
                ),
                name="booking_pending_has_hold_expiry",
            ),
            # Required columns are NOT NULL; these close the empty-string hole.
            models.CheckConstraint(
                condition=~models.Q(customer_full_name=""),
                name="booking_customer_name_present",
            ),
            models.CheckConstraint(
                condition=~models.Q(customer_email=""),
                name="booking_customer_email_present",
            ),
            models.CheckConstraint(
                condition=~models.Q(customer_phone=""),
                name="booking_customer_phone_present",
            ),
        ]

    def __str__(self):
        return f"{self.reference} ({self.studio.name})"

    def clean(self):
        super().clean()
        errors = {}

        if self.customer_email:
            self.customer_email = self.customer_email.strip().lower()

        if self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                errors["end_time"] = "End time must be after start time."
            else:
                seconds = (self.end_time - self.start_time).total_seconds()
                if seconds % SECONDS_PER_HOUR:
                    errors["end_time"] = "Bookings must cover whole hours."
                elif (
                    self.duration_hours
                    and int(seconds // SECONDS_PER_HOUR) != self.duration_hours
                ):
                    errors["duration_hours"] = (
                        "Duration does not match the booked time window."
                    )

        if (
            self.status == BookingStatus.PENDING_PAYMENT
            and self.payment_hold_expires_at is None
        ):
            errors["payment_hold_expires_at"] = (
                "A pending booking needs a payment hold expiry."
            )

        if self.studio_hourly_price_kobo and self.duration_hours:
            expected = self.studio_hourly_price_kobo * self.duration_hours
            if self.studio_subtotal_kobo != expected:
                errors["studio_subtotal_kobo"] = (
                    "Studio subtotal must be the hourly price times the duration."
                )

        expected_total = (self.studio_subtotal_kobo or 0) + (
            self.equipment_subtotal_kobo or 0
        )
        if self.total_amount_kobo != expected_total:
            errors["total_amount_kobo"] = (
                "Total must equal the studio and equipment subtotals."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Normalise on every write path, including the admin and data imports.
        self.customer_email = (self.customer_email or "").strip().lower()

        if not self.access_token:
            self.access_token = secrets.token_urlsafe(32)

        # An existing reference is immutable. Rows loaded from the database
        # always have one, so updates never regenerate it.
        if self.reference:
            return super().save(*args, **kwargs)

        # Two concurrent inserts can pick the same reference. Retry in a
        # savepoint so the failed insert is rolled back without poisoning the
        # caller's transaction.
        for attempt in range(REFERENCE_GENERATION_ATTEMPTS):
            self.reference = generate_booking_reference()
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:
                if (
                    not is_reference_conflict(exc)
                    or attempt == REFERENCE_GENERATION_ATTEMPTS - 1
                ):
                    raise

        raise AssertionError("unreachable")  # pragma: no cover


class BookingEquipment(UUIDModel):
    """One equipment line on a booking.

    Immutable by design: there is no ``updated_at`` because a line is a price
    snapshot, not editable state. Changing a booking's equipment means replacing
    its lines inside the booking service, never editing prices in place.

    Equipment is a single global pool (see ``apps.equipment.models``), so this
    line reserves units from that pool for the booking's time window across
    every studio.
    """

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="equipment_items",
    )
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.PROTECT,
        related_name="booking_items",
        help_text="Items deactivate instead of deleting, so history survives.",
    )
    quantity = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_DATABASE_INTEGER),
        ],
        help_text="Units reserved for the whole booking window.",
    )
    unit_price_kobo = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_DATABASE_INTEGER),
        ],
        help_text=(
            "Snapshot of Equipment.fixed_price_kobo at booking time. Fixed per "
            "item per booking; never multiplied by the booking duration."
        ),
    )
    subtotal_kobo = models.PositiveBigIntegerField(
        help_text="unit_price_kobo x quantity.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="booking_equipment_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price_kobo__gte=1),
                name="booking_equipment_unit_price_positive",
            ),
            # quantity >= 1 implies subtotal >= unit price. Overflow-safe,
            # unlike a subtotal = unit_price * quantity check (int4 * int4).
            models.CheckConstraint(
                condition=models.Q(subtotal_kobo__gte=models.F("unit_price_kobo")),
                name="booking_equipment_subtotal_gte_unit",
            ),
            # One line per item keeps inventory sums unambiguous.
            models.UniqueConstraint(
                fields=["booking", "equipment"],
                name="booking_equipment_unique_item",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.equipment.name} on {self.booking.reference}"

    def clean(self):
        super().clean()
        if self.unit_price_kobo and self.quantity:
            if self.subtotal_kobo != self.unit_price_kobo * self.quantity:
                raise ValidationError(
                    {
                        "subtotal_kobo": "Subtotal must be the unit price times the quantity."
                    }
                )


class BlockedSlot(UUIDModel):
    """An administrative closure of a studio (maintenance, private use, holiday).

    Overlapping blocks make the affected hours unavailable to customers. Unlike
    bookings, blocks carry no money and no history worth preserving, so they may
    be removed outright and cascade away with a hard-deleted studio.
    """

    studio = models.ForeignKey(
        Studio,
        on_delete=models.CASCADE,
        related_name="blocked_slots",
    )
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    reason = models.CharField(
        max_length=200,
        help_text="Why the studio is unavailable. Required for auditability.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blocked_slots_created",
        help_text="Administrator who created the block; null if their account is removed.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("start_time",)
        indexes = [
            models.Index(
                fields=["studio", "start_time", "end_time"],
                name="blocked_slot_studio_time_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="blocked_slot_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.studio.name} blocked {self.start_time:%Y-%m-%d %H:%M}"

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "End time must be after start time."})
