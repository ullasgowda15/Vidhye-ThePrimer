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
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts import cookies, export, privacy
from apps.accounts.models import ConsentRecord, DeletionReceipt, User
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
from apps.analytics import events
from apps.analytics.models import Event
from apps.common.renderers import ZipRenderer
from apps.common.throttling import AuthAnonThrottle, AuthUserThrottle, SessionRefreshThrottle
from apps.media import service as media_service
from apps.reading import service as reading_service

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

        # The first step of §22.3's activation funnel, and its denominator: every
        # other onboarding number is a fraction of the accounts counted here, so
        # this is the one emission whose absence would make activation look
        # perfect. No email, no name — an account was created, which is the fact.
        events.track(Event.PARENT_SIGNED_UP, parent=user)

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

    # Every docstring in this module is published as the endpoint's description in
    # `/api/schema/`, so the reasoning below is a comment: it is for whoever
    # changes this class, not for whoever integrates against it, and a note about
    # a vulnerability we used to have does not belong in a public API document.
    #
    # **`AllowAny` is a tightening rather than a loosening.** This view
    # authenticates the *cookie* — it reads it, blacklists what it names, clears
    # it — and never touches `request.user`. Requiring an access token as well
    # meant the credential that actually keeps a session alive for days could only
    # be revoked while a 15-minute one happened to be valid, so a parent whose
    # access token had expired could not sign out at all: the request 401'd and
    # the refresh cookie lived on.
    #
    # Worse, the client answers a 401 with a silent refresh, so that 401 was
    # satisfied *from the very cookie being revoked* — minting a fresh session on
    # the way out. Signing out signed you back in.
    #
    # There is nothing here for an unauthenticated caller to take: without a
    # cookie `post` does nothing at all, and with one they are by definition the
    # holder of that session, which is exactly who may end it. `AuthUserThrottle`
    # is a `UserRateThrottle`, which falls back to per-IP limiting for anonymous
    # callers, so opening this up does not open a hole to hammer.
    permission_classes = [AllowAny]
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
        # `cast` rather than `self.request.user` directly: DRF types the attribute
        # as `AbstractBaseUser | AnonymousUser`, whose `pk` is optional, and this
        # view is `IsAuthenticated` so it is neither anonymous nor unsaved. The
        # re-query is for the `prefetch_related`, which is what keeps `/me` one
        # query for the parent and one for the children.
        parent = cast(User, self.request.user)
        return User.objects.filter(pk=parent.pk).prefetch_related("children").get()


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
        record = serializer.save(
            parent=self.request.user,
            ip=client_ip(self.request),
            # The client does not get to write its own evidence. A user-agent
            # string is weak evidence, but it is *observed* evidence.
            evidence_ref=self.request.META.get("HTTP_USER_AGENT", "")[:255],
        )
        # Funnel step two (§22.3). The consent *type* and nothing about the parent:
        # `ConsentRecord` is the audit trail (§13.7) and this is a counter, and the
        # two must not become two copies of the same evidence.
        events.track(
            Event.PARENT_CONSENT_RECORDED,
            # `IsAuthenticated` has already run, so this is a real parent.
            parent=cast(User, self.request.user),
            consent_type=getattr(record, "consent_type", ""),
            method=getattr(record, "method", ""),
        )


