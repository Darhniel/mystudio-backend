"""Behaviour of the operational endpoints and the JSON error contract."""

from unittest import mock

import pytest
from django.db import OperationalError
from django.test import override_settings

from apps.accounts.tests.factories import make_admin
from apps.common.api.handlers import not_found, server_error

pytestmark = pytest.mark.django_db

API_ROOT_URL = "/api/v1/"
HEALTH_URL = "/api/v1/health/"


def test_api_root_is_public_json(api_client):
    response = api_client.get(API_ROOT_URL)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    assert response.data["version"]
    assert response.data["auth"]["login"].endswith("/api/v1/auth/login/")
    assert response.data["studios"].endswith("/api/v1/studios/")
    assert response.data["equipment"].endswith("/api/v1/equipment/")


def test_unknown_api_route_returns_json_404(api_client):
    response = api_client.get("/api/v1/does-not-exist/")

    assert response.status_code == 404
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == {"detail": "Not found."}


def test_non_api_404_still_uses_the_html_page(rf):
    response = not_found(rf.get("/missing-page/"))

    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")


def test_api_500_handler_returns_json(rf):
    response = server_error(rf.get("/api/v1/health/"))

    assert response.status_code == 500
    assert response["Content-Type"].startswith("application/json")


def test_health_reports_503_when_the_database_is_down(api_client):
    with mock.patch(
        "apps.common.api.views.connection.cursor",
        side_effect=OperationalError("database is down"),
    ):
        response = api_client.get(HEALTH_URL)

    assert response.status_code == 503
    assert response.data["status"] == "unhealthy"
    assert response.data["checks"]["database"] == "error"


def test_api_docs_are_public_while_configured(api_client):
    response = api_client.get("/api/schema/")

    assert response.status_code == 200


@override_settings(API_DOCS_PUBLIC=False)
def test_api_docs_reject_anonymous_users_when_not_public(api_client):
    response = api_client.get("/api/schema/")

    assert response.status_code == 401


@override_settings(API_DOCS_PUBLIC=False)
def test_api_docs_allow_staff_users_when_not_public(api_client):
    from rest_framework_simplejwt.tokens import RefreshToken

    staff = make_admin(email="docs-admin@example.com")
    access = RefreshToken.for_user(staff).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    assert api_client.get("/api/schema/").status_code == 200
    assert api_client.get("/api/docs/").status_code == 200
    assert api_client.get("/api/redoc/").status_code == 200


@override_settings(
    CORS_ALLOW_ALL_ORIGINS=False,
    CORS_ALLOWED_ORIGINS=["https://mystudio.ng"],
)
def test_cors_allows_the_configured_origin(api_client):
    response = api_client.get(HEALTH_URL, HTTP_ORIGIN="https://mystudio.ng")

    assert response["Access-Control-Allow-Origin"] == "https://mystudio.ng"


@override_settings(
    CORS_ALLOW_ALL_ORIGINS=False,
    CORS_ALLOWED_ORIGINS=["https://mystudio.ng"],
)
def test_cors_rejects_an_unknown_origin(api_client):
    response = api_client.get(HEALTH_URL, HTTP_ORIGIN="https://evil.example")

    assert "Access-Control-Allow-Origin" not in response


def test_cors_credentials_are_disabled():
    """Auth uses a bearer header, so browsers must not send cookies."""
    from django.conf import settings

    assert settings.CORS_ALLOW_CREDENTIALS is False
