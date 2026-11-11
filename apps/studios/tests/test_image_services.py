"""Studio image service behaviour with Cloudinary mocked."""

from unittest import mock

import pytest

from apps.common.cloudinary import (
    ImageDeleteFailed,
    ImageUploadFailed,
    UploadedImage,
)
from apps.common.tests.images import image_upload
from apps.studios import services
from apps.studios.models import StudioImage
from apps.studios.tests.factories import make_studio, make_studio_image

pytestmark = pytest.mark.django_db

UPLOADED = UploadedImage(
    url="https://res.cloudinary.com/demo/image/upload/studio.jpg",
    public_id="mystudio/studios/studio",
)


# ---------------------------------------------------------------------------
# Adding images
# ---------------------------------------------------------------------------


@mock.patch("apps.studios.services.upload_image", return_value=UPLOADED)
def test_add_studio_image_persists_cloudinary_references(upload_mock):
    studio = make_studio()

    image = services.add_studio_image(
        studio=studio,
        image=image_upload(),
        alt_text="White cyclorama",
    )

    assert image.studio == studio
    assert image.image_url == UPLOADED.url
    assert image.cloudinary_public_id == UPLOADED.public_id
    assert image.alt_text == "White cyclorama"
    assert image.display_order == 0
    assert StudioImage.objects.filter(studio=studio).count() == 1
    assert upload_mock.call_args.kwargs["folder"] == "mystudio/studios"


@mock.patch("apps.studios.services.upload_image", return_value=UPLOADED)
def test_new_images_are_appended_to_the_display_order(upload_mock):
    studio = make_studio()
    make_studio_image(studio=studio, display_order=4)

    image = services.add_studio_image(studio=studio, image=image_upload())

    assert image.display_order == 5
    assert upload_mock.called


@mock.patch("apps.studios.services.upload_image", return_value=UPLOADED)
def test_an_explicit_display_order_is_respected(upload_mock):
    studio = make_studio()

    image = services.add_studio_image(
        studio=studio,
        image=image_upload(),
        display_order=2,
    )

    assert image.display_order == 2
    assert upload_mock.called


@mock.patch(
    "apps.studios.services.upload_image",
    side_effect=ImageUploadFailed("provider down"),
)
def test_upload_failure_leaves_no_database_row(upload_mock):
    studio = make_studio()

    with pytest.raises(ImageUploadFailed):
        services.add_studio_image(studio=studio, image=image_upload())

    assert StudioImage.objects.filter(studio=studio).count() == 0
    assert upload_mock.called


def test_database_failure_removes_the_uploaded_asset():
    studio = make_studio()

    with (
        mock.patch(
            "apps.studios.services.upload_image", return_value=UPLOADED
        ),
        mock.patch(
            "apps.studios.services.StudioImage.objects.create",
            side_effect=RuntimeError("database down"),
        ),
        mock.patch("apps.studios.services.delete_image") as delete_mock,
    ):
        with pytest.raises(RuntimeError, match="database down"):
            services.add_studio_image(studio=studio, image=image_upload())

    delete_mock.assert_called_once_with(UPLOADED.public_id)


@mock.patch("apps.studios.services.upload_image", return_value=UPLOADED)
def test_a_failed_compensating_delete_does_not_mask_the_original_error(
    upload_mock,
):
    studio = make_studio()

    with (
        mock.patch(
            "apps.studios.services.StudioImage.objects.create",
            side_effect=RuntimeError("database down"),
        ),
        mock.patch(
            "apps.studios.services.delete_image",
            side_effect=ImageDeleteFailed("cleanup failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="database down"):
            services.add_studio_image(studio=studio, image=image_upload())

    assert upload_mock.called


# ---------------------------------------------------------------------------
# Updating metadata
# ---------------------------------------------------------------------------


def test_update_studio_image_changes_alt_text_and_order():
    image = make_studio_image(alt_text="Old", display_order=1)

    updated = services.update_studio_image(
        image=image,
        alt_text="New",
        display_order=7,
    )
    updated.refresh_from_db()

    assert updated.alt_text == "New"
    assert updated.display_order == 7


def test_update_studio_image_without_changes_is_a_noop():
    image = make_studio_image()

    assert services.update_studio_image(image=image) is image


# ---------------------------------------------------------------------------
# Deletion ordering
# ---------------------------------------------------------------------------


@mock.patch("apps.studios.services.delete_image")
def test_delete_removes_the_row_before_the_cloudinary_asset(delete_mock):
    image = make_studio_image()
    image_pk = image.pk
    public_id = image.cloudinary_public_id
    observed = {}

    def check_order(asset_id):
        observed["row_exists"] = StudioImage.objects.filter(pk=image_pk).exists()
        observed["asset_id"] = asset_id

    delete_mock.side_effect = check_order

    services.delete_studio_image(image=image)

    assert observed["row_exists"] is False
    assert observed["asset_id"] == public_id
    assert not StudioImage.objects.filter(pk=image_pk).exists()
    delete_mock.assert_called_once_with(public_id)


@mock.patch("apps.studios.services.delete_image")
def test_delete_propagates_a_cloudinary_failure(delete_mock):
    image = make_studio_image()
    image_pk = image.pk
    delete_mock.side_effect = ImageDeleteFailed("provider down")

    with pytest.raises(ImageDeleteFailed):
        services.delete_studio_image(image=image)

    # The database row is already gone, so the catalogue does not reference a
    # missing asset; the caller is told that Cloudinary cleanup did not finish.
    assert not StudioImage.objects.filter(pk=image_pk).exists()
