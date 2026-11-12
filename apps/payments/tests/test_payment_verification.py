"""Tests for payment verification and booking confirmation."""

import pytest
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.utils import make_aware_datetime
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import verify_and_process_payment
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


@pytest.fixture
def studio(db):
    return Studio.objects.create(
        name="Verify Test Studio",
        hourly_rate_kobo=100_000,
        capacity=10,
    )


@pytest.fixture
def pending_booking(studio, tomorrow):
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)
    return Booking.objects.create(
        studio=studio,
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=100_000,
        studio_subtotal_kobo=200_000,
        equipment_subtotal_kobo=0,
        total_amount_kobo=200_000,
        status=BookingStatus.PENDING_PAYMENT,
        payment_hold_expires_at=timezone.now() + timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )


@pytest.fixture
def initialized_payment(pending_booking):
    return Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-VERIFY-123",
        amount_kobo=200_000,
        currency="NGN",
        status=PaymentStatus.INITIALIZED,
    )


# ---------------------------------------------------------------------------
# Successful Verification & Confirmation
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_verify_success_confirms_booking(
    mock_verify, pending_booking, initialized_payment
):
    mock_verify.return_value = {
        "reference": "REF-VERIFY-123",
        "amount": 200_000,
        "currency": "NGN",
        "status": "success",
        "id": "TXN-12345",
        "channel": "card",
        "paid_at": "2026-11-12T10:05:00.000Z",
        "message": "Charge successful",
        "gateway_response": "Approved",
        "authorization": {"auth_code": "AUTH123"},
    }

    result = verify_and_process_payment(
        reference="REF-VERIFY-123",
        access_token=pending_booking.access_token,
    )

    assert result["status"] == PaymentStatus.SUCCESS
    assert result["booking_status"] == BookingStatus.CONFIRMED

    initialized_payment.refresh_from_db()
    pending_booking.refresh_from_db()

    assert initialized_payment.status == PaymentStatus.SUCCESS
    assert initialized_payment.paystack_transaction_id == "TXN-12345"
    assert initialized_payment.payment_channel == "card"
    assert initialized_payment.paid_at is not None
    assert initialized_payment.verified_at is not None
    assert pending_booking.status == BookingStatus.CONFIRMED


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_verify_idempotency_prevents_double_confirmation(
    mock_verify, pending_booking, initialized_payment
):
    # Pre-mark as success to simulate a previous successful verification
    initialized_payment.status = PaymentStatus.SUCCESS
    initialized_payment.save()
    pending_booking.status = BookingStatus.CONFIRMED
    pending_booking.save()

    mock_verify.return_value = {
        "reference": "REF-VERIFY-123",
        "amount": 200_000,
        "currency": "NGN",
        "status": "success",
        "id": "TXN-12345",
    }

    result = verify_and_process_payment(
        reference="REF-VERIFY-123",
        access_token=pending_booking.access_token,
    )

    # Should return early without modifying anything
    assert result["message"] == "Payment already processed."
    mock_verify.assert_called_once()  # Called, but state wasn't changed

    pending_booking.refresh_from_db()
    assert pending_booking.status == BookingStatus.CONFIRMED


# ---------------------------------------------------------------------------
# Validation & Security
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_verify_rejects_invalid_token(
    mock_verify, pending_booking, initialized_payment
):
    with pytest.raises(ValidationError) as exc_info:
        verify_and_process_payment(
            reference="REF-VERIFY-123",
            access_token="wrong-token",
        )
    assert "token" in exc_info.value.message_dict


@patch("apps.payments.services.paystack_client.verify_transaction")
def test_verify_rejects_amount_mismatch(
    mock_verify, pending_booking, initialized_payment
):
    mock_verify.return_value = {
        "reference": "REF-VERIFY-123",
        "amount": 999_999,  # Mismatch
        "currency": "NGN",
        "status": "success",
    }

    with pytest.raises(ValidationError) as exc_info:
        verify_and_process_payment(
            reference="REF-VERIFY-123",
            access_token=pending_booking.access_token,
        )
    assert "amount" in exc_info.value.message_dict


