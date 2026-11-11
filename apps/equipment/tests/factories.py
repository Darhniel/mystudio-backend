"""Helpers for building equipment fixtures in tests."""

from uuid import uuid4

from apps.equipment.models import Equipment


def make_equipment(*, name="Godox SL-60W light", **kwargs):
    """Create an equipment item, optionally with a fake Cloudinary image."""

    defaults = {
        "description": "Continuous LED light with a Bowens mount.",
        "fixed_price_kobo": 5_000_00,
        "stock_quantity": 4,
    }
    defaults.update(kwargs)

    return Equipment.objects.create(name=name, **defaults)


def make_equipment_image(*, name="Reflector", **kwargs):
    """Create an equipment item that already has a Cloudinary reference."""

    token = uuid4().hex
    defaults = {
        "image_url": f"https://res.cloudinary.com/demo/image/upload/{token}.jpg",
        "cloudinary_public_id": f"mystudio/equipment/{token}",
    }
    defaults.update(kwargs)

    return make_equipment(name=name, **defaults)
