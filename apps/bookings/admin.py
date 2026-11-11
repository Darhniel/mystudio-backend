"""Django admin configuration for bookings."""

from django.contrib import admin

from apps.bookings.models import BlockedSlot, Booking, BookingEquipment


class BookingEquipmentInline(admin.TabularInline):
    model = BookingEquipment
    extra = 0
    readonly_fields = ("equipment", "quantity", "unit_price_kobo", "subtotal_kobo")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "studio",
        "customer_email",
        "status",
        "total_amount_kobo",
        "start_time",
        "created_at",
    )
    list_filter = ("status", "studio", "created_at")
    search_fields = ("reference", "customer_email", "customer_full_name")
    readonly_fields = (
        "id",
        "reference",
        "studio_subtotal_kobo",
        "equipment_subtotal_kobo",
        "total_amount_kobo",
        "created_at",
        "updated_at",
    )
    inlines = [BookingEquipmentInline]
    ordering = ("-created_at",)


@admin.register(BlockedSlot)
class BlockedSlotAdmin(admin.ModelAdmin):
    list_display = (
        "studio",
        "start_time",
        "end_time",
        "reason",
        "created_by",
        "created_at",
    )
    list_filter = ("studio", "created_at")
    search_fields = ("reason",)
    readonly_fields = ("id", "created_at")
    ordering = ("-start_time",)
