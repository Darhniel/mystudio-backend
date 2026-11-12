"""Write-side business logic for payments."""

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime


from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.selectors import (
    get_booked_equipment_quantities,
    get_overlapping_blocks,
    get_overlapping_bookings,
)
from apps.payments.models import (
    Payment,
    PaymentStatus,
    WebhookEvent,
    WebhookEventStatus,
)
from apps.payments import webhook_utils
from apps.payments import paystack_client

logger = logging.getLogger("mystudio.payments")


def initialize_payment(*, booking_reference: str, access_token: str) -> dict:
    """Initialize a Paystack transaction for a pending booking.

    Security & Business Guarantees:
    1. Booking is authorized via high-entropy access token.
    2. Booking status is PENDING_PAYMENT and hold has not expired.
    3. No successful payment already exists for this booking.
    4. Existing valid INITIALIZED attempts are reused (prevents duplicate charges).
    5. Database transactions are NOT held open during external HTTP requests.
    6. The 10-minute booking hold is never extended.
    7. Payment amount is strictly copied from booking.total_amount_kobo.
    """
    now = timezone.now()

    # STEP 1: Validate and lock within a short transaction
    with transaction.atomic():
        booking = (
            Booking.objects.select_for_update()
            .filter(reference=booking_reference)
            .first()
        )

        if not booking:
            raise ValidationError({"booking": "Booking not found."})

        if not secrets.compare_digest(booking.access_token, access_token):
            raise ValidationError({"token": "Invalid or missing access token."})

        if booking.status == BookingStatus.EXPIRED:
            raise ValidationError(
                {"booking": "Booking has expired; payment cannot be initialized."}
            )

        if booking.status != BookingStatus.PENDING_PAYMENT:
            raise ValidationError({"booking": "Booking is not pending payment."})

        if booking.payment_hold_expires_at <= now:
            raise ValidationError({"booking": "Payment hold has expired."})

        if booking.payments.filter(status=PaymentStatus.SUCCESS).exists():
            raise ValidationError(
                {"booking": "Booking already has a successful payment."}
            )

        # STEP 2: Duplicate initialization protection
        # Reuse existing valid initialized attempt if available
        existing_payment = (
            booking.payments.filter(
                status__in=[PaymentStatus.INITIALIZING, PaymentStatus.INITIALIZED]
            )
            .order_by("-created_at")
            .first()
        )

        if existing_payment and existing_payment.status == PaymentStatus.INITIALIZED:
            return {
                "payment_reference": existing_payment.paystack_reference,
                "authorization_url": existing_payment.gateway_response.get(
                    "authorization_url"
                ),
                "access_code": existing_payment.gateway_response.get("access_code"),
                "booking_reference": booking.reference,
                "amount_kobo": existing_payment.amount_kobo,
                "currency": existing_payment.currency,
            }

        # STEP 3: Create new payment attempt record
        paystack_reference = f"PY-{booking.reference}-{uuid.uuid4().hex[:8]}"

        payment = Payment.objects.create(
            booking=booking,
            paystack_reference=paystack_reference,
            amount_kobo=booking.total_amount_kobo,  # Never trust frontend amount
            currency="NGN",
            status=PaymentStatus.INITIALIZING,
        )

    # STEP 4: HTTP request OUTSIDE the transaction to prevent holding DB locks
    try:
        paystack_data = paystack_client.initialize_transaction(
            amount_kobo=payment.amount_kobo,
            email=booking.customer_email,
            reference=paystack_reference,
            callback_url=settings.PAYSTACK_CALLBACK_URL,
        )
    except paystack_client.PaystackAPIError as e:
        # Handle initialization failure without corrupting the booking
        with transaction.atomic():
            payment_refreshed = Payment.objects.select_for_update().get(id=payment.id)
            payment_refreshed.status = PaymentStatus.FAILED
            payment_refreshed.gateway_response = {"error": str(e)}
            payment_refreshed.save(
                update_fields=["status", "gateway_response", "updated_at"]
            )

        logger.error(f"Paystack initialization failed for {paystack_reference}: {e}")
        raise ValidationError(
            {"payment": "Failed to initialize payment with Paystack."}
        )

    # STEP 5: Update payment to INITIALIZED
    with transaction.atomic():
        payment_refreshed = Payment.objects.select_for_update().get(id=payment.id)
        payment_refreshed.status = PaymentStatus.INITIALIZED
        payment_refreshed.initialized_at = timezone.now()
        payment_refreshed.gateway_response = {
            "authorization_url": paystack_data.get("authorization_url"),
            "access_code": paystack_data.get("access_code"),
            "reference": paystack_data.get("reference"),
        }
        payment_refreshed.save(
            update_fields=["status", "initialized_at", "gateway_response", "updated_at"]
        )

    return {
        "payment_reference": payment_refreshed.paystack_reference,
        "authorization_url": payment_refreshed.gateway_response["authorization_url"],
        "access_code": payment_refreshed.gateway_response["access_code"],
        "booking_reference": booking.reference,
        "amount_kobo": payment_refreshed.amount_kobo,
        "currency": payment_refreshed.currency,
    }


