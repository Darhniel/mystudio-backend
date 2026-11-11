"""Shared pytest fixtures."""

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from unittest.mock import patch

@pytest.fixture(autouse=True)
def mock_cloudinary_operations():
    """
    Automatically mock Cloudinary uploads and deletions during pytest runs.
    Prevents network calls and ensures tests run offline.
    """
    with patch("apps.common.cloudinary.cloudinary.uploader.upload") as mock_upload, \
         patch("apps.common.cloudinary.cloudinary.uploader.destroy") as mock_destroy:
        
        mock_upload.return_value = {
            "secure_url": "https://res.cloudinary.com/test/image/upload/v1/mystudio_test.jpg",
            "public_id": "mystudio/test/public_id"
        }
        mock_destroy.return_value = {"result": "ok"}
        yield


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
