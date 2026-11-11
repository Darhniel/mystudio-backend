"""Write-side business logic for the equipment catalogue.

Equipment is global shared inventory; the image helpers here replace or remove
the single image attached to an item. Cloudinary access is centralised in
:mod:`apps.common.cloudinary`.

Image writes and the matching Cloudinary cleanup run in one database
transaction. A provider failure therefore rolls the row back to its previous
state, and a freshly uploaded replacement is deleted as a compensating action,
so a failed request never leaves an unreferenced asset behind.
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

    A replacement uploads the new file first, saves the new references and
    deletes the previous asset inside the same transaction. If either the
    database write or the provider deletion fails, the row returns to its
    previous state and the new upload is destroyed. Removal follows the same
    rule: the cleared row is committed only after Cloudinary confirms the
    deletion, so a provider outage never orphans an asset.
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

    try:
        with transaction.atomic():
            equipment.save(update_fields=list(set(changes) | {"updated_at"}))
            if uploaded is not None:
                _delete_previous(previous_public_id, keep=uploaded.public_id)
            elif clearing:
                delete_image(previous_public_id)
    except Exception:
        if uploaded is not None:
            _discard_upload(uploaded.public_id)
        # The database transaction was rolled back; restore the in-memory
        # instance so callers do not accidentally use the rejected values. A
        # database outage can make the refresh fail too; never mask the
        # original exception in that case.
        try:
            equipment.refresh_from_db()
        except Exception:
            logger.warning(
                "Could not refresh equipment state after a failed image write"
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


def _delete_previous(public_id: str | None, *, keep: str | None = None) -> None:
    """Delete a replaced asset; failures propagate so the caller can roll back."""

    if not public_id or public_id == keep:
        return
    delete_image(public_id)


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
