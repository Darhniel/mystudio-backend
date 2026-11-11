import pytest

from apps.accounts.models import Role
from apps.accounts.tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/v1/auth/register/"
LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"
VERIFY_URL = "/api/v1/auth/verify/"
LOGOUT_URL = "/api/v1/auth/logout/"
ME_URL = "/api/v1/auth/me/"
CHANGE_PASSWORD_URL = "/api/v1/auth/change-password/"

STRONG_PASSWORD = "Str0ng-Passw0rd!2026"


def test_public_registration_endpoint_does_not_exist(api_client):
    """Customers book as guests, so there is no public sign-up route."""
    response = api_client.post(
        REGISTER_URL,
        {"email": "customer@example.com", "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 404


def test_login_returns_token_pair_and_user(api_client, user):
    response = api_client.post(
        LOGIN_URL,
        {"email": user.email, "password": DEFAULT_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["user"]["email"] == user.email
    assert response.data["user"]["role"] == Role.STAFF
    assert response.data["access"]
    assert response.data["refresh"]
    assert "password" not in response.data["user"]


def test_login_rejects_unknown_email(api_client):
    response = api_client.post(
        LOGIN_URL,
        {"email": "nobody@example.com", "password": DEFAULT_PASSWORD},
        format="json",
    )

    assert response.status_code == 401
    assert "password" not in response.data


def test_login_rejects_missing_credentials(api_client):
    response = api_client.post(LOGIN_URL, {}, format="json")

    assert response.status_code == 400
    assert "email" in response.data
    assert "password" in response.data


def test_login_with_wrong_password_fails(api_client, user):
    response = api_client.post(
        LOGIN_URL,
        {"email": user.email, "password": "not-the-password"},
        format="json",
    )

    assert response.status_code == 401


def test_me_requires_authentication(api_client):
    assert api_client.get(ME_URL).status_code == 401


def test_me_returns_current_user(auth_client, user):
    response = auth_client.get(ME_URL)

    assert response.status_code == 200
    assert response.data["email"] == user.email


def test_profile_update_changes_allowed_fields_only(auth_client, user):
    response = auth_client.patch(
        ME_URL,
        {"first_name": "Updated", "role": Role.OWNER},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["first_name"] == "Updated"
    assert response.data["role"] == Role.STAFF

    user.refresh_from_db()
    assert user.role == Role.STAFF


def test_change_password_updates_login_credentials(api_client, user):
    api_client.force_authenticate(user=user)

    response = api_client.post(
        CHANGE_PASSWORD_URL,
        {"current_password": DEFAULT_PASSWORD, "new_password": "A-Brand-New-Pass!42"},
        format="json",
    )
    assert response.status_code == 204

    api_client.force_authenticate(user=None)
    old_login = api_client.post(
        LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD}, format="json"
    )
    new_login = api_client.post(
        LOGIN_URL, {"email": user.email, "password": "A-Brand-New-Pass!42"}, format="json"
    )

    assert old_login.status_code == 401
    assert new_login.status_code == 200


def test_change_password_requires_correct_current_password(auth_client):
    response = auth_client.post(
        CHANGE_PASSWORD_URL,
        {"current_password": "wrong", "new_password": "A-Brand-New-Pass!42"},
        format="json",
    )

    assert response.status_code == 400
    assert "current_password" in response.data


def test_refresh_token_returns_new_access_token(api_client, user):
    login = api_client.post(
        LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD}, format="json"
    )

    response = api_client.post(
        REFRESH_URL, {"refresh": login.data["refresh"]}, format="json"
    )

    assert response.status_code == 200
    assert "access" in response.data


def test_refresh_rotates_and_blacklists_the_old_token(api_client, user):
    login = api_client.post(
        LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD}, format="json"
    )
    old_refresh = login.data["refresh"]

    rotated = api_client.post(REFRESH_URL, {"refresh": old_refresh}, format="json")

    assert rotated.status_code == 200
    assert rotated.data["refresh"]
    assert rotated.data["refresh"] != old_refresh

    reused = api_client.post(REFRESH_URL, {"refresh": old_refresh}, format="json")
    assert reused.status_code == 401


def test_refresh_rejects_an_invalid_token(api_client):
    response = api_client.post(
        REFRESH_URL, {"refresh": "not-a-real-token"}, format="json"
    )

    assert response.status_code == 401


def test_verify_token_validates_access_token(api_client, user):
    login = api_client.post(
        LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD}, format="json"
    )

    response = api_client.post(
        VERIFY_URL, {"token": login.data["access"]}, format="json"
    )

    assert response.status_code == 200


def test_logout_requires_authentication(api_client):
    response = api_client.post(LOGOUT_URL, {"refresh": "anything"}, format="json")

    assert response.status_code == 401


def test_logout_rejects_an_invalid_refresh_token(auth_client):
    response = auth_client.post(
        LOGOUT_URL, {"refresh": "not-a-real-token"}, format="json"
    )

    assert response.status_code == 400


def test_logout_blacklists_refresh_token(api_client, user):
    login = api_client.post(
        LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD}, format="json"
    )
    refresh = login.data["refresh"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    logout = api_client.post(LOGOUT_URL, {"refresh": refresh}, format="json")
    assert logout.status_code == 204

    api_client.credentials()
    response = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
    assert response.status_code == 401


def test_inactive_user_cannot_log_in(api_client):
    user = make_user(email="inactive@example.com", is_active=False)

    response = api_client.post(
        LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD}, format="json"
    )

    assert response.status_code == 401


