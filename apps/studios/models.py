"""Database models for studios and their images.

Business rules live in ``services.py``; read queries live in ``selectors.py``.
The models only publish the database contract: validation, constraints,
ordering and the deletion behaviour that protects booking history.
"""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction

from apps.common.constants import MAX_DATABASE_INTEGER
from apps.common.models import (
    DeactivateOnDeleteModel,
    UUIDTimeStampedModel,
)
from apps.studios.utils import (
    SLUG_GENERATION_ATTEMPTS,
    build_unique_slug,
    is_slug_conflict,
    normalise_slug,
    validate_amenities,
)


class Studio(UUIDTimeStampedModel, DeactivateOnDeleteModel):
    """A rentable photography studio."""

    name = models.CharField(max_length=150)
    slug = models.SlugField(
        max_length=160,
        unique=True,
        help_text="Public identifier, generated from the name when left blank.",
    )
    description = models.TextField(blank=True)
    hourly_rate_kobo = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_DATABASE_INTEGER),
        ],
        help_text="Hourly rental price in kobo.",
    )
    capacity = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_DATABASE_INTEGER),
        ],
        help_text="Maximum number of people allowed in the studio.",
    )
    amenities = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_amenities],
        help_text='List of amenity names, for example ["WiFi", "Parking"].',
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive studios are hidden from customers and kept for history.",
    )

    class Meta:
        ordering = ("name",)
        indexes = [
            models.Index(fields=["is_active", "name"], name="studio_active_name_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(hourly_rate_kobo__gte=1),
                name="studio_hourly_rate_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gte=1),
                name="studio_capacity_positive",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()

        # ``blank=True`` is required so an empty list is valid, which means
        # Django treats NULL as an empty value too. NULL cannot be stored, so
        # reject it during validation instead of at the database.
        if self.amenities is None:
            raise ValidationError(
                {"amenities": "Amenities cannot be null; use an empty list."}
            )

    def save(self, *args, **kwargs):
        # Explicit slugs are canonicalised so ``My-Studio`` and ``my-studio``
        # cannot coexist. A blank or unsluggable value falls back to generation.
        if self.slug:
            self.slug = normalise_slug(self.slug)

        if self.slug:
            return super().save(*args, **kwargs)

        # Two concurrent inserts can choose the same generated slug. Retry in a
        # savepoint: the failed insert is rolled back, ``build_unique_slug()``
        # sees the winning row and returns the next suffix.
        for attempt in range(SLUG_GENERATION_ATTEMPTS):
            self.slug = build_unique_slug(
                type(self),
                self.name,
                max_length=self._meta.get_field("slug").max_length,
                instance_pk=self.pk,
            )
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:
                if (
                    not is_slug_conflict(exc)
                    or attempt == SLUG_GENERATION_ATTEMPTS - 1
                ):
                    raise

        raise AssertionError("unreachable")  # pragma: no cover


class StudioImage(UUIDTimeStampedModel):
    """A Cloudinary-hosted image belonging to a studio.

    Studios deactivate instead of deleting, so images stay manageable: an image
    row may be removed, but a studio with images cannot be hard-deleted.
    """

    studio = models.ForeignKey(
        Studio,
        on_delete=models.PROTECT,
        related_name="images",
    )
    image_url = models.URLField(max_length=500)
    cloudinary_public_id = models.CharField(max_length=255, unique=True)
    alt_text = models.CharField(max_length=200, blank=True, default="")
    display_order = models.PositiveIntegerField(
        default=0,
        validators=[MaxValueValidator(MAX_DATABASE_INTEGER)],
        help_text=(
            "Lower values appear first. Duplicate values are allowed; ties "
            "are broken by created_at and then id."
        ),
    )

    class Meta:
        # ``id`` is the final tie-breaker: created_at can theoretically collide
        # when several rows are inserted in the same clock tick, and a stable
        # order keeps pagination and the public gallery deterministic.
        ordering = ("display_order", "created_at", "id")
        indexes = [
            models.Index(
                fields=["studio", "display_order"],
                name="studioimage_order_idx",
            ),
        ]

    def __str__(self):
        return f"{self.studio.name} image {self.display_order}"
