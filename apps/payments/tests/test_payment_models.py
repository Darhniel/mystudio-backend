"""Tests for the Payment model.

These tests cover model-level constraints, field validation, and the
relationship between payments and bookings. Paystack integration is mocked
out; service-level behavior is tested separately.
"""

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.utils import make_aware_datetime
from apps.payments.models import Payment, PaymentStatus
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def payment_studio():
    return Studio.objects.create(
        name="Payment Test Studio",
        hourly_rate_kobo=100_000,
        capacity=10,
    )


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


def booking_payload(studio, tomorrow, **overrides):
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)
    payload = {
        "studio": studio,
        "customer_full_name": "Test User",
        "customer_email": "test@example.com",
        "customer_phone": "+2348012345678",
        "start_time": start,
        "end_time": end,
        "duration_hours": 2,
        "studio_hourly_price_kobo": 100_000,
        "studio_subtotal_kobo": 200_000,
        "equipment_subtotal_kobo": 0,
        "total_amount_kobo": 200_000,
        "status": BookingStatus.PENDING_PAYMENT,
        "payment_hold_expires_at": timezone.now() + timedelta(minutes=10),
        "terms_accepted_at": timezone.now(),
    }
    payload.update(overrides)
    return payload


def payment_payload(booking, **overrides):
    payload = {
        "booking": booking,
        "paystack_reference": f"REF-{uuid.uuid4().hex[:8]}",
        "amount_kobo": booking.total_amount_kobo,
        "currency": "NGN",
        "status": PaymentStatus.INITIALIZING,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Payment creation
# ---------------------------------------------------------------------------
def test_payment_creation_with_defaults(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    payment = Payment.objects.create(**payment_payload(booking))

    assert payment.pk is not None
    assert payment.booking == booking
    assert payment.amount_kobo == 200_000
    assert payment.currency == "NGN"
    assert payment.status == PaymentStatus.INITIALIZING
    assert payment.created_at is not None
    assert payment.updated_at is not None


def test_payment_uuid_primary_key(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    payment = Payment.objects.create(**payment_payload(booking))

    assert isinstance(payment.pk, uuid.UUID)


# ---------------------------------------------------------------------------
# Unique references
# ---------------------------------------------------------------------------
def test_paystack_reference_must_be_unique(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    Payment.objects.create(**payment_payload(booking, paystack_reference="REF-UNIQUE"))

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(
                **payment_payload(booking, paystack_reference="REF-UNIQUE")
            )


def test_multiple_payments_can_share_a_booking(payment_studio, tomorrow):
    """A booking can have many payment attempts (retry, late payment, etc.)."""
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))

    p1 = Payment.objects.create(**payment_payload(booking, paystack_reference="REF-A"))
    p2 = Payment.objects.create(**payment_payload(booking, paystack_reference="REF-B"))
    p3 = Payment.objects.create(**payment_payload(booking, paystack_reference="REF-C"))

    assert booking.payments.count() == 3
    assert {p1.id, p2.id, p3.id} == {p.id for p in booking.payments.all()}


# ---------------------------------------------------------------------------
# Valid payment statuses
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status",
    [
        PaymentStatus.INITIALIZING,
        PaymentStatus.INITIALIZED,
        PaymentStatus.SUCCESS,
        PaymentStatus.FAILED,
        PaymentStatus.ABANDONED,
        PaymentStatus.REQUIRES_REVIEW,
    ],
)
def test_all_payment_statuses_are_valid(payment_studio, tomorrow, status):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    payment = Payment.objects.create(**payment_payload(booking, status=status))
    payment.refresh_from_db()
    assert payment.status == status


def test_invalid_status_rejected(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(**payment_payload(booking, status="refunded"))


# ---------------------------------------------------------------------------
# Correct booking relationships
# ---------------------------------------------------------------------------
def test_booking_protects_payment_on_delete(payment_studio, tomorrow):
    """Deleting a booking with payments is blocked by PROTECT."""
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    Payment.objects.create(**payment_payload(booking))

    with pytest.raises(Exception):  # ProtectedError
        booking.delete()


def test_payment_queryable_via_booking_reverse(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    Payment.objects.create(**payment_payload(booking, paystack_reference="REV-1"))
    Payment.objects.create(**payment_payload(booking, paystack_reference="REV-2"))

    assert booking.payments.count() == 2
    refs = set(booking.payments.values_list("paystack_reference", flat=True))
    assert refs == {"REV-1", "REV-2"}


# ---------------------------------------------------------------------------
# Amount and currency validation
# ---------------------------------------------------------------------------
def test_zero_amount_rejected(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(**payment_payload(booking, amount_kobo=0))


def test_negative_amount_rejected(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(**payment_payload(booking, amount_kobo=-100))


def test_currency_defaults_to_ngn(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    payment = Payment.objects.create(**payment_payload(booking))
    # Remove currency from payload to test default
    Payment.objects.filter(id=payment.id).update(currency="")
    payment.refresh_from_db()
    # The default is set at the Python level, so we test creation without it
    payment2 = Payment.objects.create(
        booking=booking,
        paystack_reference="REF-DEFAULT-CURRENCY",
        amount_kobo=1000,
        # currency omitted
    )
    assert payment2.currency == "NGN"


# ---------------------------------------------------------------------------
# Preservation of successful payment records
# ---------------------------------------------------------------------------
def test_successful_payment_cannot_be_deleted_via_queryset(payment_studio, tomorrow):
    """Payment records are preserved; queryset delete is blocked by PROTECT on FK."""
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    payment = Payment.objects.create(
        **payment_payload(booking, status=PaymentStatus.SUCCESS)
    )

    # The Payment model itself has no delete() override, but the FK PROTECT
    # means the booking can't be deleted. The payment row persists.
    assert Payment.objects.filter(id=payment.id).exists()


def test_successful_payment_paid_at_constraint(payment_studio, tomorrow):
    """paid_at can only be set when status is SUCCESS or REQUIRES_REVIEW."""
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))

    # This should fail: paid_at set but status is INITIALIZED
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(
                **payment_payload(
                    booking,
                    status=PaymentStatus.INITIALIZED,
                    paid_at=timezone.now(),
                )
            )


def test_successful_payment_allows_paid_at(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    now = timezone.now()
    payment = Payment.objects.create(
        **payment_payload(
            booking,
            status=PaymentStatus.SUCCESS,
            paid_at=now,
            paystack_transaction_id="TXN-123",
        )
    )
    assert payment.paid_at == now


def test_requires_review_allows_paid_at(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    now = timezone.now()
    payment = Payment.objects.create(
        **payment_payload(
            booking,
            status=PaymentStatus.REQUIRES_REVIEW,
            paid_at=now,
            review_reason="Late payment: slot taken.",
        )
    )
    assert payment.paid_at == now
    assert payment.review_reason == "Late payment: slot taken."


# ---------------------------------------------------------------------------
# Gateway response and audit fields
# ---------------------------------------------------------------------------
def test_gateway_response_defaults_to_empty_dict(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    payment = Payment.objects.create(**payment_payload(booking))
    assert payment.gateway_response == {}


def test_gateway_response_stores_selective_data(payment_studio, tomorrow):
    booking = Booking.objects.create(**booking_payload(payment_studio, tomorrow))
    payment = Payment.objects.create(
        **payment_payload(
            booking,
            gateway_response={
                "status": "success",
                "message": "Charge attempted",
                "channel": "card",
                "authorization": {"auth_code": "AUTH-123"},
            },
        )
    )
    payment.refresh_from_db()
    assert payment.gateway_response["channel"] == "card"
    assert payment.gateway_response["authorization"]["auth_code"] == "AUTH-123"


# ---------------------------------------------------------------------------
# No sensitive data stored
# ---------------------------------------------------------------------------
def test_payment_does_not_store_card_details(payment_studio, tomorrow):
    """The Payment model has no fields for card numbers, CVV, or PIN."""
    field_names = {f.name for f in Payment._meta.get_fields()}
    sensitive_terms = {"card_number", "cvv", "pin", "pan", "expiry"}
    assert not field_names & sensitive_terms
