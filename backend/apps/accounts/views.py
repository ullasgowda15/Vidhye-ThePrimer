"""Auth, account and consent endpoints (PRODUCT.md §18.3).

Login and refresh are SimpleJWT's views, subclassed only to attach the tighter
`auth` throttle scope, a stable `operation_id`, and the refresh cookie — the
token logic itself is not ours to reimplement.

**The refresh token never appears in a response body.** Signup, login and refresh
all set it as an `httpOnly` cookie and return `access` alone; refresh and logout
read it back from that cookie rather than from the request. `apps.accounts.cookies`
owns the attributes and explains why each one is there. The consequence worth
stating here: a client cannot store the refresh token even if it wants to, which
is what makes "an XSS cannot steal the session" a property of the system rather
than a promise about the frontend.
"""

import logging
from typing import Any, cast

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts import cookies, export, privacy
from apps.accounts.models import ConsentRecord, User
from apps.accounts.serializers import (
    AccessTokenSerializer,
    AccountDeleteSerializer,
    ConsentRecordSerializer,
    MeSerializer,
    PrivacyDisclosureSerializer,
    SignupResponseSerializer,
    SignupSerializer,
)
from apps.accounts.verification import send_verification_email
from apps.common.throttling import AuthAnonThrottle, AuthUserThrottle, SessionRefreshThrottle

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

        # `attach` rather than `move_to_cookie`, because the serializer has already
        # dropped `refresh` — it does not declare the field, precisely so that a
        # refresh token cannot reach a client through the signup body.
        tokens = tokens_for(user)
        payload = SignupResponseSerializer({"user": user, **tokens}).data
        response = Response(payload, status=status.HTTP_201_CREATED)
        return cookies.attach(response, tokens["refresh"])


class LoginView(TokenObtainPairView):
    """Exchange email + password for an access token and a refresh cookie.

    `TokenViewBase` already sets `permission_classes = ()` and
    `authentication_classes = ()`, which is what a credential endpoint needs;
    re-stating them here would only invite someone to "tidy" them into the
    project defaults and lock the login page behind a login.
    """

    throttle_classes = [AuthAnonThrottle]

    @extend_schema(
        operation_id="auth_login",
        summary="Log in",
        responses={200: AccessTokenSerializer},
        auth=[],
    )
    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        return cookies.move_to_cookie(super().post(request, *args, **kwargs))


