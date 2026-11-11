import uuid

import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import Role, User
from apps.accounts.tests.factories import DEFAULT_PASSWORD, make_admin, make_user

pytestmark = pytest.mark.django_db


def test_user_uses_uuid_primary_key():
    user = make_user()

    assert isinstance(user.pk, uuid.UUID)


def test_staff_account_defaults():
    user = make_user()

    assert user.role == Role.STAFF
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.is_active is True
    assert user.date_joined is not None
    assert user.updated_at is not None
    assert user.is_platform_staff
    assert not user.is_owner
    assert not user.is_manager


def test_password_is_hashed():
    user = make_user()

    assert user.password != DEFAULT_PASSWORD
    assert user.check_password(DEFAULT_PASSWORD)


def test_email_is_lowercased_and_trimmed():
    user = make_user(email="  Ada.Lovelace@Example.COM  ")

    assert user.email == "ada.lovelace@example.com"


def test_email_is_normalized_on_every_save():
    user = User(email="  Mixed.Case@Example.COM  ", role=Role.STAFF)
    user.set_password(DEFAULT_PASSWORD)
    user.save()

    assert user.email == "mixed.case@example.com"


def test_full_name_combines_names():
    user = make_user(first_name="Ada", last_name="Lovelace")

    assert user.full_name == "Ada Lovelace"


def test_superuser_gets_owner_role_and_flags():
    user = make_admin(first_name="Platform", last_name="Owner")

    assert user.role == Role.OWNER
    assert user.is_owner
    assert user.is_staff
    assert user.is_superuser
    assert user.is_active
    assert user.full_name == "Platform Owner"


def test_superuser_rejects_a_non_owner_role():
    with pytest.raises(ValueError, match="owner role"):
        make_admin(role=Role.STAFF)


def test_duplicate_email_is_rejected_case_insensitively():
    make_user(email="duplicate@example.com")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_user(email="DUPLICATE@example.com")


def test_email_is_required():
    with pytest.raises(ValueError, match="email address"):
        User.objects.create_user(email="", password=DEFAULT_PASSWORD)


def test_whitespace_only_email_is_required():
    with pytest.raises(ValueError, match="email address"):
        User.objects.create_user(email="   ", password=DEFAULT_PASSWORD)


def test_create_user_rejects_superuser_flag():
    with pytest.raises(ValueError, match="create_superuser"):
        User.objects.create_user(
            email="sneaky@example.com",
            password=DEFAULT_PASSWORD,
            is_superuser=True,
        )


def test_ordinary_staff_may_have_admin_site_access():
    user = make_user(email="manager@example.com", role=Role.MANAGER, is_staff=True)

    assert user.is_staff
    assert user.is_superuser is False
    assert user.role == Role.MANAGER


def test_invalid_role_is_rejected():
    with pytest.raises(ValueError, match="Invalid role"):
        make_user(email="customer@example.com", role="customer")


def test_create_user_rejects_the_owner_role():
    with pytest.raises(ValueError, match="create_superuser"):
        User.objects.create_user(
            email="would-be-owner@example.com",
            password=DEFAULT_PASSWORD,
            role=Role.OWNER,
        )


def test_database_rejects_an_invalid_role():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.create(email="bypass@example.com", role="customer")


def test_full_clean_rejects_owner_without_superuser():
    user = User(email="owner@example.com", role=Role.OWNER, is_superuser=False)
    user.set_password(DEFAULT_PASSWORD)

    with pytest.raises(ValidationError) as exc_info:
        user.full_clean()

    assert "role" in exc_info.value.message_dict


def test_full_clean_rejects_superuser_without_owner_role():
    user = User(email="super@example.com", role=Role.STAFF, is_superuser=True)
    user.set_password(DEFAULT_PASSWORD)

    with pytest.raises(ValidationError) as exc_info:
        user.full_clean()

    assert "role" in exc_info.value.message_dict


def test_full_clean_rejects_an_invalid_role():
    user = User(email="invalid@example.com", role="customer")
    user.set_password(DEFAULT_PASSWORD)

    with pytest.raises(ValidationError) as exc_info:
        user.full_clean()

    assert "role" in exc_info.value.message_dict


def test_full_clean_requires_email():
    user = User(email="", role=Role.STAFF)
    user.set_password(DEFAULT_PASSWORD)

    with pytest.raises(ValidationError) as exc_info:
        user.full_clean()

    assert "email" in exc_info.value.message_dict


def test_authentication_uses_email():
    user = make_user(email="login@example.com")

    assert authenticate(email=user.email, password=DEFAULT_PASSWORD) == user
    assert authenticate(email=user.email.upper(), password=DEFAULT_PASSWORD) == user


def test_authentication_fails_with_wrong_password():
    user = make_user(email="login@example.com")

    assert authenticate(email=user.email, password="not-the-password") is None


def test_inactive_user_cannot_authenticate():
    user = make_user(email="inactive@example.com", is_active=False)

    assert authenticate(email=user.email, password=DEFAULT_PASSWORD) is None
