"""Business logic for accounts.

Views and serializers stay thin: they validate input, call these functions and
serialize the result. Rules that must hold for every caller (API, admin,
management command) live here.
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken


def update_profile(*, user, **fields):
    """Update the profile fields a user is allowed to change themselves."""
    allowed = {"first_name", "last_name"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValidationError(
            {"detail": f"These fields cannot be updated: {', '.join(sorted(unknown))}."}
        )

    for field, value in fields.items():
        setattr(user, field, value.strip() if isinstance(value, str) else value)

    user.save(update_fields=[*fields.keys(), "updated_at"])
    return user


def change_password(*, user, current_password: str, new_password: str):
    """Change a password after verifying the current one.

    Every refresh token issued before the change is blacklisted: a stolen
    refresh token must not outlive a password reset. Access tokens already in
    circulation remain valid until they expire (30 minutes by default).
    """
    if not user.check_password(current_password):
        raise ValidationError({"current_password": "Current password is incorrect."})

    validate_password(new_password, user=user)

    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])
        revoke_all_refresh_tokens(user)

    return user


def revoke_all_refresh_tokens(user) -> int:
    """Blacklist every outstanding refresh token for ``user``.

    Returns the number of tokens that were newly blacklisted.
    """
    revoked = 0
    for token in OutstandingToken.objects.filter(user=user).iterator():
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        revoked += int(created)
    return revoked


def blacklist_refresh_token(token: str, *, user):
    """Invalidate one refresh token (logout) if it belongs to ``user``."""
    try:
        refresh = RefreshToken(token)
    except TokenError as exc:
        raise ValidationError({"refresh": "This token is invalid or has expired."}) from exc

    if str(refresh.get("user_id")) != str(user.pk):
        # Do not confirm whether the token exists or is valid for someone else.
        raise ValidationError({"refresh": "This token does not belong to you."})

    refresh.blacklist()
