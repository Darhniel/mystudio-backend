"""Project-wide DRF exception handling.

DRF already produces sensible error payloads. This handler adds two things:

* Django's ``ValidationError`` (raised by services and model validation) is
  converted into a DRF ``ValidationError`` so business-rule failures return
  ``400`` instead of ``500``.
* Truly unexpected exceptions are logged with context and, outside DEBUG,
  replaced with a generic message so internals are never leaked to clients.
"""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.common.cloudinary import ImageHostingNotConfigured

logger = logging.getLogger(__name__)


def _django_validation_detail(exc: DjangoValidationError):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    if hasattr(exc, "error_list"):
        return {"non_field_errors": exc.messages}
    return {"detail": exc.messages[0] if exc.messages else str(exc)}


class ImageHostingUnavailable(APIException):
    """The deployment has no Cloudinary credentials."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Image hosting is not configured."
    default_code = "image_hosting_unavailable"


class ImageServiceError(APIException):
    """Cloudinary could not complete an upload or deletion."""

    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = (
        "The image service could not complete the request. Please try again."
    )
    default_code = "image_service_error"


def image_service_error(exc):
    """Translate a low-level image service failure into an API exception.

    Provider internals and credentials never reach the response body.
    """

    if isinstance(exc, ImageHostingNotConfigured):
        return ImageHostingUnavailable()
    return ImageServiceError()


def api_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = DRFValidationError(detail=_django_validation_detail(exc))

    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    view = context.get("view")
    logger.exception(
        "Unhandled API exception in %s",
        view.__class__.__name__ if view else "unknown view",
        exc_info=exc,
    )

    if settings.DEBUG:
        raise exc

    return Response(
        {"detail": "An unexpected error occurred. Please try again later."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
