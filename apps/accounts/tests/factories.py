"""Helpers for building account fixtures in tests."""

DEFAULT_PASSWORD = "Str0ng-Passw0rd!2026"


def make_user(*, email="ada@example.com", password=DEFAULT_PASSWORD, **kwargs):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(email=email, password=password, **kwargs)


def make_admin(*, email="admin@example.com", password=DEFAULT_PASSWORD, **kwargs):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_superuser(email=email, password=password, **kwargs)
