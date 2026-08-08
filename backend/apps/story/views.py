"""Adventure endpoints (PRODUCT.md §18.4, §18.5, §16.4).

    POST /api/v1/children/{id}/adventures           begin *or rejoin* an adventure
    GET  /api/v1/children/{id}/adventures/current   the in-progress one, or 204
    GET  /api/v1/adventures/{id}/stream             SSE: the beats

The three are deliberately different kinds of view.

**The POST is an ordinary DRF view, and it is idempotent.** It is a normal
request/response: check ownership and consent, find or generate the plan, persist,
answer. It returns *before* the first beat is generated so the client can open the
stream immediately and put the companion's thinking animation on screen — the wait
is the same either way, and this way the child is looking at something during it
(§14.5).

"Idempotent" here means: while a child has an `in_progress` adventure, this
endpoint returns **that** adventure (200) rather than starting a second one (201).
Without it, a client that lost the adventure id — a hard refresh does exactly that,
since the client's cache is memory-only — began a whole new story, and the backend's
replay path was never reachable in practice. `GET …/adventures/current` is the same
answer without the possibility of a write, and is what the child surface asks on
mount.

**The stream is a plain async Django view.** DRF has no async handler support, and
this endpoint's whole job is to not buffer, so it authenticates by hand and yields
`StreamingHttpResponse` frames from an async generator. It requires ASGI: under
WSGI the generator is drained into one blob before anything is sent, which is not
a degraded stream but a different product (§16.4). There is no fallback for that
reason.

**Why the stream is not an `EventSource`.** `EventSource` cannot set an
`Authorization` header, and the alternatives are both worse than a fetch-based
client: a token in the query string lands in every access log and proxy cache, and
a session cookie on a GET reintroduces the CSRF surface the auth design just
closed. The client reads the stream with `fetch` + a bearer header
(`frontend/src/lib/sse.ts`).
"""

from __future__ import annotations

import logging
from typing import Any, cast

from asgiref.sync import sync_to_async
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import APIException, AuthenticationFailed
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.accounts.consent import require_consent
from apps.accounts.models import User
from apps.common.permissions import IsParentOwner
from apps.companion.models import Companion
from apps.profiles.models import Child
from apps.story import orchestrator, streaming
from apps.story.models import Adventure
from apps.story.serializers import (
    AdventureStartRequestSerializer,
    AdventureStartSerializer,
    start_payload,
)

logger = logging.getLogger(__name__)

STREAM_CONTENT_TYPE = "text/event-stream"


