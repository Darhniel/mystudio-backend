"""The custom user model.

Accounts belong to platform staff only. Customers make bookings as guests and
never receive a user record.

Email is the login identifier and the primary key is a UUID, so accounts can be
referenced safely across services and exposed in URLs without leaking counts.
"""

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.accounts.managers import UserManager
from apps.common.models import UUIDModel


class Role(models.TextChoices):
    """Platform roles.

    ``OWNER`` is the platform owner, ``MANAGER`` runs day-to-day operations and
    ``STAFF`` handles routine administrative work. Roles describe business
    authority; Django's ``is_staff`` and ``is_superuser`` flags remain separate
    and control the admin site and unrestricted permissions.
    """

    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    STAFF = "staff", "Staff"


class User(AbstractBaseUser, PermissionsMixin, UUIDModel):
    # Expose the choices on the model so callers can use ``User.Role``.
    Role = Role

    email = models.EmailField(unique=True, verbose_name="email address")
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    is_staff = models.BooleanField(
        default=False,
        help_text="Can sign in to the Django admin site.",
    )
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(
        default=timezone.now,
        editable=False,
        db_index=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        db_table = "accounts_user"
        ordering = ("-date_joined",)
        get_latest_by = "date_joined"
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci_unique",
                violation_error_message="A user with this email address already exists.",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=Role.values),
                name="accounts_user_role_valid",
                violation_error_message="Select a valid role.",
            ),
        ]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        # Normalise on every write path, including the admin and data imports.
        self.email = self.__class__.objects.normalize_email(self.email)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.email:
            self.email = self.__class__.objects.normalize_email(self.email)
        if self.role and self.role not in Role.values:
            raise ValidationError({"role": "Select a valid role."})
        # The owner role and unrestricted Django permissions describe the same
        # account. The manager enforces this at creation; clean() protects the
        # admin site and every explicit full_clean() call.
        if self.role == Role.OWNER and not self.is_superuser:
            raise ValidationError(
                {"role": "The owner role requires superuser access."}
            )
        if self.is_superuser and self.role != Role.OWNER:
            raise ValidationError({"role": "Superusers must have the owner role."})

    @property
    def full_name(self):
        return self.get_full_name()

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    @property
    def is_owner(self):
        return self.role == Role.OWNER

    @property
    def is_manager(self):
        return self.role == Role.MANAGER

    @property
    def is_platform_staff(self):
        return self.role == Role.STAFF