# ---------------------------------------------------------------------------
# Privacy centre (§12.8, §13.7)
# ---------------------------------------------------------------------------
class PrivacyDisclosureView(APIView):
    """The plain-language disclosure (§12.8, §13.8).

    Unauthenticated on purpose. A parent deciding whether to sign up has to be
    able to read what we do with their child's data *before* they have an account,
    and a privacy statement behind a login is a privacy statement written for
    people who have already agreed. `routes/Privacy.tsx` is the public page that
    reads it; `/parent/privacy` renders the same text beside the controls that act
    on real data, and those stay behind the gate.
    """

    # This docstring is the description drf-spectacular publishes at `/api/docs/`,
    # so the reasoning below lives in comments rather than in it. Two of these are
    # load-bearing and read like tidiness, which is exactly how they get reverted.

    permission_classes = [AllowAny]

    #: No credential reaches this view at all. That is what makes the response
    #: incapable of varying by who is asking — and therefore incapable of becoming
    #: a leak when somebody later adds a helpful per-user field to the disclosure.
    #: `TestTheDisclosure::test_it_says_the_same_thing_to_everyone` holds it.
    authentication_classes: list = []

    #: Its own scope, not the shared `anon` bucket. The public page is linked from
    #: the landing footer, so ordinary visitors hit this now; on `anon` (100/day,
    #: keyed by IP) reading the policy would compete with *signup* from the same
    #: address, and one school or NAT'd household could be refused an account for
    #: having read it first. Third time that bucket has needed splitting — see
    #: `session_refresh` and `companion_read` in `settings/base.py`.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "privacy"

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
    # Without `ZipRenderer` this endpoint answers **406** to the only client that
    # calls it. DRF negotiates content in `initial()`, before the handler, and the
    # frontend asks for `application/zip` (`client.ts`, `exportAccount`) — which
    # the default renderers do not offer, so `post()` below never ran. See
    # `apps.common.renderers` for the full account of it.
    #
    # One renderer, not two. Listing `JSONRenderer` alongside it made
    # drf-spectacular advertise a `?format=json` query parameter that cannot do
    # anything — the handler writes its own `HttpResponse`, so every format
    # returns the same zip. A schema that documents a knob with no wire attached
    # is worse than one that says this endpoint produces a zip and nothing else.
    #
    # Errors do not need it either: `finalize_response` below hands them
    # `JSONRenderer` directly, and DRF does not require a renderer to be in this
    # list to use it.
    renderer_classes = [ZipRenderer]

    def finalize_response(self, request: Request, response: Any, *args: Any, **kwargs: Any) -> Any:
        """Serve errors as JSON even though the request negotiated a zip.

        A 401, 403 or 429 here is a DRF `Response` carrying `{"detail": ...}`, and
        by this point negotiation has already chosen `ZipRenderer` for it — so
        without this the body would be JSON while the header claimed
        `application/zip`. Harmless to our own client, which parses the text
        either way, and confusing to everything else.
        """
        if response.status_code >= 400:
            request.accepted_renderer = JSONRenderer()
            request.accepted_media_type = "application/json"
        return super().finalize_response(request, response, *args, **kwargs)

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

    One `User.delete()` for the database. Every table in the product reaches a
    parent either directly or through a child, and every one of those FKs is
    `CASCADE` — which is asserted at the model level rather than trusted
    (`tests/test_privacy.py::test_every_fk_cascades_from_the_account`), so a future
    model that hangs off a child with `PROTECT` fails a test rather than leaving an
    orphan behind a promise.

    **And one media purge per child before it, because the database is not the
    only place a child's data lives.** Their companion's portrait, every
    illustration and every line of recorded narration are objects in storage, and
    the `Media` rows are the only record of where they are. The cascade removes
    those rows; on its own it would therefore leave the objects behind *and*
    destroy the means of ever finding them again. That is what the first version
    of this endpoint did, and the day's own "no orphans" test did not catch it
    because it walks `information_schema` — and an object in a bucket is not a row.
    `tests/test_privacy.py::TestDeletingLeavesNothingInStorage` is the test that
    looks in the right place.

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

        # Object storage first, per child, then the one cascade. The rows are what
        # record where the objects live, so a cascade that ran first would leave
        # every picture and every recorded line in the bucket with nothing left
        # that could ever name them again. `profiles.views.ChildViewSet
        # .perform_destroy` argues this at length; it is the same deletion.
        children = list(parent.children.all())
        media_rows = media_objects = 0
        voice_clips = 0
        for child in children:
            rows, objects = media_service.delete_child_media(child)
            media_rows += rows
            media_objects += objects
            # Consented reading recordings sit on the box's disk, not in the
            # media bucket, so nothing above would have found them and the
            # cascade would have left a deleted family's voices on the machine.
            voice_clips += reading_service.delete_corpus_for(child)

        parent_id, email = parent.pk, parent.email
        parent.delete()
        # After the delete, so a failed delete cannot produce a record claiming a
        # family was erased. A deletion is the one event we may need to prove
        # happened and there is no row left to prove it with — so it is recorded,
        # in `DeletionReceipt`, holding a keyed hash of the address rather than the
        # address.
        #
        # It used to be a log line carrying `email=family@example.com`, which the
        # Day 1-15 audit's F-7 caught: the log stream has no retention, no
        # rotation and no delete path, so the one action a family takes to remove
        # themselves from this product left their address in it permanently. The
        # accountability need was real; the retained address was not part of it.
        receipt = DeletionReceipt.record(
            parent_id=parent_id,
            email=email,
            children=len(children),
            media_rows=media_rows,
            media_objects=media_objects,
        )
        logger.warning(
            "privacy.account_deleted parent=%s receipt=%s children=%d media_rows=%d "
            "media_objects=%d voice_clips=%d",
            parent_id,
            receipt.pk,
            len(children),
            media_rows,
            media_objects,
            voice_clips,
        )

        # The refresh cookie would otherwise sit on the device pointing at an
        # account that no longer exists, and the client would spend a page load
        # discovering that through a 401.
        return cookies.clear(Response(status=status.HTTP_204_NO_CONTENT))
