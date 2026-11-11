"""Admin forms for the custom user model."""

from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from apps.accounts.models import User


class AdminUserCreationForm(BaseUserCreationForm):
    """Creation form that uses email instead of a username."""

    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ("email", "role", "first_name", "last_name")
        field_classes = {}


class AdminUserChangeForm(UserChangeForm):
    """Change form wired to the custom user model."""

    class Meta(UserChangeForm.Meta):
        model = User
        field_classes = {}
