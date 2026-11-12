"""Paystack API client.

Communicates directly with the Paystack REST API using standard ``requests``.
Includes strict timeout handling, API error parsing, and safe logging to prevent
credential leakage.
"""

import logging

from django.contrib.sites import requests

from django.conf import settings

logger = logging.getLogger("mystudio.payments")

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackAPIError(Exception):
    """Custom exception for Paystack API failures."""

    pass


def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def initialize_transaction(
    *, amount_kobo: int, email: str, reference: str, callback_url: str
) -> dict:
    """Initialize a transaction and return the authorization payload."""
    url = f"{PAYSTACK_BASE_URL}/transaction/initialize"
    payload = {
        "amount": amount_kobo,
        "email": email,
        "reference": reference,
        "currency": "NGN",
        "callback_url": callback_url,
    }

    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data.get("status"):
            error_msg = data.get("message", "Paystack initialization failed")
            logger.error(f"Paystack initialization failed: {error_msg}")
            raise PaystackAPIError(error_msg)

        return data["data"]

    except requests.exceptions.Timeout:
        logger.error("Paystack initialization timed out")
        raise PaystackAPIError("Payment gateway timeout")
    except requests.exceptions.HTTPError as e:
        logger.error(
            f"Paystack HTTP error: {e.response.status_code} - {e.response.text}"
        )
        raise PaystackAPIError("Payment gateway communication error")
    except requests.exceptions.RequestException as e:
        logger.error(f"Paystack request failed: {e}")
        raise PaystackAPIError("Payment gateway communication error")
    except ValueError:
        logger.error("Paystack returned invalid JSON response")
        raise PaystackAPIError("Invalid response from payment gateway")


def verify_transaction(*, reference: str) -> dict:
    """Verify a transaction by its reference."""
    url = f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}"

    try:
        response = requests.get(url, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data.get("status"):
            error_msg = data.get("message", "Paystack verification failed")
            logger.error(f"Paystack verification failed: {error_msg}")
            raise PaystackAPIError(error_msg)

        return data["data"]

    except requests.exceptions.Timeout:
        logger.error("Paystack verification timed out")
        raise PaystackAPIError("Payment gateway timeout")
    except requests.exceptions.HTTPError as e:
        logger.error(
            f"Paystack HTTP error: {e.response.status_code} - {e.response.text}"
        )
        raise PaystackAPIError("Payment gateway communication error")
    except requests.exceptions.RequestException as e:
        logger.error(f"Paystack request failed: {e}")
        raise PaystackAPIError("Payment gateway communication error")
    except ValueError:
        logger.error("Paystack returned invalid JSON response")
        raise PaystackAPIError("Invalid response from payment gateway")