# Mapping of Paystack API statuses to our internal PaymentStatus
PAYSTACK_STATUS_MAP = {
    "success": PaymentStatus.SUCCESS,
    "failed": PaymentStatus.FAILED,
    "abandoned": PaymentStatus.ABANDONED,
    "pending": PaymentStatus.PENDING,
    "ongoing": PaymentStatus.ONGOING,
    "processing": PaymentStatus.PROCESSING,
    "reversed": PaymentStatus.REVERSED,
}


def verify_and_process_payment(*, reference: str, access_token: str) -> dict:
    """Securely verify a Paystack transaction and confirm the booking if eligible.

    This is the single source of truth for payment verification. It can be
    triggered by the frontend callback or a webhook. It strictly validates
    the provider response and uses row-level locking to prevent race conditions.
    """
    now = timezone.now()

    # STEP 1: Find payment and validate access token (outside transaction for speed)
    try:
        payment = Payment.objects.select_related("booking").get(
            paystack_reference=reference
        )
    except Payment.DoesNotExist:
        raise ValidationError({"reference": "Invalid or unknown payment reference."})

    if not secrets.compare_digest(payment.booking.access_token, access_token):
        raise ValidationError({"token": "Invalid or missing access token."})

    # STEP 2: Call Paystack API to verify (NEVER trust frontend claims)
    try:
        verified_data = paystack_client.verify_transaction(reference=reference)
    except paystack_client.PaystackAPIError as e:
        logger.error(f"Paystack verification failed for {reference}: {e}")
        raise ValidationError({"payment": f"Verification failed: {e}"})

    # STEP 3: Validate provider response data
    ps_reference = verified_data.get("reference")
    ps_amount = verified_data.get("amount", 0)
    ps_currency = (verified_data.get("currency") or "").upper()
    ps_status = (verified_data.get("status") or "").lower()
    ps_transaction_id = verified_data.get("id")
    ps_channel = verified_data.get("channel", "")
    ps_paid_at_raw = verified_data.get("paid_at")

    if ps_reference != reference:
        raise ValidationError({"reference": "Reference mismatch in provider response."})
    if ps_amount != payment.amount_kobo:
        raise ValidationError(
            {
                "amount": f"Amount mismatch. Expected {payment.amount_kobo}, got {ps_amount}."
            }
        )
    if ps_currency != "NGN":
        raise ValidationError(
            {"currency": f"Currency mismatch. Expected NGN, got {ps_currency}."}
        )

    target_status = PAYSTACK_STATUS_MAP.get(ps_status)
    if not target_status:
        raise ValidationError({"status": f"Unhandled Paystack status: {ps_status}"})

    # STEP 4: Database transaction with row-level locking
    with transaction.atomic():
        # Lock both records to prevent concurrent webhook/callback race conditions
        locked_payment = (
            Payment.objects.select_for_update().filter(id=payment.id).first()
        )
        locked_booking = (
            Booking.objects.select_for_update().filter(id=payment.booking_id).first()
        )

        if not locked_payment or not locked_booking:
            raise ValidationError({"system": "Booking or payment record not found."})

        # IDEMPOTENCY: Do not overwrite a previously successful payment
        if locked_payment.status in (
            PaymentStatus.SUCCESS,
            PaymentStatus.REQUIRES_REVIEW,
        ):
            logger.info(
                f"Payment {reference} already resolved as {locked_payment.status}. Idempotent return."
            )
            return {
                "status": locked_payment.status,
                "booking_status": locked_booking.status,
                "message": "Payment already processed.",
            }

        # Parse paid_at safely
        paid_at = None
        if ps_paid_at_raw:
            try:
                paid_at = parse_datetime(ps_paid_at_raw)
            except Exception:
                paid_at = now

        # STEP 5: Handle based on verified status
        if target_status == PaymentStatus.SUCCESS:
            # Determine if we can fulfill the booking
            can_fulfill = False

            # Check if booking is still valid (PENDING_PAYMENT or EXPIRED but slot is free)
            if locked_booking.status in (
                BookingStatus.PENDING_PAYMENT,
                BookingStatus.EXPIRED,
            ):
                overlapping_bookings = get_overlapping_bookings(
                    studio=locked_booking.studio,
                    start_time=locked_booking.start_time,
                    end_time=locked_booking.end_time,
                    exclude_booking_id=locked_booking.id,
                )
                overlapping_blocks = get_overlapping_blocks(
                    studio=locked_booking.studio,
                    start_time=locked_booking.start_time,
                    end_time=locked_booking.end_time,
                )

                equipment_available = True
                for be in locked_booking.equipment_items.all():
                    booked_qty = get_booked_equipment_quantities(
                        equipment_ids=[str(be.equipment_id)],
                        start_time=locked_booking.start_time,
                        end_time=locked_booking.end_time,
                        exclude_booking_id=locked_booking.id,
                    ).get(str(be.equipment_id), 0)

                    if booked_qty + be.quantity > be.equipment.stock_quantity:
                        equipment_available = False
                        break

                if (
                    not overlapping_bookings.exists()
                    and not overlapping_blocks.exists()
                    and equipment_available
                ):
                    can_fulfill = True

            if can_fulfill:
                # Auto-confirm (whether within hold period or late but still available)
                locked_booking.status = BookingStatus.CONFIRMED
                locked_booking.save(update_fields=["status", "updated_at"])
                locked_payment.status = PaymentStatus.SUCCESS
                logger.info(
                    f"Booking {locked_booking.reference} confirmed via verified payment {reference}"
                )
            else:
                # Late payment where slot is taken: flag for manual review
                locked_payment.status = PaymentStatus.REQUIRES_REVIEW
                locked_payment.review_reason = (
                    "Late payment: requested slot is no longer available."
                )
                logger.warning(
                    f"Payment {reference} succeeded, but booking {locked_booking.reference} "
                    f"slot is no longer available. Flagged for review."
                )

        elif target_status in (
            PaymentStatus.PENDING,
            PaymentStatus.ONGOING,
            PaymentStatus.PROCESSING,
        ):
            # Transient states: update status but do NOT mark as failed
            locked_payment.status = target_status
            logger.info(
                f"Payment {reference} is {target_status}. Awaiting final status."
            )

        elif target_status in (
            PaymentStatus.FAILED,
            PaymentStatus.ABANDONED,
            PaymentStatus.REVERSED,
        ):
            # Terminal failure states
            locked_payment.status = target_status
            logger.info(f"Payment {reference} resolved as {target_status}.")

        # STEP 6: Persist payment metadata
        locked_payment.paystack_transaction_id = (
            str(ps_transaction_id) if ps_transaction_id else None
        )
        locked_payment.payment_channel = ps_channel
        if target_status == PaymentStatus.SUCCESS:
            locked_payment.paid_at = paid_at
        locked_payment.verified_at = now
        locked_payment.gateway_response = {
            "status": verified_data.get("status"),
            "message": verified_data.get("message"),
            "gateway_response": verified_data.get("gateway_response"),
            "channel": ps_channel,
            "authorization": verified_data.get("authorization", {}),
        }

        locked_payment.save(
            update_fields=[
                "status",
                "paystack_transaction_id",
                "payment_channel",
                "paid_at",
                "verified_at",
                "gateway_response",
                "review_reason",
                "updated_at",
            ]
        )

        return {
            "status": locked_payment.status,
            "booking_status": locked_booking.status,
            "payment_reference": locked_payment.paystack_reference,
        }


