"""Platform-wide constants.

Money is always handled as ``Decimal`` in NGN. Paystack speaks kobo, but that
conversion is confined to the payments app.
"""

CURRENCY_CODE = "NGN"
CURRENCY_SYMBOL = "₦"

#: Maximum digits for a money column, e.g. 9_999_999_999.99 NGN.
MONEY_MAX_DIGITS = 12

#: Number of decimal places used for every monetary value.
MONEY_DECIMAL_PLACES = 2

#: Largest value a PostgreSQL ``integer`` column (and Django's
#: ``PositiveIntegerField``) can hold. Serializer and model validators use this
#: so an oversized request returns ``400`` instead of failing at the database.
MAX_DATABASE_INTEGER = 2_147_483_647

MYSTUDIO_TIME_ZONE = "Africa/Lagos"