"""Integration tests for booking API endpoints.

These tests verify the HTTP layer: serializers, views, permissions, and
response shapes. Business logic is tested separately in test_services.py.
"""

import pytest
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.bookings.models import BlockedSlot, Booking, BookingStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        email="api-admin@mystudio.ng",
        password="password123",
        role=Role.OWNER,
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        email="api-staff@mystudio.ng",
        password="password123",
        role=Role.STAFF,
        is_staff=True,
    )


@pytest.fixture
def future_slot():
    """A future 10:00-12:00 window in the project timezone."""
    tomorrow = timezone.localdate() + timedelta(days=1)
    start = timezone.make_aware(
        timezone.datetime.combine(
            tomorrow, timezone.datetime.min.time().replace(hour=10)
        ),
        timezone.get_current_timezone(),
    )
    return start, start + timedelta(hours=2)


# ---------------------------------------------------------------------------
# Public availability endpoint
# ---------------------------------------------------------------------------
def test_public_availability_returns_slots(studio, api_client):
    tomorrow = (timezone.now() + timedelta(days=1)).date()

    response = api_client.get(
        f"/api/v1/bookings/studios/{studio.slug}/availability/?date={tomorrow}"
    )

    assert response.status_code == 200
    assert "slots" in response.data
    available_start_times = {
        slot["start_time"] for slot in response.data["slots"] if slot["available"]
    }
    assert "09:00" in available_start_times
    assert "10:00" in available_start_times


def test_public_availability_excludes_booked_slots(studio, future_slot, api_client):
    start, end = future_slot
    Booking.objects.create(
        studio=studio,
        customer_full_name="Test",
        customer_email="test@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=studio.hourly_rate_kobo,
        studio_subtotal_kobo=studio.hourly_rate_kobo * 2,
        equipment_subtotal_kobo=0,
        total_amount_kobo=studio.hourly_rate_kobo * 2,
        status=BookingStatus.CONFIRMED,
        terms_accepted_at=timezone.now(),
    )

    target_date = start.date()
    response = api_client.get(
        f"/api/v1/bookings/studios/{studio.slug}/availability/?date={target_date}"
    )

    assert response.status_code == 200
    slots_by_start_time = {
        slot["start_time"]: slot["available"] for slot in response.data["slots"]
    }
    assert slots_by_start_time["10:00"] is False
    assert slots_by_start_time["11:00"] is False


def test_public_availability_requires_date_param(studio, api_client):
    response = api_client.get(f"/api/v1/bookings/studios/{studio.slug}/availability/")
    assert response.status_code == 400


