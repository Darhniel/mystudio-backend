"""Read-side queries for studios.

Selectors return querysets and never write. Views apply 404 handling and
pagination; keeping the queryset here means future workers and services reuse
the same public visibility rule.
"""

from apps.studios.models import Studio


def active_studios():
    """Active studios with their images ready for list/detail responses."""

    return (
        Studio.objects.filter(is_active=True)
        .prefetch_related("images")
    )
