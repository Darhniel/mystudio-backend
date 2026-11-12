"""URL routes for Payments, mounted under /api/v1/payments/."""

from django.urls import path

from apps.payments.api.views import (
    AdminPaymentDetailAPIView,
    AdminPaymentListAPIView,
    PaystackWebhookAPIView,
    PaymentInitializeAPIView,
    PaymentStatusAPIView,
    PaymentVerifyAPIView,
)

app_name = "payments"

urlpatterns = [
    # Public endpoints
    path("initialize/", PaymentInitializeAPIView.as_view(), name="payment-initialize"),
    path("verify/", PaymentVerifyAPIView.as_view(), name="payment-verify"),
    path(
        "<str:reference>/status/",
        PaymentStatusAPIView.as_view(),
        name="payment-status",
    ),
    # Webhook endpoint
    path("webhook/", PaystackWebhookAPIView.as_view(), name="paystack-webhook"),
    # Administrative endpoints
    path("admin/", AdminPaymentListAPIView.as_view(), name="admin-payment-list"),
    path(
        "admin/<uuid:payment_id>/",
        AdminPaymentDetailAPIView.as_view(),
        name="admin-payment-detail",
    ),
]
