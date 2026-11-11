"""Small helpers shared by the studio models and services."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils.text import slugify

FALLBACK_SLUG = "studio"

#: Number of generated-slug attempts before a database race is reported.
SLUG_GENERATION_ATTEMPTS = 5


def normalise_slug(value: str | None) -> str:
    """Return the canonical lower-case slug form for ``value``.

    Slugs are public identifiers, so ``My-Studio`` and ``my-studio`` must mean
    the same row. ``slugify`` lower-cases and strips unsupported characters; a
    value that becomes empty is treated as blank by the caller.
    """

    return slugify(value or "")


def is_slug_conflict(exc: IntegrityError) -> bool:
    """Return whether ``exc`` looks like a unique-slug violation.

    PostgreSQL and SQLite both include the column or constraint name in the
    driver message. Keeping the check small avoids depending on a specific
    database backend while still allowing other integrity errors to surface.
    """

    return "slug" in str(exc).lower()


def validate_amenities(value):
    """Require a JSON list of non-empty strings.

    Amenities are stored denormalised on the studio so public responses never
    need a join. The list shape is enforced here so invalid payloads fail
    validation before they reach the database.
    """

    if not isinstance(value, list):
        raise ValidationError("Amenities must be a list of strings.")

    for amenity in value:
        if not isinstance(amenity, str) or not amenity.strip():
            raise ValidationError("Each amenity must be a non-empty string.")


def build_unique_slug(
    model,
    value,
    *,
    field_name="slug",
    max_length=160,
    instance_pk=None,
    fallback=FALLBACK_SLUG,
):
    """Return a slug that no other row in ``model`` already uses.

    A blank or unsluggable value falls back to ``fallback``. Collisions get a
    numeric suffix (``-2``, ``-3``, ...), and the result never exceeds
    ``max_length``. Matching is case-insensitive, so an existing ``My-Studio``
    row also reserves ``my-studio``. When updating an existing row, pass
    ``instance_pk`` so the row does not collide with itself.
    """

    base = (normalise_slug(value) or fallback)[:max_length]

    queryset = model._default_manager.all()
    if instance_pk is not None:
        queryset = queryset.exclude(pk=instance_pk)

    candidate = base
    suffix = 2
    lookup = f"{field_name}__iexact"
    while queryset.filter(**{lookup: candidate}).exists():
        suffix_text = f"-{suffix}"
        candidate = f"{base[: max_length - len(suffix_text)]}{suffix_text}"
        suffix += 1

    return candidate
