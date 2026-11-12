"""Tests for Paystack webhook handling."""

import hashlib
import hmac
import json
import time
from unittest.mock import patch, MagicMock

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.utils import make_aware_datetime
from apps.payments.models import (
    Payment,
    PaymentStatus,
    WebhookEvent,
    WebhookEventStatus,
)
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def studio(db):
    return Studio.objects.create(
        name="Webhook Studio", hourly_rate_kobo=100_000, capacity=10
    )


@pytest.fixture
def booking(studio):
    tomorrow = timezone.localdate() + timezone.timedelta(days=1)
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
        payment_hold_expires_at=timezone.now() + timezone.timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )


@pytest.fixture
def payment(booking):
    return Payment.objects.create(
        booking=booking,
        paystack_reference="REF-WEBHOOK-123",
        amount_kobo=200_000,
        currency="NGN",
        status=PaymentStatus.INITIALIZED,
    )


@pytest.fixture(autouse=True)
def webhook_settings(settings):
    settings.PAYSTACK_WEBHOOK_SECRET = "whsec_test_secret"
    settings.PAYSTACK_SECRET_KEY = "sk_test_secret"
    return settings


def generate_current_signature(raw_body, secret, timestamp=None):
    if timestamp is None:
        timestamp = int(time.time())
    payload_to_sign = f"{timestamp}.{raw_body.decode('utf-8')}"
    sig = hmac.new(
        secret.encode("utf-8"), payload_to_sign.encode("utf-8"), hashlib.sha512
    ).hexdigest()
    return f"t={timestamp},v1={sig}"


def generate_legacy_signature(raw_body, secret):
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()


# ---------------------------------------------------------------------------
# Signature Validation
# ---------------------------------------------------------------------------
def test_valid_current_signature(webhook_settings, api_client, payment):
    payload = {
        "event": "charge.success",
        "data": {"reference": "REF-WEBHOOK-123", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_current_signature(raw_body, webhook_settings.PAYSTACK_WEBHOOK_SECRET)

    with patch("apps.payments.services.verify_and_process_payment") as mock_verify:
        mock_verify.return_value = {"status": "success"}
        response = api_client.post(
            "/api/v1/payments/webhook/",
            data=raw_body,
            content_type="application/json",
            HTTP_PAYSTACK_SIGNATURE=sig,
        )

    assert response.status_code == 200
    assert WebhookEvent.objects.filter(status=WebhookEventStatus.SUCCESS).exists()


def test_invalid_signature(webhook_settings, api_client):
    raw_body = json.dumps({"event": "charge.success"}).encode("utf-8")
    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE="t=123,v1=invalid",
    )
    assert response.status_code == 401


def test_missing_signature(api_client):
    raw_body = json.dumps({"event": "charge.success"}).encode("utf-8")
    response = api_client.post(
        "/api/v1/payments/webhook/", data=raw_body, content_type="application/json"
    )
    assert response.status_code == 401


def test_expired_timestamp(webhook_settings, api_client):
    raw_body = json.dumps({"event": "charge.success"}).encode("utf-8")
    # Timestamp from 10 minutes ago
    sig = generate_current_signature(
        raw_body,
        webhook_settings.PAYSTACK_WEBHOOK_SECRET,
        timestamp=int(time.time()) - 600,
    )
    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE=sig,
    )
    assert response.status_code == 401


