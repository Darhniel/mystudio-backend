"""API endpoint tests for payments."""

import hashlib
import hmac
import json
import time
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.bookings.models import Booking, BookingStatus
from apps.bookings.utils import make_aware_datetime
from apps.payments.models import Payment, PaymentStatus
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        email="admin@mystudio.ng",
        password="password123",
        role=Role.OWNER,
        is_staff=True,
    )


@pytest.fixture
def studio(db):
    return Studio.objects.create(
        name="API Test Studio",
        hourly_rate_kobo=250_000,
        capacity=10,
    )


@pytest.fixture
def pending_booking(studio):
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
        studio_hourly_price_kobo=250_000,
        studio_subtotal_kobo=500_000,
        equipment_subtotal_kobo=0,
        total_amount_kobo=500_000,
        status=BookingStatus.PENDING_PAYMENT,
        payment_hold_expires_at=timezone.now() + timezone.timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )


@pytest.fixture
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


# ---------------------------------------------------------------------------
# Initialize Payment
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.initialize_transaction")
def test_payment_initialize_success(mock_init, pending_booking, api_client):
    mock_init.return_value = {
        "authorization_url": "https://checkout.paystack.com/test",
        "reference": "PY-TEST-123",
        "access_code": "abc",
    }

    payload = {
        "booking_reference": pending_booking.reference,
        "booking_access_token": pending_booking.access_token,
    }

    response = api_client.post("/api/v1/payments/initialize/", payload, format="json")

    assert response.status_code == 200
    assert response.data["payment_reference"].startswith("PY-")
    assert response.data["payment_reference"] == mock_init.call_args.kwargs["reference"]
    assert response.data["authorization_url"] == "https://checkout.paystack.com/test"
    assert response.data["amount_kobo"] == 500_000
    assert response.data["currency"] == "NGN"
    assert response.data["booking_reference"] == pending_booking.reference


def test_payment_initialize_invalid_token(pending_booking, api_client):
    payload = {
        "booking_reference": pending_booking.reference,
        "booking_access_token": "invalid-token",
    }

    response = api_client.post("/api/v1/payments/initialize/", payload, format="json")

    assert response.status_code == 400
    assert "booking_access_token" in response.data


# ---------------------------------------------------------------------------
# Verify Payment
# ---------------------------------------------------------------------------
@patch("apps.payments.services.paystack_client.verify_transaction")
def test_payment_verify_success(mock_verify, pending_booking, api_client):
    Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-VERIFY-123",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.INITIALIZED,
    )

    mock_verify.return_value = {
        "reference": "REF-VERIFY-123",
        "amount": 500_000,
        "currency": "NGN",
        "status": "success",
        "id": "TXN-123",
        "channel": "card",
        "paid_at": "2026-11-12T10:05:00.000Z",
    }

    payload = {
        "reference": "REF-VERIFY-123",
        "booking_access_token": pending_booking.access_token,
    }

    response = api_client.post("/api/v1/payments/verify/", payload, format="json")

    assert response.status_code == 200
    assert response.data["status"] == PaymentStatus.SUCCESS
    assert response.data["booking_status"] == BookingStatus.CONFIRMED


def test_payment_verify_invalid_token(pending_booking, api_client):
    Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-VERIFY-456",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.INITIALIZED,
    )

    payload = {
        "reference": "REF-VERIFY-456",
        "booking_access_token": "invalid-token",
    }

    response = api_client.post("/api/v1/payments/verify/", payload, format="json")

    assert response.status_code == 400
    assert "booking_access_token" in response.data


# ---------------------------------------------------------------------------
# Payment Status
# ---------------------------------------------------------------------------
def test_payment_status_success(pending_booking, api_client):
    payment = Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-STATUS-123",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.SUCCESS,
        paid_at=timezone.now(),
    )

    response = api_client.get(
        f"/api/v1/payments/{payment.paystack_reference}/status/",
        {"booking_access_token": pending_booking.access_token},
    )

    assert response.status_code == 200
    assert response.data["payment_reference"] == "REF-STATUS-123"
    assert response.data["payment_status"] == PaymentStatus.SUCCESS
    assert response.data["amount_kobo"] == 500_000
    assert response.data["booking_reference"] == pending_booking.reference


def test_payment_status_invalid_token(pending_booking, api_client):
    payment = Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-STATUS-456",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.INITIALIZED,
    )

    response = api_client.get(
        f"/api/v1/payments/{payment.paystack_reference}/status/",
        {"booking_access_token": "invalid-token"},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------
@patch("apps.payments.services.verify_and_process_payment")
def test_webhook_success(mock_verify, webhook_settings, pending_booking, api_client):
    Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-WEBHOOK-123",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.INITIALIZED,
    )

    mock_verify.return_value = {"status": "success", "booking_status": "confirmed"}
    payload = {
        "event": "charge.success",
        "data": {"reference": "REF-WEBHOOK-123", "status": "success"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = generate_current_signature(raw_body, webhook_settings.PAYSTACK_WEBHOOK_SECRET)

    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE=sig,
    )

    assert response.status_code == 200
    assert response.data["status"] == "success"


def test_webhook_invalid_signature(webhook_settings, api_client):
    raw_body = json.dumps({"event": "charge.success"}).encode("utf-8")

    response = api_client.post(
        "/api/v1/payments/webhook/",
        data=raw_body,
        content_type="application/json",
        HTTP_PAYSTACK_SIGNATURE="t=123,v1=invalid",
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Admin Payment Endpoints
# ---------------------------------------------------------------------------
def test_admin_payment_list_requires_auth(pending_booking, api_client):
    Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-ADMIN-123",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.SUCCESS,
    )

    response = api_client.get("/api/v1/payments/admin/")
    assert response.status_code == 401


def test_admin_payment_list_success(pending_booking, admin_user, api_client):
    api_client.force_authenticate(user=admin_user)

    Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-ADMIN-123",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.SUCCESS,
    )

    response = api_client.get("/api/v1/payments/admin/")
    assert response.status_code == 200
    assert len(response.data["results"]) == 1
    assert response.data["results"][0]["paystack_reference"] == "REF-ADMIN-123"


def test_admin_payment_detail_success(pending_booking, admin_user, api_client):
    api_client.force_authenticate(user=admin_user)

    payment = Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-ADMIN-456",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.SUCCESS,
    )

    response = api_client.get(f"/api/v1/payments/admin/{payment.id}/")
    assert response.status_code == 200
    assert response.data["paystack_reference"] == "REF-ADMIN-456"


def test_admin_payment_filtering(pending_booking, admin_user, api_client):
    api_client.force_authenticate(user=admin_user)

    Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-FILTER-1",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.SUCCESS,
    )
    Payment.objects.create(
        booking=pending_booking,
        paystack_reference="REF-FILTER-2",
        amount_kobo=500_000,
        currency="NGN",
        status=PaymentStatus.FAILED,
    )

    response = api_client.get("/api/v1/payments/admin/?status=success")
    assert response.status_code == 200
    assert len(response.data["results"]) == 1
    assert response.data["results"][0]["paystack_reference"] == "REF-FILTER-1"
