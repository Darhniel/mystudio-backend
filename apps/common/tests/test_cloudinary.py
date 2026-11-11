"""Cloudinary wrapper behaviour, with no network access."""

import logging
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import features

from apps.common import cloudinary as image_service
from apps.common.cloudinary import (
    ImageDeleteFailed,
    ImageHostingNotConfigured,
    ImageUploadFailed,
    UploadedImage,
)
from apps.common.tests.images import image_upload

CREDENTIALS = {
    "CLOUDINARY_CLOUD_NAME": "test-cloud",
    "CLOUDINARY_API_KEY": "test-key",
    "CLOUDINARY_API_SECRET": "test-secret",
}

FOLDER = "mystudio/studios"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_validate_image_file_accepts_supported_formats(image_format):
    if image_format == "WEBP" and not features.check("webp"):
        pytest.skip("Pillow was built without WebP support")

    assert image_service.validate_image_file(
        image_upload(image_format=image_format)
    ) == image_format


def test_validate_image_file_rejects_gif():
    with pytest.raises(ValidationError) as exc_info:
        image_service.validate_image_file(image_upload(image_format="GIF"))

    assert "image" in exc_info.value.message_dict


def test_validate_image_file_rejects_a_renamed_text_file():
    upload = SimpleUploadedFile(
        "photo.jpg",
        b"this is not an image",
        content_type="image/jpeg",
    )

    with pytest.raises(ValidationError) as exc_info:
        image_service.validate_image_file(upload)

    assert "image" in exc_info.value.message_dict


def test_validate_image_file_rejects_empty_files():
    upload = SimpleUploadedFile("photo.jpg", b"", content_type="image/jpeg")

    with pytest.raises(ValidationError):
        image_service.validate_image_file(upload)


def test_validate_image_file_rejects_oversized_files():
    upload = SimpleUploadedFile(
        "photo.jpg",
        b"x" * (5 * 1024 * 1024 + 1),
        content_type="image/jpeg",
    )

    with pytest.raises(ValidationError) as exc_info:
        image_service.validate_image_file(upload)

    assert "5 MB" in str(exc_info.value.message_dict["image"][0])


@override_settings(IMAGE_UPLOAD_MAX_BYTES=20)
def test_validate_image_file_honours_the_configured_size_limit():
    with pytest.raises(ValidationError):
        image_service.validate_image_file(image_upload())


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@override_settings(**CREDENTIALS)
def test_is_configured_when_all_credentials_are_present():
    assert image_service.is_configured() is True


@override_settings(
    CLOUDINARY_CLOUD_NAME="",
    CLOUDINARY_API_KEY="",
    CLOUDINARY_API_SECRET="",
)
def test_is_configured_is_false_without_credentials():
    assert image_service.is_configured() is False


@override_settings(
    CLOUDINARY_CLOUD_NAME="",
    CLOUDINARY_API_KEY="",
    CLOUDINARY_API_SECRET="",
)
def test_upload_without_credentials_fails_before_calling_cloudinary():
    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.upload"
    ) as upload_mock:
        with pytest.raises(ImageHostingNotConfigured):
            image_service.upload_image(image_upload(), folder=FOLDER)

    upload_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@override_settings(**CREDENTIALS)
def test_upload_returns_the_secure_url_and_public_id():
    response = {
        "secure_url": "https://res.cloudinary.com/demo/image/upload/photo.jpg",
        "public_id": "mystudio/studios/photo",
    }

    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.upload",
        return_value=response,
    ) as upload_mock:
        uploaded = image_service.upload_image(image_upload(), folder=FOLDER)

    assert uploaded == UploadedImage(
        url=response["secure_url"],
        public_id=response["public_id"],
    )
    kwargs = upload_mock.call_args.kwargs
    assert kwargs["folder"] == FOLDER
    assert kwargs["resource_type"] == "image"
    assert kwargs["allowed_formats"] == ["jpg", "jpeg", "png", "webp"]
    assert kwargs["overwrite"] is False


@override_settings(**CREDENTIALS)
def test_upload_failure_is_wrapped_and_does_not_leak_details(caplog):
    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.upload",
        side_effect=RuntimeError("provider exploded secret=test-secret"),
    ):
        with caplog.at_level(logging.WARNING):
            with pytest.raises(ImageUploadFailed) as exc_info:
                image_service.upload_image(image_upload(), folder=FOLDER)

    assert "test-secret" not in str(exc_info.value)
    assert "test-secret" not in caplog.text
    assert "RuntimeError" in caplog.text


@override_settings(**CREDENTIALS)
def test_upload_rejects_an_incomplete_provider_response():
    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.upload",
        return_value={"secure_url": None, "public_id": None},
    ):
        with pytest.raises(ImageUploadFailed):
            image_service.upload_image(image_upload(), folder=FOLDER)


@override_settings(**CREDENTIALS)
def test_upload_validates_before_calling_the_provider():
    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.upload"
    ) as upload_mock:
        with pytest.raises(ValidationError):
            image_service.upload_image(
                image_upload(image_format="GIF"),
                folder=FOLDER,
            )

    upload_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


@override_settings(**CREDENTIALS)
def test_delete_calls_destroy_with_image_resource_type():
    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.destroy",
        return_value={"result": "ok"},
    ) as destroy_mock:
        image_service.delete_image("mystudio/studios/photo")

    destroy_mock.assert_called_once_with(
        "mystudio/studios/photo",
        resource_type="image",
        invalidate=True,
    )


@override_settings(**CREDENTIALS)
def test_delete_treats_a_missing_asset_as_success():
    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.destroy",
        return_value={"result": "not found"},
    ):
        image_service.delete_image("mystudio/studios/missing")


def test_delete_without_a_public_id_is_a_noop():
    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.destroy"
    ) as destroy_mock:
        image_service.delete_image(None)
        image_service.delete_image("")

    destroy_mock.assert_not_called()


@override_settings(**CREDENTIALS)
def test_delete_failure_is_wrapped(caplog):
    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.destroy",
        side_effect=RuntimeError("provider exploded secret=test-secret"),
    ):
        with caplog.at_level(logging.WARNING):
            with pytest.raises(ImageDeleteFailed) as exc_info:
                image_service.delete_image("mystudio/studios/photo")

    assert "test-secret" not in str(exc_info.value)
    assert "test-secret" not in caplog.text


@override_settings(**CREDENTIALS)
def test_delete_rejects_an_unexpected_provider_response():
    with mock.patch(
        "apps.common.cloudinary.cloudinary.uploader.destroy",
        return_value={"result": "error"},
    ):
        with pytest.raises(ImageDeleteFailed):
            image_service.delete_image("mystudio/studios/photo")
