"""Authentication and profile endpoints."""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

from apps.accounts import services
from apps.accounts.api.serializers import (
    AuthResponseSerializer,
    ChangePasswordSerializer,
    LogoutSerializer,
    MyStudioTokenObtainPairSerializer,
    ProfileUpdateSerializer,
    UserSerializer,
)

logger = logging.getLogger("mystudio.auth")


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


class LoginAPIView(TokenObtainPairView):
    """Obtain a JWT pair with email and password."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "auth-login"
    serializer_class = MyStudioTokenObtainPairSerializer

    @extend_schema(
        tags=["auth"],
        summary="Log in",
        request=MyStudioTokenObtainPairSerializer,
        responses={200: AuthResponseSerializer},
        auth=[],
    )
    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            # Never log the submitted email or password.
            logger.warning("Failed login attempt from %s", _client_ip(request))
            raise


class RefreshTokenAPIView(TokenRefreshView):
    """Exchange a refresh token for a new access token."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "auth-refresh"

    @extend_schema(tags=["auth"], summary="Refresh an access token", auth=[])
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class VerifyTokenAPIView(TokenVerifyView):
    """Check whether a token is still valid."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "auth-verify"

    @extend_schema(tags=["auth"], summary="Verify a token", auth=[])
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class LogoutAPIView(APIView):
    """Blacklist a refresh token so it can no longer be used."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        tags=["auth"],
        summary="Log out",
        request=LogoutSerializer,
        responses={204: None},
    )
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.blacklist_refresh_token(
            serializer.validated_data["refresh"], user=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeAPIView(APIView):
    """Read or update the authenticated user's profile."""

    permission_classes = (IsAuthenticated,)
    http_method_names = ["get", "patch", "head", "options"]

    @extend_schema(tags=["auth"], summary="Get the current user", responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    @extend_schema(
        tags=["auth"],
        summary="Update the current user's profile",
        request=ProfileUpdateSerializer,
        responses=UserSerializer,
    )
    def patch(self, request):
        serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = services.update_profile(user=request.user, **serializer.validated_data)
        return Response(UserSerializer(user).data)


class ChangePasswordAPIView(APIView):
    """Change the authenticated user's password."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        tags=["auth"],
        summary="Change password",
        request=ChangePasswordSerializer,
        responses={204: None},
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.change_password(user=request.user, **serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)
