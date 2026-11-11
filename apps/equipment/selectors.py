"""Read-side queries for equipment.

Only active catalogue items are public. Stock and booking availability are not
resolved here; that belongs to the bookings milestone.

Zero-stock equipment (``stock_quantity=0``) is treated as temporarily
out of stock and remains visible in the public catalogue. This allows
customers to see the item and its price, and future bookings can implement
a waitlist or notification feature. If a different behaviour is required
later, this selector is the single place to change.
"""

from apps.equipment.models import Equipment


def active_equipment():
    """Equipment that customers may currently see.

    Includes items with ``stock_quantity=0`` because zero means temporarily
    unavailable, not discontinued. The booking milestone will enforce
    availability when a reservation is attempted.
    """

    return Equipment.objects.filter(
        is_active=True,
        stock_quantity_gt=0,
    ).order_by("name")
