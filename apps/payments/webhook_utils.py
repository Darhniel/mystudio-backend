"""Paystack webhook signature verification utilities.

Supports both the current Paystack-Signature format and the legacy
x-paystack-signature format. Never logs signing secrets.
"""

import hashlib
import hmac
import secrets
import time

from django.conf import settings
from django.core.exceptions import ValidationError


class WebhookSignatureError(Exception):
    """Raised when webhook signature validation fails."""

    pass


def verify_current_signature(raw_body: bytes, signature_header: str) -> None:
    """
    Verify the current Paystack-Signature header format.
    Format: t=timestamp,v1=signature[,v1=signature2,...]
    """
    if not signature_header:
        raise WebhookSignatureError("Missing Paystack-Signature header")

    parts = signature_header.split(",")
    timestamp = None
    v1_signatures = []

    for part in parts:
        part = part.strip()
        if part.startswith("t="):
            try:
                timestamp = int(part.split("=")[1])
            except ValueError:
                raise WebhookSignatureError("Invalid timestamp format")
        elif part.startswith("v1="):
            v1_signatures.append(part.split("=")[1])

    if not timestamp or not v1_signatures:
        raise WebhookSignatureError("Invalid signature format")

    # 5-minute tolerance
    if abs(time.time() - timestamp) > 300:
        raise WebhookSignatureError("Expired timestamp")

    payload_to_sign = f"{timestamp}.{raw_body.decode('utf-8')}"
    expected_hash = hmac.new(
        settings.PAYSTACK_WEBHOOK_SECRET.encode("utf-8"),
        payload_to_sign.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    # Constant-time comparison against any provided v1 signature
    if not any(secrets.compare_digest(expected_hash, sig) for sig in v1_signatures):
        raise WebhookSignatureError("Invalid signature")


def verify_legacy_signature(raw_body: bytes, signature_header: str) -> None:
    """
    Verify the legacy x-paystack-signature header format.
    Format: Raw HMAC-SHA512 hash of the request body using the API secret key.
    """
    if not signature_header:
        raise WebhookSignatureError("Missing x-paystack-signature header")

    expected_hash = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    if not secrets.compare_digest(expected_hash, signature_header.strip()):
        raise WebhookSignatureError("Invalid legacy signature")
