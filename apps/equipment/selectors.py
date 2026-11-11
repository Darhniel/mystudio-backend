"""Read-side queries for equipment.

Only active catalogue items are public. Stock and booking availability are not
resolved here; that belongs to the bookings milestone.
"""

from apps.equipment.models import Equipment


def active_equipment():
    """Equipment that customers may currently see."""

    return Equipment.objects.filter(is_active=True)
