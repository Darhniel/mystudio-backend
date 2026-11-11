import pytest
from django.contrib.auth import get_user_model

from apps.accounts.tests.factories import DEFAULT_PASSWORD

pytestmark = pytest.mark.django_db


def test_admin_add_user_form_renders(client, admin_user):
    client.force_login(admin_user)

    response = client.get("/admin/accounts/user/add/")

    assert response.status_code == 200
    assert "email" in response.content.decode()


def test_admin_change_user_form_renders(client, admin_user, user):
    client.force_login(admin_user)

    response = client.get(f"/admin/accounts/user/{user.pk}/change/")

    assert response.status_code == 200


def test_admin_can_create_user_with_email(client, admin_user):
    client.force_login(admin_user)

    response = client.post(
        "/admin/accounts/user/add/",
        {
            "email": "created-by-admin@example.com",
            "role": "staff",
            "first_name": "Admin",
            "last_name": "Created",
            "password1": DEFAULT_PASSWORD,
            "password2": DEFAULT_PASSWORD,
            "usable_password": "true",
        },
    )

    assert response.status_code == 302

    created = get_user_model().objects.get(email="created-by-admin@example.com")
    assert created.check_password(DEFAULT_PASSWORD)


def test_admin_login_accepts_mixed_case_email(client, admin_user):
    response = client.post(
        "/admin/login/?next=/admin/",
        {
            "username": admin_user.email.upper(),
            "password": DEFAULT_PASSWORD,
            "next": "/admin/",
        },
    )

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(admin_user.pk)
    assert client.get("/admin/").status_code == 200


def test_admin_cannot_create_an_owner_without_superuser(client, admin_user):
    client.force_login(admin_user)

    response = client.post(
        "/admin/accounts/user/add/",
        {
            "email": "not-a-superuser@example.com",
            "role": "owner",
            "first_name": "Not",
            "last_name": "Owner",
            "password1": DEFAULT_PASSWORD,
            "password2": DEFAULT_PASSWORD,
        },
    )

    assert response.status_code == 200
    assert "role" in response.context["adminform"].form.errors
    assert not get_user_model().objects.filter(
        email="not-a-superuser@example.com"
    ).exists()
