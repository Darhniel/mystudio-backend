"""Serializers for bookings and blocked slots."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.bookings import services
from apps.bookings.models import BlockedSlot, Booking, BookingEquipment, BookingStatus
from apps.bookings.utils import make_aware_datetime
from apps.equipment.models import Equipment
from apps.studios.models import Studio


class CustomerSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=20)


class EquipmentItemSerializer(serializers.Serializer):
    equipment_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)


class BookingCreateSerializer(serializers.Serializer):
    studio_id = serializers.UUIDField()
    date = serializers.DateField()
    start_time = serializers.CharField(help_text="Time in HH:MM format, e.g., '10:00'")
    duration_hours = serializers.IntegerField(min_value=1, max_value=9)
    customer = CustomerSerializer()
    equipment = serializers.ListField(
        child=EquipmentItemSerializer(), required=False, default=list
    )
    terms_accepted = serializers.BooleanField(
        error_messages={"required": "You must accept the terms to proceed."}
    )

    def validate(self, attrs):
        if not attrs.get("terms_accepted"):
            raise serializers.ValidationError(
                {"terms_accepted": "You must accept the terms to proceed."}
            )

        try:
            self.studio = Studio.objects.get(id=attrs["studio_id"], is_active=True)
        except Studio.DoesNotExist:
            raise serializers.ValidationError(
                {"studio_id": "Invalid or inactive studio."}
            )

        date = attrs["date"]
        start_time_str = attrs["start_time"]
        duration_hours = attrs["duration_hours"]

        try:
            hour, minute = map(int, str(start_time_str).split(":"))
        except ValueError:
            raise serializers.ValidationError(
                {"start_time": "Invalid start time format. Use HH:MM."}
            )

        if minute != 0:
            raise serializers.ValidationError(
                {"start_time": "Start time must be exactly on the hour."}
            )

        try:
            start_time = make_aware_datetime(date, hour, minute)
            end_time = start_time + timedelta(hours=duration_hours)
        except ValueError as e:
            raise serializers.ValidationError({"start_time": str(e)})

        attrs["start_time"] = start_time
        attrs["end_time"] = end_time

        equipment_items = []
        equipment_ids = set()
        for item in attrs.get("equipment", []):
            eq_id = str(item["equipment_id"])
            quantity = item["quantity"]
            if eq_id in equipment_ids:
                raise serializers.ValidationError(
                    {"equipment": "Duplicate equipment items are not allowed."}
                )
            equipment_ids.add(eq_id)

            try:
                Equipment.objects.get(id=eq_id, is_active=True)
            except Equipment.DoesNotExist:
                raise serializers.ValidationError(
                    {"equipment": f"Equipment {eq_id} not found or inactive."}
                )

            equipment_items.append({"id": eq_id, "quantity": quantity})

        self.equipment_items = equipment_items
        return attrs

    def create(self, validated_data):
        customer = validated_data["customer"]
        return services.create_booking(
            studio=self.studio,
            start_time=validated_data["start_time"],
            end_time=validated_data["end_time"],
            equipment_items=self.equipment_items,
            customer_full_name=customer["full_name"],
            customer_email=customer["email"],
            customer_phone=customer["phone"],
            terms_accepted_at=timezone.now(),
        )


class BookingResponseSerializer(serializers.Serializer):
    booking_reference = serializers.CharField(source="reference")
    status = serializers.CharField()
    expires_at = serializers.DateTimeField(source="payment_hold_expires_at")
    duration_hours = serializers.IntegerField()
    studio_amount_kobo = serializers.IntegerField(source="studio_subtotal_kobo")
    equipment_amount_kobo = serializers.IntegerField(source="equipment_subtotal_kobo")
    total_amount_kobo = serializers.IntegerField()
    currency = serializers.CharField(default="NGN")
    payment_required = serializers.SerializerMethodField()

    @extend_schema_field(serializers.BooleanField())
    def get_payment_required(self, obj):
        return obj.status == BookingStatus.PENDING_PAYMENT


class AdminBookingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = (
            "id",
            "reference",
            "studio",
            "customer_full_name",
            "customer_email",
            "customer_phone",
            "start_time",
            "end_time",
            "duration_hours",
            "studio_hourly_price_kobo",
            "studio_subtotal_kobo",
            "equipment_subtotal_kobo",
            "total_amount_kobo",
            "status",
            "payment_hold_expires_at",
            "terms_accepted_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class BlockedSlotSerializer(serializers.ModelSerializer):
    studio_id = serializers.UUIDField(write_only=True)
    studio_name = serializers.CharField(source="studio.name", read_only=True)
    created_by_email = serializers.CharField(
        source="created_by.email", read_only=True, allow_null=True
    )

    class Meta:
        model = BlockedSlot
        fields = (
            "id",
            "studio_id",
            "studio_name",
            "start_time",
            "end_time",
            "reason",
            "created_by_email",
            "created_at",
        )
        read_only_fields = ("id", "studio_name", "created_by_email", "created_at")

    def validate_studio_id(self, value):
        try:
            self.studio = Studio.objects.get(id=value, is_active=True)
        except Studio.DoesNotExist:
            raise serializers.ValidationError("Studio not found or inactive.")
        return value

    def validate(self, attrs):
        if attrs.get("start_time") and attrs.get("end_time"):
            if attrs["start_time"] >= attrs["end_time"]:
                raise serializers.ValidationError(
                    {"end_time": "End time must be after start time."}
                )
        return attrs

    def create(self, validated_data):
        return services.create_blocked_slot(
            studio=self.studio,
            start_time=validated_data["start_time"],
            end_time=validated_data["end_time"],
            reason=validated_data.get("reason", ""),
            created_by=self.context["request"].user,
        )
