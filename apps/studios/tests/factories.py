"""Helpers for building studio fixtures in tests."""

from uuid import uuid4

from apps.studios.models import Studio, StudioImage


def make_studio(*, name="Natural Light Studio", slug=None, **kwargs):
    """Create a studio. The slug is generated when ``slug`` is omitted."""

    defaults = {
        "description": "A bright north-facing studio.",
        "hourly_rate_kobo": 25_000_00,
        "capacity": 12,
        "amenities": ["WiFi", "Parking"],
    }
    defaults.update(kwargs)

    if slug is not None:
        defaults["slug"] = slug

    return Studio.objects.create(name=name, **defaults)


def make_studio_image(*, studio=None, **kwargs):
    """Create an image row, with a unique fake Cloudinary reference."""

    if studio is None:
        studio = make_studio()

    defaults = {
        "image_url": f"https://res.cloudinary.com/demo/image/upload/{uuid4().hex}.jpg",
        "cloudinary_public_id": f"mystudio/studios/{uuid4().hex}",
        "alt_text": "Studio interior",
        "display_order": 0,
    }
    defaults.update(kwargs)

    return StudioImage.objects.create(studio=studio, **defaults)
