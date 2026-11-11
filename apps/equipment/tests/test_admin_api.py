"""Administrative equipment CRUD, image handling and role permissions."""

from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Role
from apps.accounts.tests.factories import make_admin, make_user
from apps.common.cloudinary import ImageDeleteFailed, ImageUploadFailed, UploadedImage
from apps.common.tests.images import image_upload
from apps.equipment.models import Equipment
from apps.equipment.tests.factories import make_equipment, make_equipment_image

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/admin/equipment/"
DETAIL_URL = "/api/v1/admin/equipment/{equipment_id}/"

UPLOADED = UploadedImage(
    url="https://res.cloudinary.com/demo/image/upload/admin-light.jpg",
    public_id="mystudio/equipment/admin-light",
)

VALID_PAYLOAD = {
    "name": "Admin LED light",
    "description": "A light created through the administrative API.",
    "fixed_price_kobo": 7_500_00,
    "stock_quantity": 3,
}


def _authenticate(api_client, user):
    access = RefreshToken.for_user(user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api_client


@pytest.fixture
def manager_client(api_client):
    manager = make_user(email="manager@example.com", role=Role.MANAGER)
    return _authenticate(api_client, manager)


@pytest.fixture
def owner_client(api_client):
    return _authenticate(api_client, make_admin(email="owner@example.com"))


@pytest.fixture
def staff_client(api_client):
    staff = make_user(email="staff@example.com", role=Role.STAFF)
    return _authenticate(api_client, staff)


# ---------------------------------------------------------------------------
# Authentication and permissions
# ---------------------------------------------------------------------------


def test_anonymous_users_cannot_list(api_client):
    make_equipment()

    assert api_client.get(LIST_URL).status_code == 401


def test_anonymous_users_cannot_create(api_client):
    response = api_client.post(LIST_URL, VALID_PAYLOAD, format="json")

    assert response.status_code == 401
    assert Equipment.objects.count() == 0


def test_staff_can_read_administrative_equipment(staff_client):
    equipment = make_equipment(is_active=False)

    listed = staff_client.get(LIST_URL)
    detail = staff_client.get(
        DETAIL_URL.format(equipment_id=equipment.pk)
    )

    assert listed.status_code == 200
    assert listed.data["count"] == 1
    assert detail.status_code == 200
    assert detail.data["id"] == str(equipment.pk)


def test_staff_cannot_create_update_or_deactivate(staff_client):
    equipment = make_equipment()

    created = staff_client.post(LIST_URL, VALID_PAYLOAD, format="json")
    updated = staff_client.patch(
        DETAIL_URL.format(equipment_id=equipment.pk),
        {"stock_quantity": 20},
        format="json",
    )
    deleted = staff_client.delete(
        DETAIL_URL.format(equipment_id=equipment.pk)
    )

    assert created.status_code == 403
    assert updated.status_code == 403
    assert deleted.status_code == 403
    equipment.refresh_from_db()
    assert equipment.is_active is True
    assert equipment.stock_quantity == 4


def test_managers_can_create_update_and_deactivate(manager_client):
    created = manager_client.post(LIST_URL, VALID_PAYLOAD, format="json")
    assert created.status_code == 201

    equipment_id = created.data["id"]
    updated = manager_client.patch(
        DETAIL_URL.format(equipment_id=equipment_id),
        {"stock_quantity": 9},
        format="json",
    )
    deleted = manager_client.delete(
        DETAIL_URL.format(equipment_id=equipment_id)
    )

    assert updated.status_code == 200
    assert updated.data["stock_quantity"] == 9
    assert deleted.status_code == 204

    equipment = Equipment.objects.get(pk=equipment_id)
    assert equipment.is_active is False


def test_owners_have_full_access(owner_client):
    created = owner_client.post(LIST_URL, VALID_PAYLOAD, format="json")
    assert created.status_code == 201

    equipment_id = created.data["id"]
    updated = owner_client.patch(
        DETAIL_URL.format(equipment_id=equipment_id),
        {"stock_quantity": 8},
        format="json",
    )
    deleted = owner_client.delete(
        DETAIL_URL.format(equipment_id=equipment_id)
    )

    assert updated.status_code == 200
    assert updated.data["stock_quantity"] == 8
    assert deleted.status_code == 204
    assert Equipment.objects.get(pk=equipment_id).is_active is False


# ---------------------------------------------------------------------------
# Create and validation
# ---------------------------------------------------------------------------


def test_create_returns_the_administrative_representation(manager_client):
    response = manager_client.post(LIST_URL, VALID_PAYLOAD, format="json")

    assert response.status_code == 201
    assert response.data["id"]
    assert response.data["name"] == "Admin LED light"
    assert response.data["fixed_price_kobo"] == 7_500_00
    assert response.data["stock_quantity"] == 3
    assert response.data["image_url"] is None
    assert response.data["is_active"] is True
    assert "cloudinary_public_id" not in response.data
    assert response.data["created_at"]
    assert response.data["updated_at"]


def test_create_accepts_zero_stock(manager_client):
    payload = {**VALID_PAYLOAD, "stock_quantity": 0}

    response = manager_client.post(LIST_URL, payload, format="json")

    assert response.status_code == 201
    assert response.data["stock_quantity"] == 0


@pytest.mark.parametrize("value", [0, -1])
def test_create_rejects_a_non_positive_price(manager_client, value):
    payload = {**VALID_PAYLOAD, "fixed_price_kobo": value}

    response = manager_client.post(LIST_URL, payload, format="json")

    assert response.status_code == 400
    assert "fixed_price_kobo" in response.data


def test_create_rejects_negative_stock(manager_client):
    payload = {**VALID_PAYLOAD, "stock_quantity": -1}

    response = manager_client.post(LIST_URL, payload, format="json")

    assert response.status_code == 400
    assert "stock_quantity" in response.data


def test_create_requires_a_name(manager_client):
    payload = {**VALID_PAYLOAD}
    payload.pop("name")

    response = manager_client.post(LIST_URL, payload, format="json")

    assert response.status_code == 400
    assert "name" in response.data


@mock.patch("apps.equipment.services.upload_image", return_value=UPLOADED)
def test_create_with_a_multipart_image(upload_mock, manager_client):
    payload = {**VALID_PAYLOAD, "image": image_upload()}

    response = manager_client.post(LIST_URL, payload, format="multipart")

    assert response.status_code == 201
    assert response.data["image_url"] == UPLOADED.url
    assert "cloudinary_public_id" not in response.data
    assert upload_mock.call_args.kwargs["folder"] == "mystudio/equipment"

    equipment = Equipment.objects.get(pk=response.data["id"])
    assert equipment.image_url == UPLOADED.url
    assert equipment.cloudinary_public_id == UPLOADED.public_id


@mock.patch(
    "apps.equipment.services.upload_image",
    side_effect=ImageUploadFailed("provider down"),
)
def test_create_image_failure_returns_502(upload_mock, manager_client):
    payload = {**VALID_PAYLOAD, "image": image_upload()}

    response = manager_client.post(LIST_URL, payload, format="multipart")

    assert response.status_code == 502
    assert upload_mock.called
    assert Equipment.objects.count() == 0


def test_create_rejects_an_invalid_image(manager_client):
    payload = {
        **VALID_PAYLOAD,
        "image": SimpleUploadedFile(
            "photo.jpg",
            b"not an image",
            content_type="image/jpeg",
        ),
    }

    response = manager_client.post(LIST_URL, payload, format="multipart")

    assert response.status_code == 400
    assert "image" in response.data
    assert Equipment.objects.count() == 0


# ---------------------------------------------------------------------------
# List, filtering, search and ordering
# ---------------------------------------------------------------------------


def test_list_includes_inactive_equipment(manager_client):
    make_equipment(name="Available")
    make_equipment(name="Retired", is_active=False)

    response = manager_client.get(LIST_URL)

    assert response.status_code == 200
    assert response.data["count"] == 2
    assert {item["name"] for item in response.data["results"]} == {
        "Available",
        "Retired",
    }


def test_list_filters_by_active_state(manager_client):
    make_equipment(name="Available")
    make_equipment(name="Retired", is_active=False)

    inactive = manager_client.get(LIST_URL, {"is_active": "false"})

    assert inactive.data["count"] == 1
    assert inactive.data["results"][0]["name"] == "Retired"


def test_list_filters_by_price(manager_client):
    make_equipment(name="Cheap", fixed_price_kobo=1_000_00)
    make_equipment(name="Pricey", fixed_price_kobo=9_000_00)

    response = manager_client.get(LIST_URL, {"fixed_price_min": 5_000_00})

    assert [item["name"] for item in response.data["results"]] == ["Pricey"]


def test_list_searches_and_orders(manager_client):
    make_equipment(name="Zoom lens", description="Telephoto zoom.")
    make_equipment(name="Apple box", description="Wooden riser.")
    make_equipment(name="Monopod", description="Carbon fibre.")

    searched = manager_client.get(LIST_URL, {"search": "telephoto"})
    ordered = manager_client.get(LIST_URL, {"ordering": "name"})

    assert [item["name"] for item in searched.data["results"]] == ["Zoom lens"]
    assert [item["name"] for item in ordered.data["results"]] == [
        "Apple box",
        "Monopod",
        "Zoom lens",
    ]


def test_list_is_paginated(manager_client):
    for index in range(3):
        make_equipment(name=f"Item {index}")

    page = manager_client.get(LIST_URL, {"page_size": 2, "page": 1})

    assert page.data["count"] == 3
    assert len(page.data["results"]) == 2
    assert page.data["next"] is not None


# ---------------------------------------------------------------------------
# Detail, update and deactivation
# ---------------------------------------------------------------------------


def test_unknown_equipment_returns_404(manager_client):
    response = manager_client.get(
        DETAIL_URL.format(
            equipment_id="00000000-0000-0000-0000-000000000000"
        )
    )

    assert response.status_code == 404


def test_update_allows_partial_changes(manager_client):
    equipment = make_equipment()

    response = manager_client.patch(
        DETAIL_URL.format(equipment_id=equipment.pk),
        {"description": "Updated copy.", "is_active": False},
        format="json",
    )

    assert response.status_code == 200
    equipment.refresh_from_db()
    assert equipment.description == "Updated copy."
    assert equipment.is_active is False
    assert equipment.name == "Godox SL-60W light"


def test_update_rejects_invalid_numbers(manager_client):
    equipment = make_equipment()

    response = manager_client.patch(
        DETAIL_URL.format(equipment_id=equipment.pk),
        {"fixed_price_kobo": -5, "stock_quantity": -2},
        format="json",
    )

    assert response.status_code == 400
    assert "fixed_price_kobo" in response.data
    assert "stock_quantity" in response.data


@mock.patch("apps.equipment.services.upload_image", return_value=UPLOADED)
@mock.patch("apps.equipment.services.delete_image")
def test_update_can_replace_the_image(
    delete_mock,
    upload_mock,
    manager_client,
):
    equipment = make_equipment_image()
    previous_public_id = equipment.cloudinary_public_id

    response = manager_client.patch(
        DETAIL_URL.format(equipment_id=equipment.pk),
        {"image": image_upload(), "description": "New image."},
        format="multipart",
    )

    assert response.status_code == 200
    equipment.refresh_from_db()
    assert equipment.image_url == UPLOADED.url
    assert equipment.cloudinary_public_id == UPLOADED.public_id
    assert equipment.description == "New image."
    delete_mock.assert_called_once_with(previous_public_id)
    assert upload_mock.called


@mock.patch("apps.equipment.services.delete_image")
def test_update_can_remove_the_image(delete_mock, manager_client):
    equipment = make_equipment_image()
    previous_public_id = equipment.cloudinary_public_id

    response = manager_client.patch(
        DETAIL_URL.format(equipment_id=equipment.pk),
        {"remove_image": True},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["image_url"] is None

    equipment.refresh_from_db()
    assert equipment.image_url is None
    assert equipment.cloudinary_public_id is None
    delete_mock.assert_called_once_with(previous_public_id)


def test_update_rejects_upload_and_remove_together(manager_client):
    equipment = make_equipment_image()

    response = manager_client.patch(
        DETAIL_URL.format(equipment_id=equipment.pk),
        {"image": image_upload(), "remove_image": True},
        format="multipart",
    )

    assert response.status_code == 400
    assert "remove_image" in response.data


def test_update_image_provider_failure_returns_502(manager_client):
    equipment = make_equipment()

    with mock.patch(
        "apps.equipment.services.upload_image",
        side_effect=ImageUploadFailed("provider down"),
    ):
        response = manager_client.patch(
            DETAIL_URL.format(equipment_id=equipment.pk),
            {"image": image_upload(), "description": "Should not persist."},
            format="multipart",
        )

    assert response.status_code == 502
    equipment.refresh_from_db()
    assert equipment.description == "Continuous LED light with a Bowens mount."
    assert equipment.image_url is None


def test_delete_deactivates_and_keeps_the_row(manager_client):
    equipment = make_equipment()

    response = manager_client.delete(
        DETAIL_URL.format(equipment_id=equipment.pk)
    )

    assert response.status_code == 204
    equipment.refresh_from_db()
    assert equipment.is_active is False
    assert Equipment.objects.filter(pk=equipment.pk).exists()


def test_deleted_equipment_disappears_from_the_public_catalogue(
    manager_client,
    api_client,
):
    equipment = make_equipment()

    manager_client.delete(DETAIL_URL.format(equipment_id=equipment.pk))

    assert api_client.get("/api/v1/equipment/").data["count"] == 0


def test_deleting_an_inactive_item_is_safe(manager_client):
    equipment = make_equipment(is_active=False)

    response = manager_client.delete(
        DETAIL_URL.format(equipment_id=equipment.pk)
    )

    assert response.status_code == 204
    assert Equipment.objects.filter(pk=equipment.pk).exists()


@mock.patch(
    "apps.equipment.services.delete_image",
    side_effect=ImageDeleteFailed("provider down"),
)
def test_removing_an_image_with_provider_failure_reports_502(
    delete_mock,
    manager_client,
):
    equipment = make_equipment_image()

    response = manager_client.patch(
        DETAIL_URL.format(equipment_id=equipment.pk),
        {"remove_image": True},
        format="json",
    )

    assert response.status_code == 502
    equipment.refresh_from_db()
    assert equipment.image_url is None
    assert delete_mock.called
