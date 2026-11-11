import pytest

from apps.accounts import selectors
from apps.accounts.tests.factories import make_user

pytestmark = pytest.mark.django_db


def test_get_user_by_email_is_case_insensitive(user):
    found = selectors.get_user_by_email(user.email.upper())

    assert found == user


def test_get_user_by_email_returns_none_when_missing():
    assert selectors.get_user_by_email("nobody@example.com") is None


def test_get_active_user_by_id_hides_inactive_accounts():
    inactive = make_user(email="inactive@example.com", is_active=False)

    assert selectors.get_active_user_by_id(inactive.pk) is None


def test_get_active_user_by_id_returns_active_account(user):
    assert selectors.get_active_user_by_id(user.pk) == user
