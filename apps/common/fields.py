"""Reusable model fields."""

from django.db import models

from apps.common.constants import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS


class MoneyField(models.DecimalField):
    """A ``DecimalField`` pre-configured for platform money values (NGN).

    Never use ``FloatField`` for money. This field keeps every monetary
    column consistent so migrations and API validation stay predictable.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", MONEY_MAX_DIGITS)
        kwargs.setdefault("decimal_places", MONEY_DECIMAL_PLACES)
        super().__init__(*args, **kwargs)
