"""Cross-app foundation checks."""

import pytest
from django.apps import apps
from django.conf import settings
from django.core.management import call_command

BUSINESS_APPS = [
    "common",
    "accounts",
    "studios",
    "equipment",
    "bookings",
    "payments",
    "dashboard",
]


@pytest.mark.parametrize("app_label", BUSINESS_APPS)
def test_business_app_is_installed(app_label):
    assert apps.is_installed(f"apps.{app_label}")

    config = apps.get_app_config(app_label)
    assert config.name == f"apps.{app_label}"
    assert config.verbose_name


def test_timezone_is_lagos():
    assert settings.TIME_ZONE == "Africa/Lagos"
    assert settings.USE_TZ is True


def test_language_is_us_english():
    assert settings.LANGUAGE_CODE == "en-us"


def test_platform_currency_is_naira():
    assert settings.MYSTUDIO_CURRENCY == "NGN"


def test_custom_user_model_is_configured():
    assert settings.AUTH_USER_MODEL == "accounts.User"


def test_jwt_authentication_is_the_default():
    assert (
        settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"][0]
        == "rest_framework_simplejwt.authentication.JWTAuthentication"
    )
    assert (
        settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"][0]
        == "rest_framework.permissions.IsAuthenticated"
    )


def test_no_missing_migrations(db):
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
