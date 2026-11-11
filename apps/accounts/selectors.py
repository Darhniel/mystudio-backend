"""Read helpers for the accounts app."""

from django.contrib.auth import get_user_model

User = get_user_model()


def get_user_by_email(email: str):
    return User.objects.filter(email__iexact=email).first()


def get_active_user_by_id(user_id):
    return User.objects.filter(pk=user_id, is_active=True).first()
