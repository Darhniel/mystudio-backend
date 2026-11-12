"""Tests for payment services."""

import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.utils import make_aware_datetime
from apps.payments.models import Payment, PaymentStatus
from apps.payments.paystack_client import PaystackAPIError
from apps.payments.services import initialize_payment, process_paystack_webhook
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


def process_success_webhook(reference):
    raw_body = json.dumps(
        {
            "event": "charge.success",
            "data": {"reference": reference, "status": "success"},
        }
    ).encode("utf-8")
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{raw_body.decode('utf-8')}".encode("utf-8")
    signature = hmac.new(
        settings.PAYSTACK_WEBHOOK_SECRET.encode("utf-8"),
        signed_payload,
        hashlib.sha512,
    ).hexdigest()
    return process_paystack_webhook(
        raw_body=raw_body,
        signature_header=f"t={timestamp},v1={signature}",
    )


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


@pytest.fixture
def studio(db):
    return Studio.objects.create(
        name="Service Test Studio",
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
def expired_booking(studio, tomorrow):
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
        status=BookingStatus.EXPIRED,
        payment_hold_expires_at=timezone.now() - timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.initialize_transaction")
def test_initialize_payment_creates_initialized_record(mock_init, pending_booking):
    mock_init.return_value = {
        "authorization_url": "https://paystack.test/pay/123",
        "reference": "PY-TEST-123",
        "access_code": "abc",
    }

    result = initialize_payment(
        booking_reference=pending_booking.reference,
        access_token=pending_booking.access_token,
    )

    assert result["authorization_url"] == "https://paystack.test/pay/123"
    payment = Payment.objects.get(
        paystack_reference=result["payment_reference"]
    )
    assert payment.status == PaymentStatus.INITIALIZED
    assert payment.amount_kobo == pending_booking.total_amount_kobo
    assert payment.currency == "NGN"
    assert payment.initialized_at is not None


@patch("apps.payments.services.paystack_client.initialize_transaction")
def test_initialize_payment_fails_gracefully(mock_init, pending_booking):
    mock_init.side_effect = PaystackAPIError("Paystack down")

    with pytest.raises(ValidationError):
        initialize_payment(
            booking_reference=pending_booking.reference,
            access_token=pending_booking.access_token,
        )

    payment = Payment.objects.get(booking=pending_booking)
    assert payment.status == PaymentStatus.FAILED


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_successful_payment_not_downgraded_by_duplicate_event(
    mock_verify, pending_booking
):
    """A SUCCESS payment cannot be downgraded by a duplicate webhook."""
    payment = Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-IDEM",
        amount_kobo=200_000,
        status=PaymentStatus.SUCCESS,
        paystack_transaction_id="TXN-IDEM",
        paid_at=timezone.now(),
    )

    process_success_webhook("REF-IDEM")

    mock_verify.assert_not_called()
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCESS
    pending_booking.refresh_from_db()
    assert pending_booking.status == BookingStatus.PENDING_PAYMENT  # unchanged


# ---------------------------------------------------------------------------
# Late payment auto-confirm
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_late_payment_auto_confirms_if_available(mock_verify, expired_booking):
    payment = Payment.objects.create(
        booking=expired_booking,
        paystack_reference="REF-LATE-OK",
        amount_kobo=200_000,
        status=PaymentStatus.INITIALIZED,
    )

    mock_verify.return_value = {
        "reference": "REF-LATE-OK",
        "amount": 200_000,
        "currency": "NGN",
        "id": "TXN-LATE-OK",
        "status": "success",
        "channel": "card",
        "paid_at": "2026-11-12T10:05:00.000Z",
        "gateway_response": "Approved",
        "authorization": {},
        "message": "Charge attempted",
    }

    process_success_webhook("REF-LATE-OK")

    expired_booking.refresh_from_db()
    payment.refresh_from_db()

    assert expired_booking.status == BookingStatus.CONFIRMED
    assert payment.status == PaymentStatus.SUCCESS
    assert payment.payment_channel == "card"
    assert payment.paid_at is not None
    assert payment.verified_at is not None


# ---------------------------------------------------------------------------
# Late payment flagging
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_late_payment_flagged_if_unavailable(mock_verify, expired_booking, tomorrow):
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)
    Booking.objects.create(
        studio=expired_booking.studio,
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

    payment = Payment.objects.create(
        booking=expired_booking,
        paystack_reference="REF-LATE-FAIL",
        amount_kobo=200_000,
        status=PaymentStatus.INITIALIZED,
    )

    mock_verify.return_value = {
        "reference": "REF-LATE-FAIL",
        "amount": 200_000,
        "currency": "NGN",
        "id": "TXN-LATE-FAIL",
        "status": "success",
        "channel": "card",
        "paid_at": "2026-11-12T10:05:00.000Z",
        "gateway_response": "Approved",
        "authorization": {},
        "message": "Charge attempted",
    }

    process_success_webhook("REF-LATE-FAIL")

    expired_booking.refresh_from_db()
    payment.refresh_from_db()

    assert expired_booking.status == BookingStatus.EXPIRED
    assert payment.status == PaymentStatus.REQUIRES_REVIEW
    assert "no longer available" in payment.review_reason


# ---------------------------------------------------------------------------
# Amount / currency validation
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_amount_mismatch_marks_payment_failed(mock_verify, pending_booking):
    payment = Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-MISMATCH",
        amount_kobo=200_000,
        status=PaymentStatus.INITIALIZED,
    )

    mock_verify.return_value = {
        "reference": "REF-MISMATCH",
        "amount": 100_000,  # wrong amount
        "currency": "NGN",
        "id": "TXN-MISMATCH",
        "status": "success",
    }

    process_success_webhook("REF-MISMATCH")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED
    assert payment.gateway_response.get("error") == "amount_currency_mismatch"


@patch("apps.payments.services.paystack_client.verify_transaction")
def test_currency_mismatch_marks_payment_failed(mock_verify, pending_booking):
    payment = Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-CUR",
        amount_kobo=200_000,
        status=PaymentStatus.INITIALIZED,
    )

    mock_verify.return_value = {
        "reference": "REF-CUR",
        "amount": 200_000,
        "currency": "USD",  # wrong currency
        "id": "TXN-CUR",
        "status": "success",
    }

    process_success_webhook("REF-CUR")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED
