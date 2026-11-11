"""Abstract base models shared across business apps.

Concrete entities should inherit from :class:`UUIDTimeStampedModel` so every
business table gets a UUID primary key and audit timestamps. Django's own
framework tables keep their default integer keys.
"""

import uuid

from django.db import models
from django.utils import timezone


class UUIDModel(models.Model):
    """Abstract model with a UUID primary key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """Abstract model tracking creation and last update timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        get_latest_by = "created_at"
        ordering = ("-created_at",)


class UUIDTimeStampedModel(UUIDModel, TimeStampedModel):
    """The standard base for every business entity."""

    class Meta:
        abstract = True
        get_latest_by = "created_at"
        ordering = ("-created_at",)


class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet that marks rows as deleted instead of removing them."""

    def delete(self):
        count = super().update(is_deleted=True, deleted_at=timezone.now())
        return count, {self.model._meta.label: count}

    def hard_delete(self):
        return super().delete()

    def alive(self):
        return self.filter(is_deleted=False)

    def dead(self):
        return self.filter(is_deleted=True)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Default manager that hides soft-deleted rows."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class SoftDeleteModel(models.Model):
    """Abstract model with opt-in soft deletion.

    ``objects`` excludes deleted rows; use ``all_objects`` to include them.
    Soft deletion is not cascaded to related rows, so callers must decide
    explicitly what happens to dependants.
    """

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(using=using, update_fields=["is_deleted", "deleted_at"])
        return 1, {self._meta.label: 1}

    def restore(self, *, using=None):
        self.is_deleted = False
        self.deleted_at = None
        self.save(using=using, update_fields=["is_deleted", "deleted_at"])


class DeactivateOnDeleteQuerySet(models.QuerySet):
    """QuerySet that retires rows instead of removing them."""

    def delete(self):
        count = super().update(is_active=False, updated_at=timezone.now())
        return count, {self.model._meta.label: count}

    def hard_delete(self):
        """Explicit escape hatch for data cleanup and tests."""
        return super().delete()


class DeactivateOnDeleteManager(
    models.Manager.from_queryset(DeactivateOnDeleteQuerySet)
):
    """Default manager whose ``delete()`` deactivates instead of removing."""


class DeactivateOnDeleteModel(models.Model):
    """Abstract model that turns deletion into deactivation.

    Historical records must survive because future bookings reference them.
    Subclasses therefore expose ``is_active`` and ``updated_at``; calling
    ``delete()`` (on an instance or a queryset) only flips ``is_active``.
    ``hard_delete()`` remains available on the queryset for explicit cleanup.
    """

    objects = DeactivateOnDeleteManager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.is_active = False
        self.save(using=using, update_fields=["is_active", "updated_at"])
        return 1, {self._meta.label: 1}
