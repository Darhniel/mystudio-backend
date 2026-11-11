"""Shared pytest fixtures."""

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from unittest.mock import patch
from apps.accounts.models import User, Role
from apps.studios.models import Studio
from apps.equipment.models import Equipment


@pytest.fixture(autouse=True)
def mock_cloudinary_operations():
    """
    Automatically mock Cloudinary uploads and deletions during pytest runs.
    Prevents network calls and ensures tests run offline.
    """
    with patch(
        "apps.common.cloudinary.cloudinary.uploader.upload"
    ) as mock_upload, patch(
        "apps.common.cloudinary.cloudinary.uploader.destroy"
    ) as mock_destroy:

        mock_upload.return_value = {
            "secure_url": "https://res.cloudinary.com/test/image/upload/v1/mystudio_test.jpg",
            "public_id": "mystudio/test/public_id",
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


@pytest.fixture
def booking_admin_user(db):
    return User.objects.create_superuser(
        email="admin@mystudio.ng",
        password="password123",
        role=Role.OWNER,
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def studio(db):
    return Studio.objects.create(
        name="Test Studio",
        slug="test-studio",
        description="A great test studio",
        hourly_rate_kobo=500000,  # 5,000 NGN
        capacity=10,
    )


@pytest.fixture
def equipment(db):
    return Equipment.objects.create(
        name="Test Light",
        description="A bright light",
        fixed_price_kobo=100000,  # 1,000 NGN
        stock_quantity=5,
    )