def test_public_availability_rejects_invalid_date(studio, api_client):
    response = api_client.get(
        f"/api/v1/bookings/studios/{studio.slug}/availability/?date=invalid"
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Public booking creation
# ---------------------------------------------------------------------------
def test_public_booking_create_success(studio, equipment, future_slot, api_client):
    start, end = future_slot
    payload = {
        "studio_id": str(studio.id),
        "date": start.date().isoformat(),
        "start_time": start.strftime("%H:%M"),
        "duration_hours": int((end - start).total_seconds() // 3600),
        "equipment": [{"equipment_id": str(equipment.id), "quantity": 1}],
        "customer": {
            "full_name": "Ada Obi",
            "email": "ada@example.com",
            "phone": "+2348012345678",
        },
        "terms_accepted": True,
    }

    response = api_client.post("/api/v1/bookings/", payload, format="json")

    assert response.status_code == 201
    assert response.data["status"] == BookingStatus.PENDING_PAYMENT
    assert "expires_at" in response.data
    assert response.data["total_amount_kobo"] > 0


def test_public_booking_create_without_equipment(studio, future_slot, api_client):
    start, end = future_slot
    payload = {
        "studio_id": str(studio.id),
        "date": start.date().isoformat(),
        "start_time": start.strftime("%H:%M"),
        "duration_hours": int((end - start).total_seconds() // 3600),
        "equipment": [],
        "customer": {
            "full_name": "Ada Obi",
            "email": "ada@example.com",
            "phone": "+2348012345678",
        },
        "terms_accepted": True,
    }

    response = api_client.post("/api/v1/bookings/", payload, format="json")

    assert response.status_code == 201
    assert response.data["equipment_amount_kobo"] == 0


def test_public_booking_create_rejects_invalid_studio(future_slot, api_client):
    start, end = future_slot
    payload = {
        "studio_id": "00000000-0000-0000-0000-000000000000",
        "date": start.date().isoformat(),
        "start_time": start.strftime("%H:%M"),
        "duration_hours": int((end - start).total_seconds() // 3600),
        "equipment": [],
        "customer": {
            "full_name": "Test",
            "email": "test@example.com",
            "phone": "123",
        },
        "terms_accepted": True,
    }

    response = api_client.post("/api/v1/bookings/", payload, format="json")
    assert response.status_code == 400


def test_public_booking_create_rejects_duplicate_equipment(
    studio, equipment, future_slot, api_client
):
    start, end = future_slot
    payload = {
        "studio_id": str(studio.id),
        "date": start.date().isoformat(),
        "start_time": start.strftime("%H:%M"),
        "duration_hours": int((end - start).total_seconds() // 3600),
        "equipment": [
            {"equipment_id": str(equipment.id), "quantity": 1},
            {"equipment_id": str(equipment.id), "quantity": 2},
        ],
        "customer": {
            "full_name": "Test",
            "email": "test@example.com",
            "phone": "123",
        },
        "terms_accepted": True,
    }

    response = api_client.post("/api/v1/bookings/", payload, format="json")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Admin booking list and detail
# ---------------------------------------------------------------------------
def test_admin_booking_list_requires_auth(studio, api_client):
    response = api_client.get("/api/v1/bookings/admin/")
    assert response.status_code == 401


def test_admin_booking_list_requires_staff_role(studio, api_client):
    user = User.objects.create_user(
        email="customer@example.com", password="pass", role=Role.STAFF, is_staff=False
    )
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/bookings/admin/")
    assert response.status_code == 403


def test_admin_booking_list_success(studio, future_slot, admin_user, api_client):
    start, end = future_slot
    Booking.objects.create(
        studio=studio,
        customer_full_name="Test",
        customer_email="test@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=studio.hourly_rate_kobo,
        studio_subtotal_kobo=studio.hourly_rate_kobo * 2,
        equipment_subtotal_kobo=0,
        total_amount_kobo=studio.hourly_rate_kobo * 2,
        status=BookingStatus.PENDING_PAYMENT,
        payment_hold_expires_at=timezone.now() + timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )

    api_client.force_authenticate(user=admin_user)
    response = api_client.get("/api/v1/bookings/admin/")

    assert response.status_code == 200
    assert len(response.data["results"]) == 1


def test_admin_booking_detail_success(studio, future_slot, admin_user, api_client):
    start, end = future_slot
    booking = Booking.objects.create(
        studio=studio,
        customer_full_name="Test",
        customer_email="test@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=studio.hourly_rate_kobo,
        studio_subtotal_kobo=studio.hourly_rate_kobo * 2,
        equipment_subtotal_kobo=0,
        total_amount_kobo=studio.hourly_rate_kobo * 2,
        status=BookingStatus.PENDING_PAYMENT,
        payment_hold_expires_at=timezone.now() + timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )

    api_client.force_authenticate(user=admin_user)
    response = api_client.get(f"/api/v1/bookings/admin/{booking.id}/")

    assert response.status_code == 200
    assert response.data["id"] == str(booking.id)


# ---------------------------------------------------------------------------
# Admin booking cancellation
# ---------------------------------------------------------------------------
def test_admin_cancel_booking_requires_owner_or_manager(
    studio, future_slot, staff_user, api_client
):
    start, end = future_slot
    booking = Booking.objects.create(
        studio=studio,
        customer_full_name="Test",
        customer_email="test@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=studio.hourly_rate_kobo,
        studio_subtotal_kobo=studio.hourly_rate_kobo * 2,
        equipment_subtotal_kobo=0,
        total_amount_kobo=studio.hourly_rate_kobo * 2,
        status=BookingStatus.PENDING_PAYMENT,
        payment_hold_expires_at=timezone.now() + timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )

    api_client.force_authenticate(user=staff_user)
    response = api_client.post(
        f"/api/v1/bookings/admin/{booking.id}/cancel/", {"reason": "Test"}
    )
    assert response.status_code == 403


def test_admin_cancel_booking_success(studio, future_slot, admin_user, api_client):
    start, end = future_slot
    booking = Booking.objects.create(
        studio=studio,
        customer_full_name="Test",
        customer_email="test@example.com",
        customer_phone="123",
        start_time=start,
        end_time=end,
        duration_hours=2,
        studio_hourly_price_kobo=studio.hourly_rate_kobo,
        studio_subtotal_kobo=studio.hourly_rate_kobo * 2,
        equipment_subtotal_kobo=0,
        total_amount_kobo=studio.hourly_rate_kobo * 2,
        status=BookingStatus.PENDING_PAYMENT,
        payment_hold_expires_at=timezone.now() + timedelta(minutes=10),
        terms_accepted_at=timezone.now(),
    )

    api_client.force_authenticate(user=admin_user)
    response = api_client.post(
        f"/api/v1/bookings/admin/{booking.id}/cancel/", {"reason": "Test cancellation"}
    )

    assert response.status_code == 200
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED


# ---------------------------------------------------------------------------
# Admin blocked slots
# ---------------------------------------------------------------------------
def test_admin_create_blocked_slot_success(studio, future_slot, admin_user, api_client):
    start, end = future_slot
    api_client.force_authenticate(user=admin_user)

    payload = {
        "studio_id": str(studio.id),
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "reason": "Annual maintenance",
    }

    response = api_client.post(
        "/api/v1/bookings/admin/blocked-slots/",
        payload,
        format="json",
    )

    assert response.status_code == 201
    assert studio.blocked_slots.count() == 1


def test_admin_list_blocked_slots(studio, future_slot, admin_user, api_client):
    start, end = future_slot
    BlockedSlot.objects.create(
        studio=studio,
        start_time=start,
        end_time=end,
        reason="Test block",
        created_by=admin_user,
    )

    api_client.force_authenticate(user=admin_user)
    response = api_client.get("/api/v1/bookings/admin/blocked-slots/")

    assert response.status_code == 200
    assert len(response.data["results"]) == 1


def test_admin_delete_blocked_slot(studio, future_slot, admin_user, api_client):
    start, end = future_slot
    block = BlockedSlot.objects.create(
        studio=studio,
        start_time=start,
        end_time=end,
        reason="Test block",
        created_by=admin_user,
    )

    api_client.force_authenticate(user=admin_user)
    response = api_client.delete(
        f"/api/v1/bookings/admin/blocked-slots/{block.id}/"
    )

    assert response.status_code == 204
    assert studio.blocked_slots.count() == 0
