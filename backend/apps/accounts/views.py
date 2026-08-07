"""Auth, account and consent endpoints (PRODUCT.md §18.3).

Login and refresh are SimpleJWT's views, subclassed only to attach the tighter
`auth` throttle scope and a stable `operation_id` — the token logic itself is
not ours to reimplement.
"""

import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts.models import ConsentRecord, User
from apps.accounts.serializers import (
    ConsentRecordSerializer,
    LogoutSerializer,
    MeSerializer,
    SignupResponseSerializer,
    SignupSerializer,
)
from apps.accounts.verification import send_verification_email
from apps.common.throttling import AuthAnonThrottle, AuthUserThrottle

logger = logging.getLogger(__name__)


def client_ip(request: Request) -> str | None:
    """Best-effort caller IP for the consent audit trail.

    `X-Forwarded-For` is only trustworthy behind our own proxy, which terminates
    TLS and rewrites it; we take the left-most entry there and fall back to the
    socket address. Good enough for evidence, never used for authorization.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def tokens_for(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


class SignupView(generics.CreateAPIView):
    """Create a parent account and sign them straight in."""

    serializer_class = SignupSerializer
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [AuthAnonThrottle]

    @extend_schema(
        operation_id="auth_signup",
        summary="Create a parent account",
        request=SignupSerializer,
        responses={201: SignupResponseSerializer},
        auth=[],
    )
    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Stubbed until an email provider lands: the link goes to the console.
        send_verification_email(user)

        payload = SignupResponseSerializer({"user": user, **tokens_for(user)}).data
        return Response(payload, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    """Exchange email + password for an access/refresh pair.

    `TokenViewBase` already sets `permission_classes = ()` and
    `authentication_classes = ()`, which is what a credential endpoint needs;
    re-stating them here would only invite someone to "tidy" them into the
    project defaults and lock the login page behind a login.
    """

    throttle_classes = [AuthAnonThrottle]

    @extend_schema(operation_id="auth_login", summary="Log in", auth=[])
    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().post(request, *args, **kwargs)


class RefreshView(TokenRefreshView):
    """Rotate a refresh token.

    With `ROTATE_REFRESH_TOKENS` + `BLACKLIST_AFTER_ROTATION` the response
    carries a *new* refresh token and the presented one is blacklisted, so the
    client must replace both halves of the pair, not just the access token.

    Unauthenticated by inheritance, as in `LoginView` — the refresh token *is*
    the credential.
    """

    throttle_classes = [AuthAnonThrottle]

    @extend_schema(operation_id="auth_refresh", summary="Rotate tokens", auth=[])
    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().post(request, *args, **kwargs)


class LogoutView(APIView):
    """Blacklist a refresh token."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthUserThrottle]

    @extend_schema(
        operation_id="auth_logout",
        summary="Log out",
        request=LogoutSerializer,
        responses={204: OpenApiResponse(description="Refresh token blacklisted.")},
    )
    def post(self, request: Request) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            # An already-expired or already-blacklisted token means the client is
            # logged out, which is what it asked for. Reporting an error here would
            # only tempt clients to keep the token around and retry.
            logger.info("Logout presented a token that was already invalid.")
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(generics.RetrieveAPIView):
    """The signed-in parent and their children."""

    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id="me", summary="The signed-in parent")
    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().get(request, *args, **kwargs)

    def get_object(self) -> User:
        return User.objects.filter(pk=self.request.user.pk).prefetch_related("children").get()


class ConsentView(generics.CreateAPIView):
    """Record verifiable parental consent (§13.7).

    Create-only on purpose: consent is an audit trail, so a change of mind is a
    new record (or a revocation), never an edit of the old one.
    """

    serializer_class = ConsentRecordSerializer
    permission_classes = [IsAuthenticated]
    queryset = ConsentRecord.objects.none()

    @extend_schema(
        operation_id="consent_create",
        summary="Record parental consent",
        responses={201: ConsentRecordSerializer},
    )
    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().post(request, *args, **kwargs)

    def perform_create(self, serializer: BaseSerializer) -> None:
        serializer.save(
            parent=self.request.user,
            ip=client_ip(self.request),
            # The client does not get to write its own evidence. A user-agent
            # string is weak evidence, but it is *observed* evidence.
            evidence_ref=self.request.META.get("HTTP_USER_AGENT", "")[:255],
        )
