import pytest
from django.core.exceptions import ValidationError

from apps.accounts import services
from apps.accounts.models import Role
from apps.accounts.tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db


def test_change_password_requires_current_password():
    user = make_user()

    with pytest.raises(ValidationError) as exc_info:
        services.change_password(
            user=user,
            current_password="wrong-password",
            new_password="A-Brand-New-Pass!42",
        )

    assert "current_password" in exc_info.value.message_dict


def test_change_password_updates_hash():
    user = make_user()

    services.change_password(
        user=user,
        current_password=DEFAULT_PASSWORD,
        new_password="A-Brand-New-Pass!42",
    )

    user.refresh_from_db()
    assert user.check_password("A-Brand-New-Pass!42")
    assert not user.check_password(DEFAULT_PASSWORD)


def test_expired_token_is_rejected(user):
    from datetime import timedelta

    from django.utils import timezone
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import AccessToken

    token = AccessToken.for_user(user)
    token.set_exp(
        from_time=timezone.now() - timedelta(hours=2),
        lifetime=timedelta(minutes=1),
    )

    with pytest.raises(TokenError):
        AccessToken(str(token))


def test_change_password_revokes_every_refresh_token(user):
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import RefreshToken

    first = str(RefreshToken.for_user(user))
    second = str(RefreshToken.for_user(user))

    services.change_password(
        user=user,
        current_password=DEFAULT_PASSWORD,
        new_password="A-Brand-New-Pass!42",
    )

    for token in (first, second):
        with pytest.raises(TokenError):
            RefreshToken(token)

    assert services.revoke_all_refresh_tokens(user) == 0


def test_blacklist_refresh_token_rejects_garbage(user):
    with pytest.raises(ValidationError):
        services.blacklist_refresh_token("not-a-real-token", user=user)


def test_blacklist_refresh_token_rejects_another_users_token(user):
    from rest_framework_simplejwt.tokens import RefreshToken

    other = make_user(email="other@example.com")
    other_token = str(RefreshToken.for_user(other))

    with pytest.raises(ValidationError) as exc_info:
        services.blacklist_refresh_token(other_token, user=user)

    assert "refresh" in exc_info.value.message_dict
    # The victim's token is still usable.
    assert RefreshToken(other_token).get("user_id") == str(other.pk)


def test_update_profile_rejects_unexpected_fields(user):
    with pytest.raises(ValidationError) as exc_info:
        services.update_profile(user=user, role=Role.OWNER)

    assert "detail" in exc_info.value.message_dict


def test_update_profile_rejects_removed_customer_fields(user):
    with pytest.raises(ValidationError) as exc_info:
        services.update_profile(user=user, phone_number="+2348012345678")

    assert "detail" in exc_info.value.message_dict


def test_update_profile_trims_string_fields(user):
    updated = services.update_profile(
        user=user, first_name="  Ada  ", last_name="  Lovelace  "
    )

    assert updated.first_name == "Ada"
    assert updated.last_name == "Lovelace"
