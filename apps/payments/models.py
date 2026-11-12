"""Database models for payments.

Business rules live in ``services.py``. This model tracks the lifecycle of
Paystack transactions, ensuring idempotency and providing an audit trail for
late payments that require manual administrative review.

Payment records are immutable once successful: they are never deleted, and a
successful payment cannot be downgraded to failed by duplicate or delayed
webhook events.
"""

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.bookings.models import Booking
from apps.common.constants import MAX_DATABASE_INTEGER


class PaymentStatus(models.TextChoices):
    """Lifecycle of a payment attempt.

    Includes standard states and specific Paystack gateway statuses to ensure
    we never incorrectly mark a pending or ongoing transaction as failed.
    """

    INITIALIZING = "initializing", "Initializing"
    INITIALIZED = "initialized", "Initialized"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    ABANDONED = "abandoned", "Abandoned"
    REQUIRES_REVIEW = "requires_review", "Requires review"

    # Paystack-specific transient states
    PENDING = "pending", "Pending"
    ONGOING = "ongoing", "Ongoing"
    PROCESSING = "processing", "Processing"
    REVERSED = "reversed", "Reversed"


class Payment(models.Model):
    """A single payment attempt against a booking.

    A booking may have many payment attempts (browser refresh, network retry,
    late payment after hold expiry). Each attempt gets its own row with a
    unique Paystack reference. Only one successful payment can ultimately
    confirm the booking.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey(
        Booking,
        on_delete=models.PROTECT,
        related_name="payments",
        help_text=(
            "Protects booking history. Payment records are never deleted, "
            "so the booking they reference must also survive."
        ),
    )

    # --- Paystack identifiers ---------------------------------------------
    # Paystack references are opaque strings up to ~100 chars. Transaction IDs
    # returned by Paystack are also strings (numeric or UUID-formatted), so
    # CharField is the safe choice for both.
    paystack_reference = models.CharField(
        max_length=100,
        unique=True,
        help_text="Unique reference sent to Paystack. Used for idempotency.",
    )
    paystack_transaction_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Paystack's internal transaction ID, populated on success.",
    )

    # --- Money ------------------------------------------------------------
    amount_kobo = models.PositiveBigIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_DATABASE_INTEGER)],
        help_text=(
            "Exact amount charged, copied from Booking.total_amount_kobo at "
            "initialization time. Never accepted from the frontend."
        ),
    )
    currency = models.CharField(
        max_length=3,
        default="NGN",
        help_text="Always NGN for this platform.",
    )

    # --- Lifecycle --------------------------------------------------------
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.INITIALIZING,
    )
    payment_channel = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text=(
            "Payment channel reported by Paystack (e.g., 'card', 'bank', "
            "'ussd', 'mobile_money'). Empty until a successful charge."
        ),
    )

    # --- Timestamps -------------------------------------------------------
    # ``created_at`` / ``updated_at`` are the row-level audit trail. The three
    # lifecycle timestamps below record exactly when each Paystack state
    # transition occurred, so support staff can answer "when did the customer
    # actually pay?" without parsing raw JSON.
    initialized_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When Paystack returned the authorization URL.",
    )
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When Paystack reported the charge as successful.",
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When our server verified the transaction with Paystack.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- Audit / reconciliation -------------------------------------------
    gateway_response = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Selective gateway response data (status, message, gateway "
            "response code, authorization details). Raw payloads are not "
            "stored here; see the audit log for full event data."
        ),
    )
    review_reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Why this payment requires manual review. Empty when status is "
            "not REQUIRES_REVIEW."
        ),
    )

    class Meta:
        ordering = ("-created_at",)
        get_latest_by = "created_at"
        indexes = [
            # Admin dashboard: list payments by status.
            models.Index(
                fields=["status", "-created_at"], name="payment_status_created_idx"
            ),
            # Booking detail page: all payment attempts for a booking.
            models.Index(
                fields=["booking", "-created_at"], name="payment_booking_created_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=PaymentStatus.values),
                name="payment_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_kobo__gte=1),
                name="payment_amount_positive",
            ),
            # ``paid_at`` can only be set when the payment is successful or
            # awaiting review (i.e., Paystack actually charged the customer).
            models.CheckConstraint(
                condition=(
                    models.Q(paid_at__isnull=True)
                    | models.Q(
                        status__in=[
                            PaymentStatus.SUCCESS,
                            PaymentStatus.REQUIRES_REVIEW,
                        ]
                    )
                ),
                name="payment_paid_at_only_when_charged",
            ),
        ]

    def __str__(self):
        return f"Payment {self.paystack_reference} ({self.status})"

    @property
    def requires_review_flag(self) -> bool:
        """Return whether this payment is awaiting manual review."""
        return self.status == PaymentStatus.REQUIRES_REVIEW


class WebhookEventStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class WebhookEvent(models.Model):
    """Idempotency and audit trail for incoming Paystack webhook events."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_fingerprint = models.CharField(
        max_length=128,
        unique=True,
        help_text="SHA256 hash of the raw event body to guarantee idempotency.",
    )
    event_type = models.CharField(max_length=50)
    transaction_reference = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=WebhookEventStatus.choices,
        default=WebhookEventStatus.PENDING,
    )
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(
        max_length=500,
        blank=True,
        help_text="Sanitized error info. Never contains secrets or raw payloads.",
    )
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Sanitized payload subset (event, reference, status) for debugging.",
    )

    class Meta:
        ordering = ("-received_at",)
        indexes = [
            models.Index(fields=["event_fingerprint"], name="webhook_fingerprint_idx"),
            models.Index(
                fields=["transaction_reference", "status"],
                name="webhook_ref_status_idx",
            ),
        ]

    def __str__(self):
        return f"Webhook {self.event_type} ({self.status})"
