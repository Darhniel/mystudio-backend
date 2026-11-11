"""Administrative studio image endpoints (multipart upload, delete, reorder)."""

from unittest import mock

import pytest
from django.test import override_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Role
from apps.accounts.tests.factories import make_user
from apps.common.cloudinary import (
    ImageDeleteFailed,
    ImageHostingNotConfigured,
    ImageUploadFailed,
    UploadedImage,
)
from apps.common.tests.images import image_upload
from apps.studios.models import StudioImage
from apps.studios.tests.factories import make_studio, make_studio_image

pytestmark = pytest.mark.django_db

UPLOAD_URL = "/api/v1/studios/{slug}/images/"
DETAIL_URL = "/api/v1/studios/{slug}/images/{image_id}/"

UPLOADED = UploadedImage(
    url="https://res.cloudinary.com/demo/image/upload/studio.jpg",
    public_id="mystudio/studios/studio",
)


@pytest.fixture
def manager_client(api_client):
    manager = make_user(email="manager@example.com", role=Role.MANAGER)
    access = RefreshToken.for_user(manager).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api_client


def _upload(api_client, studio, **data):
    payload = {"image": image_upload()}
    payload.update(data)
    return api_client.post(
        UPLOAD_URL.format(slug=studio.slug),
        payload,
        format="multipart",
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@mock.patch("apps.studios.services.upload_image", return_value=UPLOADED)
def test_manager_can_upload_a_studio_image(upload_mock, manager_client):
    studio = make_studio()

    response = _upload(
        manager_client,
        studio,
        alt_text="White cyclorama",
        display_order=1,
    )

    assert response.status_code == 201
    assert response.data["image_url"] == UPLOADED.url
    assert response.data["cloudinary_public_id"] == UPLOADED.public_id
    assert response.data["alt_text"] == "White cyclorama"
    assert response.data["display_order"] == 1
    assert StudioImage.objects.filter(studio=studio).count() == 1
    assert upload_mock.call_args.kwargs["folder"] == "mystudio/studios"
    assert "test-secret" not in str(response.data)


@mock.patch("apps.studios.services.upload_image", return_value=UPLOADED)
def test_upload_without_a_display_order_appends(upload_mock, manager_client):
    studio = make_studio()
    make_studio_image(studio=studio, display_order=3)

    response = _upload(manager_client, studio)

    assert response.status_code == 201
    assert response.data["display_order"] == 4
    assert upload_mock.called


def test_anonymous_users_cannot_upload(api_client):
    studio = make_studio()

    response = _upload(api_client, studio)

    assert response.status_code == 401
    assert StudioImage.objects.filter(studio=studio).count() == 0


def test_staff_cannot_upload(auth_client):
    studio = make_studio()

    response = _upload(auth_client, studio)

    assert response.status_code == 403
    assert StudioImage.objects.filter(studio=studio).count() == 0


def test_a_renamed_non_image_is_rejected(manager_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    studio = make_studio()
    payload = {
        "image": SimpleUploadedFile(
            "photo.jpg",
            b"not an image",
            content_type="image/jpeg",
        )
    }

    response = manager_client.post(
        UPLOAD_URL.format(slug=studio.slug),
        payload,
        format="multipart",
    )

    assert response.status_code == 400
    assert "image" in response.data
    assert StudioImage.objects.filter(studio=studio).count() == 0


@override_settings(IMAGE_UPLOAD_MAX_BYTES=20)
def test_oversized_uploads_are_rejected(manager_client):
    studio = make_studio()

    response = _upload(manager_client, studio)

    assert response.status_code == 400
    assert "image" in response.data
    assert StudioImage.objects.filter(studio=studio).count() == 0


@override_settings(
    CLOUDINARY_CLOUD_NAME="",
    CLOUDINARY_API_KEY="",
    CLOUDINARY_API_SECRET="",
)
def test_uploading_without_credentials_returns_503(manager_client):
    studio = make_studio()

    response = _upload(manager_client, studio)

    assert response.status_code == 503
    assert response.data["detail"] == "Image hosting is not configured."


@mock.patch(
    "apps.studios.services.upload_image",
    side_effect=ImageUploadFailed("provider exploded with test-secret"),
)
def test_provider_failure_returns_a_generic_502(upload_mock, manager_client):
    studio = make_studio()

    response = _upload(manager_client, studio)

    assert response.status_code == 502
    assert "test-secret" not in str(response.data)
    assert StudioImage.objects.filter(studio=studio).count() == 0
    assert upload_mock.called


# ---------------------------------------------------------------------------
# Metadata updates
# ---------------------------------------------------------------------------


def test_manager_can_update_display_order(manager_client):
    studio = make_studio()
    image = make_studio_image(studio=studio, display_order=0)

    response = manager_client.patch(
        DETAIL_URL.format(slug=studio.slug, image_id=image.pk),
        {"alt_text": "Side view", "display_order": 9},
        format="json",
    )

    assert response.status_code == 200
    image.refresh_from_db()
    assert image.alt_text == "Side view"
    assert image.display_order == 9


def test_update_requires_at_least_one_field(manager_client):
    studio = make_studio()
    image = make_studio_image(studio=studio)

    response = manager_client.patch(
        DETAIL_URL.format(slug=studio.slug, image_id=image.pk),
        {},
        format="json",
    )

    assert response.status_code == 400


def test_images_are_scoped_to_their_studio(manager_client):
    first = make_studio(name="First Studio")
    second = make_studio(name="Second Studio")
    image = make_studio_image(studio=first)

    response = manager_client.patch(
        DETAIL_URL.format(slug=second.slug, image_id=image.pk),
        {"display_order": 3},
        format="json",
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


@mock.patch("apps.studios.services.delete_image")
def test_manager_can_delete_an_image(delete_mock, manager_client):
    studio = make_studio()
    image = make_studio_image(studio=studio)
    image_pk = image.pk
    public_id = image.cloudinary_public_id

    response = manager_client.delete(
        DETAIL_URL.format(slug=studio.slug, image_id=image_pk)
    )

    assert response.status_code == 204
    assert not StudioImage.objects.filter(pk=image_pk).exists()
    delete_mock.assert_called_once_with(public_id)


@mock.patch(
    "apps.studios.services.delete_image",
    side_effect=ImageDeleteFailed("provider down"),
)
def test_failed_asset_deletion_reports_502(delete_mock, manager_client):
    studio = make_studio()
    image = make_studio_image(studio=studio)

    response = manager_client.delete(
        DETAIL_URL.format(slug=studio.slug, image_id=image.pk)
    )

    assert response.status_code == 502
    # With atomic deletion, a Cloudinary failure rolls back the row deletion.
    # The image stays visible so the catalogue never points at a missing asset.
    assert StudioImage.objects.filter(pk=image.pk).exists()
    assert delete_mock.called


def test_anonymous_users_cannot_delete(api_client):
    studio = make_studio()
    image = make_studio_image(studio=studio)

    response = api_client.delete(
        DETAIL_URL.format(slug=studio.slug, image_id=image.pk)
    )

    assert response.status_code == 401
    assert StudioImage.objects.filter(pk=image.pk).exists()