class RefreshView(TokenRefreshView):
    """Rotate the session, using the cookie as the credential.

    With `ROTATE_REFRESH_TOKENS` + `BLACKLIST_AFTER_ROTATION` a *new* refresh
    token is issued and the presented one is blacklisted, so the cookie must be
    replaced on every call — which `move_to_cookie` does, in the same place it
    strips the token from the body.

    The token is read from the cookie and **only** from the cookie: accepting a
    body as well would leave the old, script-readable path open forever, and a
    client that still worked without the cookie is a client that never had to stop
    storing the token.

    Unauthenticated by inheritance, as in `LoginView` — the refresh token *is* the
    credential. `SameSite=Lax` on the cookie is what stops that from being a CSRF
    hole; see `apps.accounts.cookies`.

    **Its own throttle scope, not `auth`.** This endpoint is called once per page
    load, automatically, by every client that has ever been signed in; login is
    called by a human, rarely. While they shared one 30/hour budget, a morning of
    reloads spent it, `LoginView` then answered 429 to a perfectly correct
    password, and — because a failed refresh reads as "your session is over" — the
    client had already signed the parent out and sent them to the form that was
    refusing them. See `SessionRefreshThrottle`.
    """

    throttle_classes = [SessionRefreshThrottle]

    @extend_schema(
        operation_id="auth_refresh",
        summary="Rotate the session using the refresh cookie",
        request=None,
        responses={
            200: AccessTokenSerializer,
            401: OpenApiResponse(description="No refresh cookie, or it is no longer valid."),
        },
        auth=[],
    )
    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        token = cookies.read(request)
        if not token:
            # 401, not 400: "you have no session" is what the client needs to
            # hear, and it is the same answer a dead token gets. A bootstrap
            # refresh on a first-ever visit lands here every time and must not
            # look like a malformed request.
            return Response(
                {"detail": "No refresh cookie was presented."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # The serializer reads `refresh` from the request data, so the cookie is
        # handed to it there rather than by reimplementing rotation.
        serializer = self.get_serializer(data={"refresh": token})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc

        response = Response(serializer.validated_data, status=status.HTTP_200_OK)
        return cookies.move_to_cookie(response)


class LogoutView(APIView):
    """End the session: blacklist the refresh token and delete its cookie."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthUserThrottle]

    @extend_schema(
        operation_id="auth_logout",
        summary="Log out",
        request=None,
        responses={204: OpenApiResponse(description="Session ended and cookie cleared.")},
    )
    def post(self, request: Request) -> Response:
        if token := cookies.read(request):
            try:
                # SimpleJWT's `RefreshToken` takes the encoded string; its stub
                # annotates the parameter as an already-decoded `Token`.
                RefreshToken(cast("Any", token)).blacklist()
            except TokenError:
                # An already-expired or already-blacklisted token means the client
                # is logged out, which is what it asked for. Reporting an error
                # here would only tempt clients to keep the token and retry.
                logger.info("Logout presented a token that was already invalid.")

        # Cleared unconditionally. A logout that leaves the cookie in place
        # because the token was already dead is a logout that does not log out.
        return cookies.clear(Response(status=status.HTTP_204_NO_CONTENT))


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


# ---------------------------------------------------------------------------
# Privacy centre (§12.8, §13.7)
# ---------------------------------------------------------------------------
class PrivacyDisclosureView(APIView):
    """The plain-language disclosure (§12.8, §13.8).

    Unauthenticated on purpose. A parent deciding whether to sign up has to be
    able to read what we do with their child's data *before* they have an account,
    and a privacy statement behind a login is a privacy statement written for
    people who have already agreed.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(
        operation_id="privacy_disclosure",
        summary="What we collect, why, and what we never do (§12.8)",
        responses={200: PrivacyDisclosureSerializer},
        auth=[],
    )
    def get(self, request: Request) -> Response:
        return Response(PrivacyDisclosureSerializer(privacy.disclosure()).data)


class AccountExportView(APIView):
    """`POST /account/export` — every byte this parent owns, as a zip (§12.8).

    **POST rather than GET**, for one reason worth writing down: this is the one
    read in the product that assembles a family's entire history into a single
    artefact, and a GET is a URL — something a browser puts in history, a proxy may
    cache, and a page can be made to fetch cross-site. A POST cannot be triggered
    by navigation and is not cached by anything.

    Built synchronously; see `apps.accounts.export` for why that is right at this
    size and what bounds it.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthUserThrottle]

    @extend_schema(
        operation_id="account_export",
        summary="Download everything we hold about your family (§12.8)",
        description=(
            "A zip of JSON: the account, and for each child their profile and "
            "settings, their companion, every story **verbatim** with what they "
            "chose, every memory, their learning progress, their world map, every "
            "session report, and every safety incident we recorded about them.\n\n"
            "Memory embeddings are omitted — they are machine representations of "
            "the sentences already in the bundle."
        ),
        request=None,
        responses={(200, "application/zip"): OpenApiTypes.BINARY},
    )
    def post(self, request: Request) -> HttpResponse:
        parent = cast(User, request.user)
        payload, filename = export.build_bundle(parent)
        logger.info("privacy.export_downloaded parent=%s bytes=%d", parent.pk, len(payload))

        response = HttpResponse(payload, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = str(len(payload))
        return response


class AccountDeleteView(APIView):
    """`DELETE /account` — the account and every child, gone (§12.8, §13.7).

    One `User.delete()`. Every table in the product reaches a parent either
    directly or through a child, and every one of those FKs is `CASCADE` — which
    is asserted at the model level rather than trusted
    (`tests/test_privacy.py::test_every_fk_cascades_from_the_account`), so a future
    model that hangs off a child with `PROTECT` fails a test rather than leaving an
    orphan behind a promise.

    **Requires the password.** Deletion is the one irreversible action in the
    product, and an access token left on a shared tablet must not be enough to
    erase a family's two years of stories. This is also why it is not merely a
    confirmation dialog: a dialog protects against a mis-tap, and a password
    protects against everyone else.

    No soft delete and no grace period. §13.8 says the data goes; a hidden row
    that still exists is not deleted, and a product whose deletion is a flag is a
    product that will one day be found to have kept everything.
    """

    # No `serializer_class`: this is a plain `APIView`, which never reads one —
    # the body is validated explicitly below and the schema is declared on
    # `@extend_schema`. An attribute nothing consumes would only suggest DRF was
    # doing something here that it is not.
    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthUserThrottle]

    @extend_schema(
        operation_id="account_delete",
        summary="Delete the account and every child's data (§12.8)",
        description=(
            "Irreversible and immediate. Requires the account password in the "
            "body. Export first if you want a copy — afterwards there is nothing "
            "left to export."
        ),
        request=AccountDeleteSerializer,
        responses={204: OpenApiResponse(description="Deleted. The session is over.")},
    )
    def delete(self, request: Request) -> Response:
        parent = cast(User, request.user)
        serializer = AccountDeleteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        children = parent.children.count()
        parent_id, email = parent.pk, parent.email
        parent.delete()
        # After the delete, so a failed delete cannot produce a log line claiming
        # a family was erased. The email is recorded because a deletion is the one
        # event we may need to prove happened, and there is no row left to prove it
        # with.
        logger.warning(
            "privacy.account_deleted parent=%s email=%s children=%d", parent_id, email, children
        )

        # The refresh cookie would otherwise sit on the device pointing at an
        # account that no longer exists, and the client would spend a page load
        # discovering that through a 401.
        return cookies.clear(Response(status=status.HTTP_204_NO_CONTENT))
