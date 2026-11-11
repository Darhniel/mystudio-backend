"""API endpoint tests for bookings."""

import pytest
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.bookings.models import BlockedSlot, Booking, BookingStatus
from apps.bookings.utils import make_aware_datetime
from apps.equipment.models import Equipment
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
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


@pytest.fixture
def studio(db):
    return Studio.objects.create(
        name="API Test Studio",
        slug="api-test-studio",
        hourly_rate_kobo=250_000,  # ₦2,500
        capacity=10,
    )


@pytest.fixture
def equipment(db):
    return Equipment.objects.create(
        name="LED Light",
        fixed_price_kobo=50_000,  # ₦500
        stock_quantity=10,
    )


# ---------------------------------------------------------------------------
# Public Studio Availability
# ---------------------------------------------------------------------------
def test_public_availability_returns_slots(studio, tomorrow, api_client):
    response = api_client.get(
        f"/api/v1/bookings/studios/{studio.slug}/availability/",
        {"date": tomorrow.isoformat(), "duration_hours": 2},
    )

    assert response.status_code == 200
    assert "slots" in response.data
    assert (
        len(response.data["slots"]) == 8
    )  # 09:00 to 16:00 start times for 2hr duration
    assert response.data["slots"][0]["start_time"] == "09:00"
    assert response.data["slots"][0]["end_time"] == "11:00"
    assert response.data["slots"][0]["available"] is True


def test_public_availability_marks_booked_slots_unavailable(
    studio, tomorrow, api_client
):
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    Booking.objects.create(
        studio=studio,
        customer_full_name="Test",
        customer_email="test@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=250_000,
        studio_subtotal_kobo=500_000,
        equipment_subtotal_kobo=0,
        total_amount_kobo=500_000,
        status=BookingStatus.CONFIRMED,
        terms_accepted_at=timezone.now(),
        access_token="dummy",
    )

    response = api_client.get(
        f"/api/v1/bookings/studios/{studio.slug}/availability/",
        {"date": tomorrow.isoformat(), "duration_hours": 2},
    )

    assert response.status_code == 200
    slot_10 = next(s for s in response.data["slots"] if s["start_time"] == "10:00")
    assert slot_10["available"] is False


# ---------------------------------------------------------------------------
# Public Booking Creation
# ---------------------------------------------------------------------------
def test_public_booking_create_success(studio, equipment, tomorrow, api_client):
    payload = {
        "studio_id": str(studio.id),
        "date": tomorrow.isoformat(),
        "start_time": "10:00",
        "duration_hours": 3,
        "customer": {
            "full_name": "John Doe",
            "email": "john@example.com",
            "phone": "08012345678",
        },
        "equipment": [{"equipment_id": str(equipment.id), "quantity": 2}],
        "terms_accepted": True,
    }

    response = api_client.post("/api/v1/bookings/", payload, format="json")

    assert response.status_code == 201
    assert response.data["booking_reference"].startswith("MS-")
    assert response.data["status"] == "pending_payment"
    assert response.data["duration_hours"] == 3
    assert response.data["studio_amount_kobo"] == 750_000  # 3 * 250,000
    assert response.data["equipment_amount_kobo"] == 100_000  # 2 * 50,000
    assert response.data["total_amount_kobo"] == 850_000
    assert response.data["currency"] == "NGN"
    assert response.data["payment_required"] is True
    assert "expires_at" in response.data


def test_public_booking_create_validation_errors(studio, tomorrow, api_client):
    # Terms not accepted
    payload = {
        "studio_id": str(studio.id),
        "date": tomorrow.isoformat(),
        "start_time": "10:00",
        "duration_hours": 3,
        "customer": {"full_name": "John", "email": "john@example.com", "phone": "123"},
        "equipment": [],
        "terms_accepted": False,
    }
    response = api_client.post("/api/v1/bookings/", payload, format="json")
    assert response.status_code == 400
    assert "terms_accepted" in response.data

    # Invalid start time
    payload["terms_accepted"] = True
    payload["start_time"] = "10:30"
    response = api_client.post("/api/v1/bookings/", payload, format="json")
    assert response.status_code == 400
    assert "start_time" in response.data

    # Invalid studio
    payload["start_time"] = "10:00"
    payload["studio_id"] = "00000000-0000-0000-0000-000000000000"
    response = api_client.post("/api/v1/bookings/", payload, format="json")
    assert response.status_code == 400
    assert "studio_id" in response.data


# ---------------------------------------------------------------------------
# Public Booking Retrieval
# ---------------------------------------------------------------------------
def test_public_booking_retrieve_success(studio, tomorrow, api_client):
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = Booking.objects.create(
        studio=studio,
        customer_full_name="Test",
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
        payment_hold_expires_at=timezone.now() + timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )

    response = api_client.get(
        f"/api/v1/bookings/{booking.reference}/", {"token": booking.access_token}
    )

    assert response.status_code == 200
    assert response.data["booking_reference"] == booking.reference


def test_public_booking_retrieve_invalid_token(studio, tomorrow, api_client):
    start = make_aware_datetime(tomorrow, 10)
    end = make_aware_datetime(tomorrow, 12)

    booking = Booking.objects.create(
        studio=studio,
        customer_full_name="Test",
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
        payment_hold_expires_at=timezone.now() + timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )

    response = api_client.get(
        f"/api/v1/bookings/{booking.reference}/", {"token": "invalid-token"}
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Admin Blocked Slots
# ---------------------------------------------------------------------------
def test_admin_blocked_slot_create_success(studio, tomorrow, admin_user, api_client):
    api_client.force_authenticate(user=admin_user)

    payload = {
        "studio_id": str(studio.id),
        "start_time": make_aware_datetime(tomorrow, 14).isoformat(),
        "end_time": make_aware_datetime(tomorrow, 16).isoformat(),
        "reason": "Maintenance",
    }

    response = api_client.post(
        "/api/v1/bookings/admin/blocked-slots/", payload, format="json"
    )

    assert response.status_code == 201
    assert response.data["studio_name"] == studio.name
    assert response.data["reason"] == "Maintenance"


def test_admin_blocked_slot_delete_success(studio, tomorrow, admin_user, api_client):
    api_client.force_authenticate(user=admin_user)

    block = BlockedSlot.objects.create(
        studio=studio,
        start_time=make_aware_datetime(tomorrow, 14),
        end_time=make_aware_datetime(tomorrow, 16),
        reason="Maintenance",
        created_by=admin_user,
    )

    response = api_client.delete(f"/api/v1/bookings/admin/blocked-slots/{block.id}/")

    assert response.status_code == 204
    assert not BlockedSlot.objects.filter(id=block.id).exists()


def test_admin_blocked_slot_requires_auth(studio, tomorrow, api_client):
    payload = {
        "studio_id": str(studio.id),
        "start_time": make_aware_datetime(tomorrow, 14).isoformat(),
        "end_time": make_aware_datetime(tomorrow, 16).isoformat(),
        "reason": "Maintenance",
    }

    response = api_client.post(
        "/api/v1/bookings/admin/blocked-slots/", payload, format="json"
    )
    assert response.status_code == 401