@patch("apps.payments.services.paystack_client.verify_transaction")
def test_verify_rejects_currency_mismatch(
    mock_verify, pending_booking, initialized_payment
):
    mock_verify.return_value = {
        "reference": "REF-VERIFY-123",
        "amount": 200_000,
        "currency": "USD",  # Mismatch
        "status": "success",
    }

    with pytest.raises(ValidationError) as exc_info:
        verify_and_process_payment(
            reference="REF-VERIFY-123",
            access_token=pending_booking.access_token,
        )
    assert "currency" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Transient Statuses (Pending, Ongoing, Processing)
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_verify_handles_pending_status_without_failing(
    mock_verify, pending_booking, initialized_payment
):
    mock_verify.return_value = {
        "reference": "REF-VERIFY-123",
        "amount": 200_000,
        "currency": "NGN",
        "status": "pending",
    }

    result = verify_and_process_payment(
        reference="REF-VERIFY-123",
        access_token=pending_booking.access_token,
    )

    assert result["status"] == PaymentStatus.PENDING

    initialized_payment.refresh_from_db()
    assert initialized_payment.status == PaymentStatus.PENDING
    pending_booking.refresh_from_db()
    assert pending_booking.status == BookingStatus.PENDING_PAYMENT  # Unchanged


# ---------------------------------------------------------------------------
# Late Payment Handling
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_late_payment_auto_confirms_if_slot_available(mock_verify, studio, tomorrow):
    # Create an expired booking
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)
    expired_booking = Booking.objects.create(
        studio=studio,
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=100_000,
        studio_subtotal_kobo=200_000,
        equipment_subtotal_kobo=0,
        total_amount_kobo=200_000,
        status=BookingStatus.EXPIRED,
        payment_hold_expires_at=timezone.now() - timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )
    payment = Payment.objects.create(
        booking=expired_booking,
        paystack_reference="REF-LATE-OK",
        amount_kobo=200_000,
        currency="NGN",
        status=PaymentStatus.INITIALIZED,
    )

    mock_verify.return_value = {
        "reference": "REF-LATE-OK",
        "amount": 200_000,
        "currency": "NGN",
        "status": "success",
        "id": "TXN-LATE",
        "channel": "card",
        "paid_at": "2026-11-12T10:05:00.000Z",
    }

    result = verify_and_process_payment(
        reference="REF-LATE-OK",
        access_token=expired_booking.access_token,
    )

    assert result["status"] == PaymentStatus.SUCCESS
    assert result["booking_status"] == BookingStatus.CONFIRMED

    expired_booking.refresh_from_db()
    assert expired_booking.status == BookingStatus.CONFIRMED


@patch("apps.payments.services.paystack_client.verify_transaction")
def test_late_payment_flagged_if_slot_taken(mock_verify, studio, tomorrow):
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    # Expired booking
    expired_booking = Booking.objects.create(
        studio=studio,
        customer_full_name="Test User",
        customer_email="test@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=100_000,
        studio_subtotal_kobo=200_000,
        equipment_subtotal_kobo=0,
        total_amount_kobo=200_000,
        status=BookingStatus.EXPIRED,
        payment_hold_expires_at=timezone.now() - timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )
    payment = Payment.objects.create(
        booking=expired_booking,
        paystack_reference="REF-LATE-FAIL",
        amount_kobo=200_000,
        currency="NGN",
        status=PaymentStatus.INITIALIZED,
    )

    # Another user took the slot
    Booking.objects.create(
        studio=studio,
        customer_full_name="Other User",
        customer_email="other@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=100_000,
        studio_subtotal_kobo=200_000,
        equipment_subtotal_kobo=0,
        total_amount_kobo=200_000,
        status=BookingStatus.CONFIRMED,
        terms_accepted_at=timezone.now(),
    )

    mock_verify.return_value = {
        "reference": "REF-LATE-FAIL",
        "amount": 200_000,
        "currency": "NGN",
        "status": "success",
        "id": "TXN-LATE-FAIL",
        "channel": "card",
        "paid_at": "2026-11-12T10:05:00.000Z",
    }

    result = verify_and_process_payment(
        reference="REF-LATE-FAIL",
        access_token=expired_booking.access_token,
    )

    assert result["status"] == PaymentStatus.REQUIRES_REVIEW
    assert result["booking_status"] == BookingStatus.EXPIRED

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REQUIRES_REVIEW
    assert "no longer available" in payment.review_reason
