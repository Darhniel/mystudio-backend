"""Write-side business logic for the equipment catalogue.

Equipment is global shared inventory; the image helpers here replace or remove
the single image attached to an item. Cloudinary access is centralised in
:mod:`apps.common.cloudinary`.

Image replacement uploads the new file first, saves the new references, and
then attempts to delete the previous asset. A failure to delete the old asset
is logged but does not fail the replacement, because the new image is already
active and the catalogue must not point at a missing file.

Image removal (clearing the image without a replacement) clears the row and
deletes the Cloudinary asset in one transaction. A provider failure rolls the
row back to its previous state so the catalogue never points at a deleted
asset or orphans an asset with no database reference.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.common.cloudinary import (
    CloudinaryError,
    delete_image,
    upload_image,
)
from apps.equipment.models import Equipment

logger = logging.getLogger("mystudio.equipment")
logger.addHandler(logging.NullHandler())

EQUIPMENT_IMAGE_FOLDER = "mystudio/equipment"


def create_equipment(*, image=None, **data) -> Equipment:
    """Create an equipment item, optionally with an uploaded image.

    The image reaches Cloudinary first. If the database insert then fails, the
    fresh asset is removed so PostgreSQL and Cloudinary stay in sync.
    """

    uploaded = None
    if image is not None:
        uploaded = upload_image(image, folder=EQUIPMENT_IMAGE_FOLDER)
        data["image_url"] = uploaded.url
        data["cloudinary_public_id"] = uploaded.public_id

    try:
        with transaction.atomic():
            return Equipment.objects.create(**data)
    except Exception:
        if uploaded is not None:
            _discard_upload(uploaded.public_id)
        raise


def update_equipment(
    *,
    equipment: Equipment,
    image=None,
    remove_image: bool = False,
    **changes,
) -> Equipment:
    """Apply validated changes, optionally replacing or clearing the image.

    Replacement: uploads the new file, saves the new references, then attempts
    to delete the previous asset. A failure to delete the old asset is logged
    but the replacement succeeds because the new image is already active.

    Removal: clears the row and deletes the Cloudinary asset in one transaction.
    A provider failure rolls the row back so the catalogue never points at a
    deleted asset or orphans an asset with no database reference.
    """

    if image is not None and remove_image:
        raise ValidationError(
            {
                "remove_image": (
                    "Upload a new image or remove the current one, not both."
                )
            }
        )

    uploaded = None
    previous_public_id = equipment.cloudinary_public_id
    clearing = remove_image

    if image is not None:
        uploaded = upload_image(image, folder=EQUIPMENT_IMAGE_FOLDER)
        changes["image_url"] = uploaded.url
        changes["cloudinary_public_id"] = uploaded.public_id
    elif remove_image:
        changes["image_url"] = None
        changes["cloudinary_public_id"] = None

    if not changes:
        return equipment

    for field, value in changes.items():
        setattr(equipment, field, value)

    # Replacement: save new refs first, then try to delete old asset outside
    # the transaction. If old-asset deletion fails, the replacement still
    # succeeds and the failure is logged.
    if uploaded is not None:
        try:
            equipment.save(update_fields=list(set(changes) | {"updated_at"}))
        except Exception:
            _discard_upload(uploaded.public_id)
            raise
        _remove_previous(previous_public_id, keep=uploaded.public_id)
        return equipment

    # Removal: clear row and delete asset atomically. Failure rolls back.
    try:
        with transaction.atomic():
            equipment.save(update_fields=list(set(changes) | {"updated_at"}))
            delete_image(previous_public_id)
    except Exception:
        # The database transaction was rolled back; restore the in-memory
        # instance so callers do not accidentally use the rejected values. A
        # database outage can make the refresh fail too; never mask the
        # original exception in that case.
        try:
            equipment.refresh_from_db()
        except Exception:
            logger.warning(
                "Could not refresh equipment state after a failed image removal"
            )
        raise

    return equipment


def deactivate_equipment(*, equipment: Equipment) -> Equipment:
    """Retire an equipment item without deleting its row."""

    if equipment.is_active:
        equipment.delete()
    return equipment


def _discard_upload(public_id: str) -> None:
    """Best-effort cleanup when the database write after an upload fails."""

    try:
        delete_image(public_id)
    except CloudinaryError:
        logger.warning(
            "Could not remove an uploaded equipment image after a database error"
        )


def _remove_previous(public_id: str | None, *, keep: str | None = None) -> None:
    """Delete a replaced asset without failing an otherwise successful upload.

    This matches the studio image replacement behaviour: the replacement is
    already active; an orphaned asset is preferable to reporting failure for
    an operation the user can see succeeded.
    """

    if not public_id or public_id == keep:
        return

    try:
        delete_image(public_id)
    except CloudinaryError:
        logger.warning("Could not remove the replaced equipment image")


def set_equipment_image(*, equipment: Equipment, image) -> Equipment:
    """Upload an image and use it as the equipment image.

    This is a thin alias over :func:`update_equipment`; both the admin serializer
    and the standalone image endpoint use the same transactional replacement
    path.
    """

    return update_equipment(equipment=equipment, image=image)


def remove_equipment_image(*, equipment: Equipment) -> Equipment:
    """Clear the equipment image and delete its Cloudinary asset.

    Removing an item that has no image is a no-op. The row is cleared and the
    provider deletion runs in one transaction, so a failure leaves the item
    unchanged rather than pointing at a missing file or orphaning the asset.
    """

    if not equipment.cloudinary_public_id and not equipment.image_url:
        return equipment
    return update_equipment(equipment=equipment, remove_image=True)