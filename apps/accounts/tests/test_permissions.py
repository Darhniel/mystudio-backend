from types import SimpleNamespace

import pytest

from apps.accounts.api.permissions import (
    IsManager,
    IsOwner,
    IsOwnerOrManager,
    IsStaffRole,
)
from apps.accounts.models import Role
from apps.accounts.tests.factories import make_user

pytestmark = pytest.mark.django_db


def _request(user):
    return SimpleNamespace(user=user)


def test_owner_permission_allows_owners_only(admin_user, user):
    permission = IsOwner()

    assert permission.has_permission(_request(admin_user), None)
    assert not permission.has_permission(_request(user), None)


def test_manager_permission_requires_the_manager_role(user):
    manager = make_user(email="manager@example.com", role=Role.MANAGER)

    permission = IsManager()

    assert permission.has_permission(_request(manager), None)
    assert not permission.has_permission(_request(user), None)


def test_staff_permission_requires_the_staff_role(admin_user, user):
    permission = IsStaffRole()

    assert permission.has_permission(_request(user), None)
    assert not permission.has_permission(_request(admin_user), None)


def test_owner_or_manager_permission(admin_user, user):
    manager = make_user(email="manager@example.com", role=Role.MANAGER)

    permission = IsOwnerOrManager()

    assert permission.has_permission(_request(admin_user), None)
    assert permission.has_permission(_request(manager), None)
    assert not permission.has_permission(_request(user), None)


def test_anonymous_user_is_denied():
    from django.contrib.auth.models import AnonymousUser

    permission = IsOwner()

    assert not permission.has_permission(_request(AnonymousUser()), None)
