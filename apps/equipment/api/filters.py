"""Filters for the public equipment catalogue."""

import django_filters

from apps.equipment.models import Equipment


class EquipmentFilter(django_filters.FilterSet):
    """Price range filters. Availability is a future bookings concern."""

    fixed_price_min = django_filters.NumberFilter(
        field_name="fixed_price_kobo",
        lookup_expr="gte",
    )
    fixed_price_max = django_filters.NumberFilter(
        field_name="fixed_price_kobo",
        lookup_expr="lte",
    )

    class Meta:
        model = Equipment
        fields = ("fixed_price_min", "fixed_price_max")


class EquipmentAdminFilter(EquipmentFilter):
    """Price range filters plus the administrative active-state filter."""

    is_active = django_filters.BooleanFilter(field_name="is_active")

    class Meta(EquipmentFilter.Meta):
        fields = EquipmentFilter.Meta.fields + ("is_active",)