def process_paystack_webhook(
    *, raw_body: bytes, signature_header: str, legacy_signature_header: str = None
) -> dict:
    """
    Process a Paystack webhook event securely and idempotently.

    Returns a dict for the HTTP response. Raises ValueError for temporary
    failures (triggering a 500 response so Paystack retries).
    """
    # 1. Validate signature
    if signature_header:
        webhook_utils.verify_current_signature(raw_body, signature_header)
    elif legacy_signature_header:
        webhook_utils.verify_legacy_signature(raw_body, legacy_signature_header)
    else:
        raise webhook_utils.WebhookSignatureError(
            "No valid signature header provided"
        )

    # 2. Parse payload
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON payload")

    event_type = payload.get("event")
    event_data = payload.get("data", {})
    reference = event_data.get("reference")

    # 3. Create or get WebhookEvent record for idempotency
    fingerprint = hashlib.sha256(raw_body).hexdigest()

    with transaction.atomic():
        webhook_event, created = WebhookEvent.objects.select_for_update().get_or_create(
            event_fingerprint=fingerprint,
            defaults={
                "event_type": event_type,
                "transaction_reference": reference,
                "status": WebhookEventStatus.PENDING,
                "raw_payload": {
                    "event": event_type,
                    "reference": reference,
                    "status": event_data.get("status"),
                },
            },
        )

        if not created:
            if webhook_event.status == WebhookEventStatus.SUCCESS:
                logger.info(f"Duplicate webhook event already processed: {fingerprint}")
                return {"status": "success", "message": "Event already processed"}
            elif webhook_event.status == WebhookEventStatus.FAILED:
                logger.warning(
                    f"Retrying previously failed webhook event: {fingerprint}"
                )
                webhook_event.status = WebhookEventStatus.PENDING
                webhook_event.save(update_fields=["status"])

    # A successful duplicate can return after the transaction releases its lock.
    if not created and webhook_event.status == WebhookEventStatus.SUCCESS:
        logger.info(f"Duplicate webhook event already processed: {fingerprint}")
        return {"status": "success", "message": "Event already processed"}

    def finish_event(*, status_value, error_message=""):
        with transaction.atomic():
            event = WebhookEvent.objects.select_for_update().get(pk=webhook_event.pk)
            event.status = status_value
            event.error_message = error_message
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "error_message", "processed_at"])

    if event_type != "charge.success":
        finish_event(status_value=WebhookEventStatus.SUCCESS)
        return {
            "status": "ignored",
            "message": f"Event type {event_type} not handled",
        }

    if not reference:
        finish_event(
            status_value=WebhookEventStatus.FAILED,
            error_message="Missing transaction reference",
        )
        return {"status": "failed", "message": "Missing reference"}

    try:
        payment = Payment.objects.select_related("booking").get(
            paystack_reference=reference
        )
    except Payment.DoesNotExist:
        finish_event(
            status_value=WebhookEventStatus.FAILED,
            error_message="Unknown payment reference",
        )
        return {"status": "failed", "message": "Unknown reference"}

    if payment.status in (PaymentStatus.SUCCESS, PaymentStatus.REQUIRES_REVIEW):
        finish_event(status_value=WebhookEventStatus.SUCCESS)
        return {
            "status": payment.status,
            "booking_status": payment.booking.status,
            "message": "Payment already processed.",
        }

    try:
        # A valid signature authenticates the webhook; the token is passed to
        # the shared verification service for its defense-in-depth check.
        result = verify_and_process_payment(
            reference=reference,
            access_token=payment.booking.access_token,
        )
    except ValidationError as e:
        finish_event(
            status_value=WebhookEventStatus.FAILED,
            error_message=str(e)[:500],
        )
        validation_fields = getattr(e, "message_dict", {})
        if any(
            field in validation_fields for field in ("amount", "currency", "reference")
        ):
            with transaction.atomic():
                locked_payment = Payment.objects.select_for_update().get(pk=payment.pk)
                if locked_payment.status not in (
                    PaymentStatus.SUCCESS,
                    PaymentStatus.REQUIRES_REVIEW,
                ):
                    locked_payment.status = PaymentStatus.FAILED
                    locked_payment.gateway_response = {
                        "error": "amount_currency_mismatch"
                    }
                    locked_payment.save(
                        update_fields=["status", "gateway_response", "updated_at"]
                    )
        return {"status": "failed", "message": "Validation failed"}
    except Exception as e:
        finish_event(
            status_value=WebhookEventStatus.FAILED,
            error_message="Temporary processing error",
        )
        logger.error(f"Webhook temporary processing failed for {reference}: {e}")
        raise ValueError(f"Processing failed: {e}") from e

    finish_event(status_value=WebhookEventStatus.SUCCESS)
    return result