def test_multiple_v1_signatures(webhook_settings, api_client, payment):
    payload = {
        "event": "charge.success",
        "data": {"reference": "REF-WEBHOOK-123", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    timestamp = int(time.time())

    # One valid, one invalid signature
    valid_sig = hmac.new(
        webhook_settings.PAYSTACK_WEBHOOK_SECRET.encode("utf-8"),
        f"{timestamp}.{raw_body.decode('utf-8')}".encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    sig_header = f"t={timestamp},v1=invalid_sig,v1={valid_sig}"

    with patch("apps.payments.services.verify_and_process_payment") as mock_verify:
        mock_verify.return_value = {"status": "success"}
        response = api_client.post(
            "/api/v1/payments/webhook/",
            data=raw_body,
            content_type="application/json",
            HTTP_PAYSTACK_SIGNATURE=sig_header,
        )

    assert response.status_code == 200


def test_legacy_signature_fallback(webhook_settings, api_client, payment):
    payload = {
        "event": "charge.success",
        "data": {"reference": "REF-WEBHOOK-123", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_legacy_signature(raw_body, webhook_settings.PAYSTACK_SECRET_KEY)

    with patch("apps.payments.services.verify_and_process_payment") as mock_verify:
        mock_verify.return_value = {"status": "success"}
        response = api_client.post(
            "/api/v1/payments/webhook/",
            data=raw_body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=sig,
        )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Event Processing & Idempotency
# ---------------------------------------------------------------------------
@patch("apps.payments.services.verify_and_process_payment")
def test_charge_success_processing(mock_verify, api_client, payment):
    mock_verify.return_value = {"status": "success", "booking_status": "confirmed"}
    payload = {
        "event": "charge.success",
        "data": {"reference": "REF-WEBHOOK-123", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_current_signature(raw_body, "whsec_test_secret")

    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE=sig,
    )

    assert response.status_code == 200
    assert response.data["status"] == "success"
    mock_verify.assert_called_once()


@patch("apps.payments.services.verify_and_process_payment")
def test_duplicate_webhook_delivery(mock_verify, api_client, payment):
    payload = {
        "event": "charge.success",
        "data": {"reference": "REF-WEBHOOK-123", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_current_signature(raw_body, "whsec_test_secret")

    # First request
    mock_verify.return_value = {"status": "success"}
    api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE=sig,
    )

    # Second request (duplicate)
    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE=sig,
    )

    assert response.status_code == 200
    assert response.data["message"] == "Event already processed"
    assert mock_verify.call_count == 1  # Not called again


def test_unknown_payment_reference(webhook_settings, api_client):
    payload = {
        "event": "charge.success",
        "data": {"reference": "UNKNOWN-REF", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_current_signature(raw_body, webhook_settings.PAYSTACK_WEBHOOK_SECRET)

    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE=sig,
    )

    # Returns 200 to stop retries for permanent failures like unknown ref
    assert response.status_code == 200
    assert response.data["status"] == "failed"
    assert WebhookEvent.objects.filter(
        status=WebhookEventStatus.FAILED, error_message="Unknown payment reference"
    ).exists()


@patch("apps.payments.services.verify_and_process_payment")
def test_validation_failure_does_not_retry(mock_verify, api_client, payment):
    mock_verify.side_effect = ValidationError("Amount mismatch")
    payload = {
        "event": "charge.success",
        "data": {"reference": "REF-WEBHOOK-123", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_current_signature(raw_body, "whsec_test_secret")

    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE=sig,
    )

    # Returns 200 because ValidationError is a permanent business logic failure
    assert response.status_code == 200
    assert response.data["status"] == "failed"
    assert WebhookEvent.objects.filter(status=WebhookEventStatus.FAILED).exists()


@patch("apps.payments.services.verify_and_process_payment")
def test_temporary_failure_triggers_500_for_retry(mock_verify, api_client, payment):
    mock_verify.side_effect = Exception("Database connection lost")
    payload = {
        "event": "charge.success",
        "data": {"reference": "REF-WEBHOOK-123", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_current_signature(raw_body, "whsec_test_secret")

    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE=sig,
    )

    # Returns 500 so Paystack will retry
    assert response.status_code == 500
    assert WebhookEvent.objects.filter(
        status=WebhookEventStatus.FAILED, error_message="Temporary processing error"
    ).exists()


@patch("apps.payments.services.verify_and_process_payment")
def test_late_successful_payment_flagged(mock_verify, api_client, payment):
    # Simulate the service returning REQUIRES_REVIEW due to late payment logic
    mock_verify.return_value = {
        "status": "requires_review",
        "booking_status": "expired",
    }
    payload = {
        "event": "charge.success",
        "data": {"reference": "REF-WEBHOOK-123", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_current_signature(raw_body, "whsec_test_secret")

    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE=sig,
    )

    assert response.status_code == 200
    assert response.data["status"] == "requires_review"
    assert WebhookEvent.objects.filter(status=WebhookEventStatus.SUCCESS).exists()
