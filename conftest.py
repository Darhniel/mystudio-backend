"""Shared pytest fixtures."""

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient


@pytest.fixture(autouse=True)
def _clear_cache():
    """Keep throttle counters from leaking between tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    from apps.accounts.tests.factories import make_user

    return make_user()


@pytest.fixture
def admin_user(db):
    from apps.accounts.tests.factories import make_admin

    return make_admin()


@pytest.fixture
def auth_client(api_client, user):
    """An API client authenticated as ``user`` via a real access token."""
    from rest_framework_simplejwt.tokens import RefreshToken

    access = RefreshToken.for_user(user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api_client
