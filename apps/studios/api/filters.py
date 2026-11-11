"""Filters for the public studio catalogue."""

import django_filters

from apps.studios.models import Studio


class StudioFilter(django_filters.FilterSet):
    """Range filters that are useful when browsing studios."""

    capacity_min = django_filters.NumberFilter(
        field_name="capacity",
        lookup_expr="gte",
    )
    capacity_max = django_filters.NumberFilter(
        field_name="capacity",
        lookup_expr="lte",
    )
    hourly_rate_min = django_filters.NumberFilter(
        field_name="hourly_rate_kobo",
        lookup_expr="gte",
    )
    hourly_rate_max = django_filters.NumberFilter(
        field_name="hourly_rate_kobo",
        lookup_expr="lte",
    )

    class Meta:
        model = Studio
        fields = (
            "capacity_min",
            "capacity_max",
            "hourly_rate_min",
            "hourly_rate_max",
        )


class StudioAdminFilter(StudioFilter):
    """Public range filters plus the administrative active-state filter."""

    is_active = django_filters.BooleanFilter(field_name="is_active")

    class Meta(StudioFilter.Meta):
        fields = StudioFilter.Meta.fields + ("is_active",)
