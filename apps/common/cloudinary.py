"""Cloudinary image hosting.

The rest of the codebase never talks to the SDK directly. Services call
:func:`upload_image` and :func:`delete_image`, which:

* validate the real image format with Pillow (not the filename extension);
* enforce the configured size limit;
* fail with typed exceptions instead of provider-specific errors;
* never include credentials in exception messages or logs.

The module is import-safe without credentials: configuration is checked lazily,
so tests and local development (where uploads may be disabled) still start.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cloudinary
import cloudinary.uploader
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger("mystudio.images")
logger.addHandler(logging.NullHandler())

ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
ALLOWED_EXTENSIONS = ("jpg", "jpeg", "png", "webp")
DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class CloudinaryError(Exception):
    """Base class for image hosting failures."""


class ImageHostingNotConfigured(CloudinaryError):
    """Raised when the Cloudinary credentials are missing."""


class ImageUploadFailed(CloudinaryError):
    """Raised when Cloudinary cannot store an image."""


class ImageDeleteFailed(CloudinaryError):
    """Raised when Cloudinary cannot remove an image."""


@dataclass(frozen=True)
class UploadedImage:
    """The two values persisted in PostgreSQL."""

    url: str
    public_id: str


def is_configured() -> bool:
    """Return whether all three Cloudinary credentials are present."""

    return bool(
        getattr(settings, "CLOUDINARY_CLOUD_NAME", "")
        and getattr(settings, "CLOUDINARY_API_KEY", "")
        and getattr(settings, "CLOUDINARY_API_SECRET", "")
    )


def _configure() -> None:
    if not is_configured():
        raise ImageHostingNotConfigured(
            "Cloudinary credentials are not configured."
        )

    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


def _max_upload_bytes() -> int:
    return int(
        getattr(settings, "IMAGE_UPLOAD_MAX_BYTES", DEFAULT_MAX_UPLOAD_BYTES)
    )


def validate_image_file(file) -> str:
    """Validate an uploaded file and return its detected Pillow format.

    The file extension and the browser-supplied content type are ignored: the
    bytes are opened by Pillow so a renamed text file, GIF or SVG is rejected.
    """

    if not isinstance(file, UploadedFile) or not file.size:
        raise ValidationError({"image": "Select an image file to upload."})

    if file.size > _max_upload_bytes():
        limit_mb = max(_max_upload_bytes() // (1024 * 1024), 1)
        raise ValidationError(
            {"image": f"Images must be {limit_mb} MB or smaller."}
        )

    file.seek(0)
    try:
        with Image.open(file) as image:
            image_format = (image.format or "").upper()
            if image_format not in ALLOWED_IMAGE_FORMATS:
                raise ValidationError(
                    {"image": "Upload a JPEG, PNG or WebP image."}
                )
            # ``verify()`` parses the container and catches truncated or
            # malformed files that still have a recognisable header.
            image.verify()
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ValidationError(
            {"image": "The uploaded file is not a valid image."}
        )
    except Image.DecompressionBombError:
        raise ValidationError(
            {"image": "The uploaded image is too large to process."}
        )
    finally:
        file.seek(0)

    return image_format


def upload_image(file, *, folder: str) -> UploadedImage:
    """Validate ``file`` and store it in the given Cloudinary folder.

    Raises Django ``ValidationError`` for unacceptable files and
    :class:`ImageUploadFailed` when the provider rejects the upload.
    """

    validate_image_file(file)
    _configure()

    try:
        result = cloudinary.uploader.upload(
            file,
            folder=folder,
            resource_type="image",
            allowed_formats=list(ALLOWED_EXTENSIONS),
            unique_filename=True,
            overwrite=False,
            use_filename=False,
        )
    except Exception as exc:  # provider errors have many concrete types
        # Log the exception type only. Provider payloads can contain request
        # metadata, and the secret must never reach a log line.
        logger.warning(
            "Cloudinary upload failed for folder %s (%s)",
            folder,
            type(exc).__name__,
        )
        raise ImageUploadFailed("The image could not be uploaded.") from exc

    url = result.get("secure_url") if isinstance(result, dict) else None
    public_id = result.get("public_id") if isinstance(result, dict) else None
    if not url or not public_id:
        logger.warning(
            "Cloudinary returned an incomplete upload response for folder %s",
            folder,
        )
        raise ImageUploadFailed("The image service returned an invalid response.")

    return UploadedImage(url=url, public_id=public_id)


def delete_image(public_id: str | None) -> None:
    """Remove a Cloudinary asset.

    A missing asset is treated as success so callers can retry safely.
    """

    if not public_id:
        return

    _configure()

    try:
        result = cloudinary.uploader.destroy(
            public_id,
            resource_type="image",
            invalidate=True,
        )
    except Exception as exc:
        logger.warning(
            "Cloudinary delete failed (%s)",
            type(exc).__name__,
        )
        raise ImageDeleteFailed("The image could not be removed.") from exc

    status = result.get("result") if isinstance(result, dict) else None
    if status not in {"ok", "not found"}:
        logger.warning("Cloudinary returned an unexpected delete response")
        raise ImageDeleteFailed("The image service returned an invalid response.")
