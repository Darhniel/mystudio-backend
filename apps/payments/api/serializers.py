"""Serializers for payments."""

import secrets

from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.bookings.models import Booking
from apps.payments.models import Payment, PaymentStatus
from apps.payments import services


class PaymentInitializeSerializer(serializers.Serializer):
    """Initialize a Paystack transaction for a pending booking."""

    booking_reference = serializers.CharField(max_length=50)
    booking_access_token = serializers.CharField(max_length=64)

    def validate(self, attrs):
        booking_reference = attrs["booking_reference"]
        access_token = attrs["booking_access_token"]

        try:
            booking = Booking.objects.get(reference=booking_reference)
        except Booking.DoesNotExist:
            raise serializers.ValidationError(
                {"booking_reference": "Invalid booking reference."}
            )

        if not booking.access_token or not secrets.compare_digest(
            booking.access_token, access_token
        ):
            raise serializers.ValidationError(
                {"booking_access_token": "Invalid access token."}
            )

        self.booking = booking
        return attrs

    def create(self, validated_data):
        return services.initialize_payment(
            booking_reference=self.booking.reference,
            access_token=self.booking.access_token,
        )


class PaymentVerifySerializer(serializers.Serializer):
    """Verify a Paystack transaction and confirm the booking if eligible."""

    reference = serializers.CharField(max_length=100)
    booking_access_token = serializers.CharField(max_length=64)

    def validate(self, attrs):
        reference = attrs["reference"]
        access_token = attrs["booking_access_token"]

        try:
            payment = Payment.objects.select_related("booking").get(
                paystack_reference=reference
            )
        except Payment.DoesNotExist:
            raise serializers.ValidationError(
                {"reference": "Invalid payment reference."}
            )

        if not payment.booking.access_token or not secrets.compare_digest(
            payment.booking.access_token, access_token
        ):
            raise serializers.ValidationError(
                {"booking_access_token": "Invalid access token."}
            )

        self.payment = payment
        return attrs

    def create(self, validated_data):
        return services.verify_and_process_payment(
            reference=self.payment.paystack_reference,
            access_token=self.payment.booking.access_token,
        )


class PaymentStatusSerializer(serializers.ModelSerializer):
    """Public-facing payment status, excluding internal gateway details."""

    payment_reference = serializers.CharField(source="paystack_reference")
    payment_status = serializers.CharField(source="status")
    booking_reference = serializers.CharField(source="booking.reference")
    booking_status = serializers.CharField(source="booking.status")

    class Meta:
        model = Payment
        fields = (
            "payment_reference",
            "payment_status",
            "amount_kobo",
            "currency",
            "paid_at",
            "booking_reference",
            "booking_status",
        )
        read_only_fields = fields


class AdminPaymentSerializer(serializers.ModelSerializer):
    """Administrative payment details with booking context."""

    booking_reference = serializers.CharField(source="booking.reference")
    customer_email = serializers.CharField(source="booking.customer_email")

    class Meta:
        model = Payment
        fields = (
            "id",
            "paystack_reference",
            "paystack_transaction_id",
            "amount_kobo",
            "currency",
            "status",
            "payment_channel",
            "initialized_at",
            "paid_at",
            "verified_at",
            "review_reason",
            "created_at",
            "booking_reference",
            "customer_email",
        )
        read_only_fields = fields