class CompanionRequired(APIException):
    """A story needs someone to tell it.

    409 rather than 400: nothing about the request is malformed, the account is
    simply not ready. The child surface never reaches this — a child gets to the
    story page through their companion — so the message is written for the parent
    screen that would.
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This child has no companion yet. Create one before starting a story."
    default_code = "companion_required"


class AdventureChildView(GenericAPIView):
    """Shared plumbing for the two child-scoped adventure endpoints."""

    permission_classes = [IsAuthenticated, IsParentOwner]
    queryset = Adventure.objects.none()

    def child_for(self, request: Request, child_id: str) -> Child:
        child = get_object_or_404(Child, pk=child_id)
        # Against the *child*, before any adventure row is read or written: a
        # request from the wrong parent must not learn whether stories exist.
        self.check_object_permissions(request, child)
        require_consent(cast(User, request.user))

        if not Companion.objects.filter(child=child).exists():
            raise CompanionRequired
        return child

    def payload_for(self, adventure: Adventure) -> dict[str, Any]:
        return start_payload(
            adventure,
            stream_url=reverse("adventure-stream", kwargs={"adventure_id": adventure.id}),
        )


@extend_schema_view(
    post=extend_schema(
        operation_id="adventure_begin",
        summary="Begin or rejoin an adventure",
        description=(
            "Returns the child's in-progress adventure if they have one (**200**), "
            "and otherwise generates and persists a story plan and returns the new "
            "adventure (**201**). Either way the body carries the SSE URL that "
            "streams the beats.\n\n"
            "It is idempotent on purpose: a client that has lost the adventure id — "
            "a hard refresh, a remount, a second device — must rejoin the same story "
            "rather than pay for a new one. A child gets a new adventure once the "
            "current one is no longer `in_progress`.\n\n"
            "The first beat is generated inside the stream, not here, so the client "
            "can open the stream and start the companion's thinking animation "
            "immediately.\n\n"
            "The stream itself is not in this schema: it is a raw "
            "`text/event-stream` endpoint (`GET /api/v1/adventures/{id}/stream`) "
            "rather than a DRF view. Its frames are documented in "
            "`apps/story/streaming.py`."
        ),
        request=AdventureStartRequestSerializer,
        responses={200: AdventureStartSerializer, 201: AdventureStartSerializer},
    )
)
class AdventureStartView(AdventureChildView):
    serializer_class = AdventureStartRequestSerializer
    # An adventure costs a plan call, a beat call, an illustration and a TTS
    # call before the child has done anything (§22.4). A family starts a handful
    # a day; anything past a couple of dozen an hour is a bug or an attack.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "adventure"

    def post(self, request: Request, child_id: str, *args: Any, **kwargs: Any) -> Response:
        child = self.child_for(request, child_id)

        # Validated even though nothing reads it yet, so a client that sends a
        # malformed `context` is told now rather than on the day we start using it.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        adventure, created = orchestrator.resume_or_create_adventure(child)
        return Response(
            AdventureStartSerializer(self.payload_for(adventure)).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="adventure_current",
        summary="The adventure this child is in the middle of",
        description=(
            "**200** with the same body as `POST /adventures` when the child has an "
            "`in_progress` adventure, **204** when they do not.\n\n"
            "This is what the child surface calls on mount: it resumes a story "
            "across a reload without the possibility of starting one, so a client "
            "bug can cost a request but never a generation."
        ),
        responses={200: AdventureStartSerializer, 204: None},
    )
)
class AdventureCurrentView(AdventureChildView):
    serializer_class = AdventureStartSerializer
    # Its own scope, sized for a read. Sharing `adventure` would let a page that
    # merely *asks* whether a story is in progress spend the budget for starting
    # one — the same mistake `companion_read` exists to undo, one endpoint over.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "adventure_read"

    def get(self, request: Request, child_id: str, *args: Any, **kwargs: Any) -> Response:
        child = self.child_for(request, child_id)

        adventure = orchestrator.current_adventure(child)
        if adventure is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(AdventureStartSerializer(self.payload_for(adventure)).data)


class StreamRateThrottle(SimpleRateThrottle):
    """A per-parent ceiling on stream opens, applied by hand.

    DRF's throttles are attached to DRF views, and this endpoint is not one — so
    the `adventure` scope guarded only the POST, and opening the stream, which is
    where a beat is actually generated, was unlimited. A client stuck in a retry
    loop on a beatless adventure could run N generations and throw all but one away.

    Keyed on the parent, never the IP: a family behind one NAT is one address, and
    the thing worth bounding is a runaway client rather than a guessable secret.
    The user is passed in because this runs after hand-rolled authentication, so
    `request.user` has not been populated by any middleware.
    """

    scope = "adventure_stream"

    def __init__(self, user: Any) -> None:
        self.user = user
        super().__init__()

    def get_cache_key(self, request: Any = None, view: Any = None) -> str:
        return self.cache_format % {"scope": self.scope, "ident": self.user.pk}

    def allow(self) -> bool:
        """`allow_request` without the DRF request/view it never looks at.

        The base class reads neither argument once `get_cache_key` stops asking
        for them, and this endpoint has neither to give — the request here is a
        plain `HttpRequest` and there is no `APIView` at all.
        """
        return self.allow_request(None, None)  # type: ignore[arg-type]


def _within_stream_rate(user: Any) -> bool:
    return StreamRateThrottle(user).allow()


async def adventure_stream(request: HttpRequest, adventure_id: str) -> HttpResponseBase:
    """Stream this adventure's beats as SSE (§18.5). Async, ASGI-only.

    `HttpResponseBase`, not `HttpResponse`: a `StreamingHttpResponse` is not one.
    They are siblings, and the difference is exactly the thing this view exists
    for — a `StreamingHttpResponse` has no `content`, only an iterator.
    """
    if request.method != "GET":
        return _error(status.HTTP_405_METHOD_NOT_ALLOWED, "Only GET is supported.")

    user = await sync_to_async(_authenticated_user)(request)
    if user is None:
        return _error(status.HTTP_401_UNAUTHORIZED, "Authentication credentials were not provided.")

    # After authentication, so the limit is per parent — and before the adventure
    # is loaded, so a hammering client costs one cache read.
    if not await sync_to_async(_within_stream_rate)(user):
        logger.warning("story.stream_throttled parent=%s adventure=%s", user.pk, adventure_id)
        return _error(status.HTTP_429_TOO_MANY_REQUESTS, "Too many story streams just now.")

    adventure, owned = await sync_to_async(_load_adventure)(adventure_id, user)
    if adventure is None:
        return _error(status.HTTP_404_NOT_FOUND, "No such adventure.")
    if not owned:
        return _error(status.HTTP_403_FORBIDDEN, "This resource belongs to another account.")

    logger.info("story.stream_open adventure=%s parent=%s", adventure.id, user.pk)
    response = StreamingHttpResponse(
        streaming.stream_first_beat(adventure),
        content_type=STREAM_CONTENT_TYPE,
    )
    for header, value in streaming.STREAM_HEADERS.items():
        response[header] = value
    return response


def _authenticated_user(request: HttpRequest) -> Any:
    """Resolve the bearer token by hand, because DRF is not in this path.

    Only `JWTAuthentication`, deliberately: `SessionAuthentication` is enabled
    project-wide for the admin and the browsable API, and honouring a session
    cookie on a streaming GET would make this the one endpoint a cross-site page
    could open on a signed-in parent's behalf.
    """
    try:
        resolved = JWTAuthentication().authenticate(request)  # type: ignore[arg-type]
    except AuthenticationFailed:
        return None
    return resolved[0] if resolved else None


def _load_adventure(adventure_id: str, user: Any) -> tuple[Adventure | None, bool]:
    """The adventure and whether this parent owns it.

    Returned as a pair rather than as one filtered lookup so "does not exist" and
    "belongs to someone else" stay distinguishable — the same reasoning as
    `common.permissions.ParentScopedQuerysetMixin`, where an ownership bug that
    looks like a deleted row is the thing that costs a day of debugging.

    `select_related` because the generator immediately needs the child and the
    companion, and following a lazy FK from inside an async generator raises.
    """
    adventure = (
        Adventure.objects.select_related("child", "child__companion")
        .filter(pk=adventure_id)
        .first()
    )
    if adventure is None:
        return None, False
    return adventure, adventure.child.parent_id == user.pk


def _error(code: int, detail: str) -> JsonResponse:
    """A DRF-shaped error body, so one API has one error shape (§18.8).

    Never rendered to a child: the story client treats any non-stream response as
    "the companion is still thinking" and shows the idle scene (§13).
    """
    return JsonResponse({"detail": detail}, status=code)
