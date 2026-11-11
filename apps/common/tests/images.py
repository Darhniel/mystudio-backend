"""In-memory image helpers for tests.

Files are generated with Pillow so validation exercises real image bytes. No
network access and no Cloudinary credentials are required.
"""

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

CONTENT_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}

EXTENSIONS = {
    "JPEG": "jpg",
    "PNG": "png",
    "WEBP": "webp",
    "GIF": "gif",
}


def image_bytes(image_format="JPEG", size=(16, 16)):
    buffer = BytesIO()
    Image.new("RGB", size, (220, 220, 220)).save(buffer, format=image_format)
    return buffer.getvalue()


def image_upload(*, name=None, image_format="JPEG"):
    """Build an ``UploadedFile`` with real bytes for ``image_format``."""

    return SimpleUploadedFile(
        name or f"upload.{EXTENSIONS[image_format]}",
        image_bytes(image_format),
        content_type=CONTENT_TYPES[image_format],
    )
