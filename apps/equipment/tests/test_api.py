"""Administrative equipment image endpoints (upload, replace, remove)."""

from unittest import mock

import pytest
from django.test import override_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Role
from apps.accounts.tests.factories import make_user
from apps.common.cloudinary import (
    ImageDeleteFailed,
    ImageUploadFailed,
    UploadedImage,
)
from apps.common.tests.images import image_upload
from apps.equipment.tests.factories import make_equipment, make_equipment_image

pytestmark = pytest.mark.django_db

IMAGE_URL = "/api/v1/equipment/{equipment_id}/image/"

UPLOADED = UploadedImage(
    url="https://res.cloudinary.com/demo/image/upload/light.jpg",
    public_id="mystudio/equipment/light",
)


@pytest.fixture
def manager_client(api_client):
    manager = make_user(email="manager@example.com", role=Role.MANAGER)
    access = RefreshToken.for_user(manager).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api_client


def _upload(api_client, equipment, method="post"):
    return getattr(api_client, method)(
        IMAGE_URL.format(equipment_id=equipment.pk),
        {"image": image_upload()},
        format="multipart",
    )


# ---------------------------------------------------------------------------
# Upload and replace
# ---------------------------------------------------------------------------


@mock.patch("apps.equipment.services.upload_image", return_value=UPLOADED)
def test_manager_can_upload_an_equipment_image(upload_mock, manager_client):
    equipment = make_equipment()

    response = _upload(manager_client, equipment)

    assert response.status_code == 200
    assert response.data["image_url"] == UPLOADED.url
    assert response.data["cloudinary_public_id"] == UPLOADED.public_id
    equipment.refresh_from_db()
    assert equipment.image_url == UPLOADED.url
    assert equipment.cloudinary_public_id == UPLOADED.public_id
    assert upload_mock.call_args.kwargs["folder"] == "mystudio/equipment"
    assert "test-secret" not in str(response.data)


@mock.patch("apps.equipment.services.upload_image", return_value=UPLOADED)
@mock.patch("apps.equipment.services.delete_image")
def test_put_replaces_the_image_and_removes_the_previous_asset(
    delete_mock,
    upload_mock,
    manager_client,
):
    equipment = make_equipment_image()
    previous_public_id = equipment.cloudinary_public_id

    response = _upload(manager_client, equipment, method="put")

    assert response.status_code == 200
    equipment.refresh_from_db()
    assert equipment.cloudinary_public_id == UPLOADED.public_id
    delete_mock.assert_called_once_with(previous_public_id)
    assert upload_mock.called


@mock.patch(
    "apps.equipment.services.upload_image",
    side_effect=ImageUploadFailed("provider exploded with test-secret"),
)
def test_provider_failure_returns_a_generic_502(upload_mock, manager_client):
    equipment = make_equipment()

    response = _upload(manager_client, equipment)

    assert response.status_code == 502
    assert "test-secret" not in str(response.data)
    equipment.refresh_from_db()
    assert equipment.image_url is None
    assert upload_mock.called


def test_anonymous_users_cannot_upload(api_client):
    equipment = make_equipment()

    response = _upload(api_client, equipment)

    assert response.status_code == 401
    equipment.refresh_from_db()
    assert equipment.image_url is None


def test_staff_cannot_upload(auth_client):
    equipment = make_equipment()

    response = _upload(auth_client, equipment)

    assert response.status_code == 403
    equipment.refresh_from_db()
    assert equipment.image_url is None


@override_settings(IMAGE_UPLOAD_MAX_BYTES=20)
def test_oversized_uploads_are_rejected(manager_client):
    equipment = make_equipment()

    response = _upload(manager_client, equipment)

    assert response.status_code == 400
    assert "image" in response.data
    equipment.refresh_from_db()
    assert equipment.image_url is None


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


@mock.patch("apps.equipment.services.delete_image")
def test_manager_can_remove_an_equipment_image(delete_mock, manager_client):
    equipment = make_equipment_image()
    previous_public_id = equipment.cloudinary_public_id

    response = manager_client.delete(
        IMAGE_URL.format(equipment_id=equipment.pk)
    )

    assert response.status_code == 204
    equipment.refresh_from_db()
    assert equipment.image_url is None
    assert equipment.cloudinary_public_id is None
    delete_mock.assert_called_once_with(previous_public_id)


@mock.patch("apps.equipment.services.delete_image")
def test_removing_a_missing_image_is_idempotent(delete_mock, manager_client):
    equipment = make_equipment()

    response = manager_client.delete(
        IMAGE_URL.format(equipment_id=equipment.pk)
    )

    assert response.status_code == 204
    delete_mock.assert_not_called()


@mock.patch(
    "apps.equipment.services.delete_image",
    side_effect=ImageDeleteFailed("provider down"),
)
def test_failed_asset_deletion_reports_502(delete_mock, manager_client):
    equipment = make_equipment_image()

    response = manager_client.delete(
        IMAGE_URL.format(equipment_id=equipment.pk)
    )

    assert response.status_code == 502
    equipment.refresh_from_db()
    assert equipment.image_url is None
    assert delete_mock.called


def test_anonymous_users_cannot_delete(api_client):
    equipment = make_equipment_image()

    response = api_client.delete(
        IMAGE_URL.format(equipment_id=equipment.pk)
    )

    assert response.status_code == 401
    equipment.refresh_from_db()
    assert equipment.image_url is not None
