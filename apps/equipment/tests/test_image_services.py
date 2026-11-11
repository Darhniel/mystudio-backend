"""Equipment image service behaviour with Cloudinary mocked."""

from unittest import mock

import pytest

from apps.common.cloudinary import (
    ImageDeleteFailed,
    ImageUploadFailed,
    UploadedImage,
)
from apps.common.tests.images import image_upload
from apps.equipment import services
from apps.equipment.models import Equipment
from apps.equipment.tests.factories import make_equipment, make_equipment_image

pytestmark = pytest.mark.django_db

UPLOADED = UploadedImage(
    url="https://res.cloudinary.com/demo/image/upload/light.jpg",
    public_id="mystudio/equipment/light",
)


# ---------------------------------------------------------------------------
# Upload and replace
# ---------------------------------------------------------------------------


@mock.patch("apps.equipment.services.upload_image", return_value=UPLOADED)
def test_set_equipment_image_stores_the_cloudinary_references(upload_mock):
    equipment = make_equipment()

    with mock.patch("apps.equipment.services.delete_image") as delete_mock:
        services.set_equipment_image(equipment=equipment, image=image_upload())

    equipment.refresh_from_db()

    assert equipment.image_url == UPLOADED.url
    assert equipment.cloudinary_public_id == UPLOADED.public_id
    assert upload_mock.call_args.kwargs["folder"] == "mystudio/equipment"
    delete_mock.assert_not_called()


@mock.patch("apps.equipment.services.upload_image", return_value=UPLOADED)
def test_replacing_an_image_removes_the_previous_asset_after_saving(
    upload_mock,
):
    equipment = make_equipment_image()
    previous_public_id = equipment.cloudinary_public_id
    observed = {}

    def check_order(asset_id):
        equipment.refresh_from_db()
        observed["deleted"] = asset_id
        observed["stored"] = equipment.cloudinary_public_id

    with mock.patch(
        "apps.equipment.services.delete_image", side_effect=check_order
    ) as delete_mock:
        services.set_equipment_image(equipment=equipment, image=image_upload())

    equipment.refresh_from_db()

    assert observed["deleted"] == previous_public_id
    assert observed["stored"] == UPLOADED.public_id
    assert equipment.cloudinary_public_id == UPLOADED.public_id
    delete_mock.assert_called_once_with(previous_public_id)
    assert upload_mock.called


@mock.patch(
    "apps.equipment.services.upload_image",
    side_effect=ImageUploadFailed("provider down"),
)
def test_upload_failure_leaves_the_equipment_unchanged(upload_mock):
    equipment = make_equipment_image()
    original_url = equipment.image_url
    original_public_id = equipment.cloudinary_public_id

    with pytest.raises(ImageUploadFailed):
        services.set_equipment_image(equipment=equipment, image=image_upload())

    equipment.refresh_from_db()

    assert equipment.image_url == original_url
    assert equipment.cloudinary_public_id == original_public_id
    assert upload_mock.called


def test_database_failure_removes_the_new_upload():
    equipment = make_equipment()

    with (
        mock.patch(
            "apps.equipment.services.upload_image", return_value=UPLOADED
        ),
        mock.patch.object(
            Equipment,
            "save",
            side_effect=RuntimeError("database down"),
        ),
        mock.patch("apps.equipment.services.delete_image") as delete_mock,
    ):
        with pytest.raises(RuntimeError, match="database down"):
            services.set_equipment_image(
                equipment=equipment,
                image=image_upload(),
            )

    delete_mock.assert_called_once_with(UPLOADED.public_id)


@mock.patch("apps.equipment.services.upload_image", return_value=UPLOADED)
def test_a_failed_previous_asset_delete_does_not_fail_the_replacement(
    upload_mock,
    caplog,
):
    equipment = make_equipment_image()

    with mock.patch(
        "apps.equipment.services.delete_image",
        side_effect=ImageDeleteFailed("provider down"),
    ):
        services.set_equipment_image(equipment=equipment, image=image_upload())

    equipment.refresh_from_db()

    assert equipment.cloudinary_public_id == UPLOADED.public_id
    assert "Could not remove the replaced equipment image" in caplog.text
    assert upload_mock.called


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


@mock.patch("apps.equipment.services.delete_image")
def test_remove_clears_the_row_before_deleting_the_asset(delete_mock):
    equipment = make_equipment_image()
    previous_public_id = equipment.cloudinary_public_id
    observed = {}

    def check_order(asset_id):
        equipment.refresh_from_db()
        observed["cleared_public_id"] = equipment.cloudinary_public_id
        observed["cleared_url"] = equipment.image_url
        observed["asset_id"] = asset_id

    delete_mock.side_effect = check_order

    services.remove_equipment_image(equipment=equipment)

    equipment.refresh_from_db()

    assert observed["cleared_public_id"] is None
    assert observed["cleared_url"] is None
    assert observed["asset_id"] == previous_public_id
    assert equipment.image_url is None
    assert equipment.cloudinary_public_id is None
    delete_mock.assert_called_once_with(previous_public_id)


@mock.patch("apps.equipment.services.delete_image")
def test_removing_an_image_that_is_not_there_is_a_noop(delete_mock):
    equipment = make_equipment()

    services.remove_equipment_image(equipment=equipment)

    delete_mock.assert_not_called()


@mock.patch("apps.equipment.services.delete_image")
def test_remove_propagates_a_cloudinary_failure(delete_mock):
    equipment = make_equipment_image()
    delete_mock.side_effect = ImageDeleteFailed("provider down")

    with pytest.raises(ImageDeleteFailed):
        services.remove_equipment_image(equipment=equipment)

    equipment.refresh_from_db()

    assert equipment.image_url is None
    assert equipment.cloudinary_public_id is None
