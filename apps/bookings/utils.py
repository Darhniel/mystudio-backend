"""Helpers for the bookings app.

Only identifier generation and timezone conversion live here, mirroring
``apps.studios.utils``. Booking rules -- operating hours, availability, pricing
and payment holds -- belong in ``services.py``.
"""

import secrets
from datetime import datetime, time

from django.utils import timezone

from apps.common.constants import MYSTUDIO_TIME_ZONE

# ---------------------------------------------------------------------------
# Booking reference generation
# ---------------------------------------------------------------------------

#: Public, human-quotable booking identifier, e.g. ``MS-7KQ2XR9B4T``.
BOOKING_REFERENCE_PREFIX = "MS"

#: Crockford-style alphabet: no I/O/0/1, so a reference read aloud over the
#: phone cannot be mis-transcribed.
BOOKING_REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: 32 ** 10 ~= 1.1e15 combinations. Collisions are still possible, so the model
#: retries inside a savepoint instead of trusting uniqueness.
BOOKING_REFERENCE_RANDOM_LENGTH = 10

#: Column width. ``MS-`` + 10 characters is 13; the headroom lets the prefix
#: change later without a migration.
BOOKING_REFERENCE_MAX_LENGTH = 20

#: Bounded retry count for concurrent inserts that pick the same reference.
REFERENCE_GENERATION_ATTEMPTS = 5


def generate_booking_reference() -> str:
    """Return a fresh, unguessable booking reference.

    ``secrets`` rather than ``random``: the reference is the only handle a guest
    has on their booking, so it must not be predictable from previous ones.
    """

    suffix = "".join(
        secrets.choice(BOOKING_REFERENCE_ALPHABET)
        for _ in range(BOOKING_REFERENCE_RANDOM_LENGTH)
    )
    return f"{BOOKING_REFERENCE_PREFIX}-{suffix}"


def is_reference_conflict(exc) -> bool:
    """True when an ``IntegrityError`` was caused by the reference uniqueness.

    Deliberately message-based so it behaves identically on PostgreSQL
    ("duplicate key value violates unique constraint ...reference...") and on
    the SQLite fallback used by the test suite ("UNIQUE constraint failed:
    bookings_booking.reference"). Every other integrity failure -- a check
    constraint, a protected foreign key -- must propagate untouched.
    """

    message = str(exc).lower()
    if "reference" not in message:
        return False
    return "unique" in message or "duplicate key" in message


# ---------------------------------------------------------------------------
# Timezone conversion
# ---------------------------------------------------------------------------


def make_aware_datetime(date, hour: int, minute: int = 0) -> datetime:
    """Convert a local date and time into a timezone-aware datetime.

    The platform operates in Africa/Lagos. This helper ensures every datetime
    used in availability checks and booking creation is timezone-aware, so
    comparisons with database timestamps (stored in UTC) are correct.

    Args:
        date: A date object or string in YYYY-MM-DD format.
        hour: Hour (0-23).
        minute: Minute (0-59), defaults to 0.

    Returns:
        A timezone-aware datetime in Africa/Lagos.

    Raises:
        ValueError: If the date string is malformed or hour/minute are invalid.
    """
    if isinstance(date, str):
        try:
            date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Invalid date format: {date}. Use YYYY-MM-DD.")

    if not (0 <= hour <= 23):
        raise ValueError(f"Hour must be 0-23, got {hour}")
    if not (0 <= minute <= 59):
        raise ValueError(f"Minute must be 0-59, got {minute}")

    local_time = time(hour, minute)
    local_dt = datetime.combine(date, local_time)

    # Get the Africa/Lagos timezone
    try:
        import zoneinfo

        tz = zoneinfo.ZoneInfo(MYSTUDIO_TIME_ZONE)
    except ImportError:
        # Fallback for Python < 3.9
        from django.utils.timezone import get_fixed_timezone

        # Africa/Lagos is WAT (UTC+1), no DST
        tz = get_fixed_timezone(60)

    return timezone.make_aware(local_dt, tz)
