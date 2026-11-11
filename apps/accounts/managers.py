"""Manager for the custom user model.

Accounts are staff-only. Superuser creation is the single supported path to a
``is_superuser=True`` account; ordinary creation rejects that flag.
"""

from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    """Create users with an email address instead of a username."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        # Email is the login identifier, so normalise the whole address rather
        # than just the domain (which is BaseUserManager's default).
        return super().normalize_email(email).strip().lower()

    def get_by_natural_key(self, username):
        """Look up by email case-insensitively, as users type mixed case."""
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})

    def _validate_role(self, role):
        if role not in self.model.Role.values:
            valid = ", ".join(self.model.Role.values)
            raise ValueError(f"Invalid role {role!r}. Choose one of: {valid}.")

    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        if not email:
            raise ValueError("Users must have an email address.")

        role = extra_fields.setdefault("role", self.model.Role.STAFF)
        self._validate_role(role)

        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            # Passwordless accounts cannot authenticate until a password is set.
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        """Create an ordinary staff account.

        ``is_staff`` may be granted (admin-site access), but a normal account
        can never be a superuser; use :meth:`create_superuser` for that.
        """
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)

        if extra_fields.get("is_superuser"):
            raise ValueError(
                "Ordinary accounts cannot be superusers. Use create_superuser()."
            )

        if extra_fields.get("role") == self.model.Role.OWNER:
            raise ValueError(
                "The owner role is reserved for superusers. Use create_superuser()."
            )

        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        """Create the platform owner with full Django permissions."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", self.model.Role.OWNER)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superusers must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusers must have is_superuser=True.")
        if extra_fields.get("role") != self.model.Role.OWNER:
            raise ValueError("Superusers must have the owner role.")

        return self._create_user(email, password, **extra_fields)
