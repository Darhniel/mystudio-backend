"""API views for payments."""

import logging

from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiTypes,
    extend_schema_view,
)

from apps.accounts.api.permissions import IsAdministrativeStaff
from apps.payments.api.serializers import (
    AdminPaymentSerializer,
    PaymentInitializeSerializer,
    PaymentStatusSerializer,
    PaymentVerifySerializer,
)
from apps.payments.models import Payment
from apps.payments.services import process_paystack_webhook
from apps.payments import webhook_utils

logger = logging.getLogger("mystudio.payments")


@extend_schema(tags=["Payments - Public"])
class PaymentInitializeAPIView(generics.CreateAPIView):
    """Initialize a Paystack transaction for a pending booking."""

    permission_classes = [AllowAny]
    serializer_class = PaymentInitializeSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(result, status=status.HTTP_200_OK)


@extend_schema(tags=["Payments - Public"])
class PaymentVerifyAPIView(generics.CreateAPIView):
    """Verify a Paystack transaction and confirm the booking if eligible."""

    permission_classes = [AllowAny]
    serializer_class = PaymentVerifySerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = serializer.save()
            return Response(result, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response(e.message_dict, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Payments - Public"],
    parameters=[
        OpenApiParameter(
            name="booking_access_token",
            description="Secure access token for the booking",
            required=True,
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
        )
    ],
)
class PaymentStatusAPIView(generics.RetrieveAPIView):
    """Retrieve the status of a payment and its associated booking."""

    permission_classes = [AllowAny]
    serializer_class = PaymentStatusSerializer
    lookup_field = "paystack_reference"
    lookup_url_kwarg = "reference"

    def get_queryset(self):
        return Payment.objects.select_related("booking")

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        token = request.query_params.get("booking_access_token")

        if not token or not __import__("secrets").compare_digest(
            token, instance.booking.access_token
        ):
            return Response(
                {"detail": "Invalid or missing access token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(instance)
        return Response(serializer.data)


@method_decorator(csrf_exempt, name="dispatch")
@extend_schema(
    tags=["Payments - Webhook"],
    description="Paystack webhook endpoint. Secured via Paystack-Signature header.",
    request=OpenApiTypes.OBJECT,
    responses={
        200: OpenApiTypes.OBJECT,
        401: OpenApiTypes.OBJECT,
        500: OpenApiTypes.OBJECT,
    },
)
class PaystackWebhookAPIView(generics.GenericAPIView):
    """Handle Paystack webhook events."""

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        raw_body = request.body
        signature_header = request.headers.get("Paystack-Signature")
        legacy_signature_header = request.headers.get("x-paystack-signature")

        try:
            result = process_paystack_webhook(
                raw_body=raw_body,
                signature_header=signature_header,
                legacy_signature_header=legacy_signature_header,
            )
            return Response(result, status=status.HTTP_200_OK)
        except webhook_utils.WebhookSignatureError as e:
            logger.warning(f"Webhook signature error: {e}")
            return Response(
                {"detail": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED
            )
        except ValueError as e:
            logger.error(f"Webhook temporary processing error: {e}")
            return Response(
                {"detail": "Processing failed, Paystack will retry"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as e:
            logger.exception(f"Unexpected webhook error: {e}")
            return Response(
                {"detail": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@extend_schema(tags=["Payments - Admin"])
class AdminPaymentListAPIView(generics.ListAPIView):
    """List all payments with filtering and search capabilities."""

    permission_classes = [IsAuthenticated, IsAdministrativeStaff]
    serializer_class = AdminPaymentSerializer
    queryset = Payment.objects.select_related("booking").order_by("-created_at")
    filterset_fields = [
        "status",
        "booking__reference",
        "created_at",
        "paystack_reference",
    ]
    search_fields = [
        "paystack_reference",
        "booking__reference",
        "booking__customer_email",
    ]
    ordering_fields = ["created_at", "paid_at", "amount_kobo"]


@extend_schema(tags=["Payments - Admin"])
class AdminPaymentDetailAPIView(generics.RetrieveAPIView):
    """Retrieve a single payment by ID."""

    permission_classes = [IsAuthenticated, IsAdministrativeStaff]
    serializer_class = AdminPaymentSerializer
    queryset = Payment.objects.select_related("booking")
    lookup_field = "id"
    lookup_url_kwarg = "payment_id"