def test_logout_rejects_another_users_refresh_token(auth_client, user):
    other = make_user(email="other@example.com")
    login = auth_client.post(
        LOGIN_URL, {"email": other.email, "password": DEFAULT_PASSWORD}, format="json"
    )
    other_refresh = login.data["refresh"]

    response = auth_client.post(LOGOUT_URL, {"refresh": other_refresh}, format="json")

    assert response.status_code == 400
    assert "refresh" in response.data

    # The victim's token was not touched.
    auth_client.credentials()
    still_valid = auth_client.post(
        REFRESH_URL, {"refresh": other_refresh}, format="json"
    )
    assert still_valid.status_code == 200


def test_change_password_revokes_existing_refresh_tokens(api_client, user):
    login = api_client.post(
        LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD}, format="json"
    )
    refresh = login.data["refresh"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    changed = api_client.post(
        CHANGE_PASSWORD_URL,
        {"current_password": DEFAULT_PASSWORD, "new_password": "A-Brand-New-Pass!42"},
        format="json",
    )
    assert changed.status_code == 204

    api_client.credentials()
    reused = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
    assert reused.status_code == 401


def test_change_password_rejects_a_weak_password(auth_client):
    response = auth_client.post(
        CHANGE_PASSWORD_URL,
        {"current_password": DEFAULT_PASSWORD, "new_password": "password"},
        format="json",
    )

    assert response.status_code == 400
    assert "non_field_errors" in response.data


def test_failed_login_is_logged_without_the_email(api_client, user, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="mystudio.auth"):
        api_client.post(
            LOGIN_URL,
            {"email": user.email, "password": "not-the-password"},
            format="json",
        )

    assert "Failed login attempt" in caplog.text
    assert user.email not in caplog.text


def test_login_is_rate_limited(api_client, user):
    from unittest import mock

    from django.core.cache import cache
    from rest_framework.throttling import ScopedRateThrottle

    rates = {"auth-login": "2/min"}

    with mock.patch.object(ScopedRateThrottle, "THROTTLE_RATES", rates):
        cache.clear()
        payload = {"email": user.email, "password": "not-the-password"}

        assert api_client.post(LOGIN_URL, payload, format="json").status_code == 401
        assert api_client.post(LOGIN_URL, payload, format="json").status_code == 401
        throttled = api_client.post(LOGIN_URL, payload, format="json")

    assert throttled.status_code == 429
