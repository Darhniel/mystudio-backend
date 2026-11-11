"""Database models for the equipment catalogue.

Equipment inventory is shared globally across every studio: one catalogue and
one ``stock_quantity`` per item. Future bookings will reserve quantities from
that single pool, so there is deliberately no studio foreign key here.
"""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common.constants import MAX_DATABASE_INTEGER
from apps.common.models import DeactivateOnDeleteModel, UUIDTimeStampedModel


class Equipment(UUIDTimeStampedModel, DeactivateOnDeleteModel):
    """A rentable item or accessory, shared by every studio."""

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    fixed_price_kobo = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_DATABASE_INTEGER),
        ],
        help_text="Fixed price per item per booking, in kobo.",
    )
    stock_quantity = models.PositiveIntegerField(
        validators=[
            MinValueValidator(0),
            MaxValueValidator(MAX_DATABASE_INTEGER),
        ],
        help_text="Total units owned. Zero means temporarily out of stock.",
    )
    image_url = models.URLField(max_length=500, null=True, blank=True)
    cloudinary_public_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Cloudinary identifier, set together with image_url.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive items are hidden from customers and kept for history.",
    )

    class Meta:
        ordering = ("name",)
        indexes = [
            models.Index(
                fields=["is_active", "name"],
                name="equipment_active_name_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fixed_price_kobo__gte=1),
                name="equipment_fixed_price_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(stock_quantity__gte=0),
                name="equipment_stock_non_negative",
            ),
            models.UniqueConstraint(
                fields=["cloudinary_public_id"],
                condition=models.Q(cloudinary_public_id__isnull=False),
                name="equipment_unique_public_id",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()

        has_url = bool(self.image_url)
        has_public_id = bool(self.cloudinary_public_id)
        if has_url != has_public_id:
            message = "Set both image_url and cloudinary_public_id, or neither."
            raise ValidationError(
                {
                    "image_url": message,
                    "cloudinary_public_id": message,
                }
            )

    def save(self, *args, **kwargs):
        # Collapse empty strings to NULL so the partial unique constraint and
        # the optional-image checks only see a real Cloudinary reference.
        self.image_url = self.image_url or None
        self.cloudinary_public_id = self.cloudinary_public_id or None
        super().save(*args, **kwargs)
