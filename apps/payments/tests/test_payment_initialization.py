"""Tests for payment initialization service."""

import pytest
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.utils import make_aware_datetime
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import initialize_payment
from apps.payments.paystack_client import PaystackAPIError
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


@pytest.fixture
def studio(db):
    return Studio.objects.create(
        name="Init Test Studio",
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


@patch("apps.payments.services.paystack_client.initialize_transaction")
def test_initialize_payment_success(mock_init, pending_booking):
    mock_init.return_value = {
        "authorization_url": "https://paystack.test/pay/123",
        "reference": "PY-TEST-123",
        "access_code": "abc",
    }

    result = initialize_payment(
        booking_reference=pending_booking.reference,
        access_token=pending_booking.access_token,
    )

    assert result["payment_reference"].startswith("PY-")
    assert result["authorization_url"] == "https://paystack.test/pay/123"
    assert result["amount_kobo"] == 200_000
    assert result["currency"] == "NGN"

    payment = Payment.objects.get(paystack_reference=result["payment_reference"])
    assert payment.status == PaymentStatus.INITIALIZED
    assert payment.amount_kobo == 200_000


@patch("apps.payments.services.paystack_client.initialize_transaction")
def test_initialize_payment_reuses_existing_initialized(mock_init, pending_booking):
    # Create an existing initialized payment
    Payment.objects.create(
        booking=pending_booking,
        paystack_reference="PY-EXISTING-123",
        amount_kobo=200_000,
        currency="NGN",
        status=PaymentStatus.INITIALIZED,
        gateway_response={
            "authorization_url": "https://paystack.test/pay/existing",
            "access_code": "xyz",
            "reference": "PY-EXISTING-123",
        },
    )

    result = initialize_payment(
        booking_reference=pending_booking.reference,
        access_token=pending_booking.access_token,
    )

    # Should return existing data WITHOUT calling Paystack API
    mock_init.assert_not_called()
    assert result["payment_reference"] == "PY-EXISTING-123"
    assert result["authorization_url"] == "https://paystack.test/pay/existing"


def test_initialize_payment_invalid_token(pending_booking):
    with pytest.raises(ValidationError) as exc_info:
        initialize_payment(
            booking_reference=pending_booking.reference,
            access_token="invalid-token",
        )
    assert "token" in exc_info.value.message_dict


def test_initialize_payment_expired_hold(studio, tomorrow):
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

    with pytest.raises(ValidationError) as exc_info:
        initialize_payment(
            booking_reference=expired_booking.reference,
            access_token=expired_booking.access_token,
        )
    assert "expired" in str(exc_info.value).lower()


@patch("apps.payments.services.paystack_client.initialize_transaction")
def test_initialize_payment_handles_api_error(mock_init, pending_booking):
    mock_init.side_effect = PaystackAPIError("Gateway timeout")

    with pytest.raises(ValidationError) as exc_info:
        initialize_payment(
            booking_reference=pending_booking.reference,
            access_token=pending_booking.access_token,
        )

    assert "payment" in exc_info.value.message_dict

    # Verify payment record was marked as FAILED, not left dangling in INITIALIZING
    payment = Payment.objects.filter(booking=pending_booking).first()
    assert payment.status == PaymentStatus.FAILED
    assert "Gateway timeout" in payment.gateway_response.get("error", "")
