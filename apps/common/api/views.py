"""Operational endpoints that are not tied to a business domain."""

import logging

from django.conf import settings
from django.db import connection
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger("mystudio.health")


class APIRootAPIView(APIView):
    """Discoverable index of the versioned API surface."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        tags=["meta"],
        summary="API root",
        description="Lists the available API groups. Useful for smoke checks and clients.",
        responses={200: OpenApiTypes.OBJECT},
        auth=[],
    )
    def get(self, request):
        base = request.build_absolute_uri("/api/v1/")
        return Response(
            {
                "version": settings.API_VERSION,
                "auth": {
                    "login": f"{base}auth/login/",
                    "refresh": f"{base}auth/refresh/",
                    "logout": f"{base}auth/logout/",
                },
                "health": f"{base}health/",
                "studios": f"{base}studios/",
                "equipment": f"{base}equipment/",
                "schema": request.build_absolute_uri("/api/schema/"),
                "docs": request.build_absolute_uri("/api/docs/"),
            }
        )


class HealthCheckAPIView(APIView):
    """Liveness/readiness probe used by deployment platforms."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        tags=["health"],
        summary="Service health",
        description="Returns the service status and verifies database connectivity.",
        responses={200: OpenApiTypes.OBJECT, 503: OpenApiTypes.OBJECT},
        auth=[],
    )
    def get(self, request):
        checks = {"database": "ok"}
        healthy = True

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            logger.warning("Health check could not reach the database", exc_info=True)
            checks["database"] = "error"
            healthy = False

        payload = {
            "status": "ok" if healthy else "unhealthy",
            "checks": checks,
            "version": settings.API_VERSION,
            "timestamp": timezone.now().isoformat(),
        }
        return Response(
            payload,
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
