"""Adventure endpoints (PRODUCT.md §18.4, §18.5, §16.4).

    POST /api/v1/children/{id}/adventures   begin an adventure (plan + stream url)
    GET  /api/v1/adventures/{id}/stream     SSE: the beats

The two are deliberately different kinds of view.

**The POST is an ordinary DRF view.** It is a normal request/response: check
ownership and consent, generate the plan, persist, answer. It returns *before*
the first beat is generated so the client can open the stream immediately and
put the companion's thinking animation on screen — the wait is the same either
way, and this way the child is looking at something during it (§14.5).

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
from rest_framework.throttling import ScopedRateThrottle
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


@extend_schema_view(
    post=extend_schema(
        operation_id="adventure_begin",
        summary="Begin an adventure",
        description=(
            "Generates and persists the story plan, then returns the SSE URL that "
            "streams the beats. The first beat is generated inside the stream, not "
            "here, so the client can open the stream and start the companion's "
            "thinking animation immediately.\n\n"
            "The stream itself is not in this schema: it is a raw "
            "`text/event-stream` endpoint (`GET /api/v1/adventures/{id}/stream`) "
            "rather than a DRF view. Its frames are documented in "
            "`apps/story/streaming.py`."
        ),
        request=AdventureStartRequestSerializer,
        responses={201: AdventureStartSerializer},
    )
)
class AdventureStartView(GenericAPIView):
    serializer_class = AdventureStartRequestSerializer
    permission_classes = [IsAuthenticated, IsParentOwner]
    # An adventure costs a plan call, a beat call, an illustration and a TTS
    # call before the child has done anything (§22.4). A family starts a handful
    # a day; anything past a couple of dozen an hour is a bug or an attack.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "adventure"
    queryset = Adventure.objects.none()

    def post(self, request: Request, child_id: str, *args: Any, **kwargs: Any) -> Response:
        child = get_object_or_404(Child, pk=child_id)
        # Against the *child*, before any adventure row is read or written: a
        # request from the wrong parent must not learn whether stories exist.
        self.check_object_permissions(request, child)
        require_consent(cast(User, request.user))

        if not Companion.objects.filter(child=child).exists():
            raise CompanionRequired

        # Validated even though nothing reads it yet, so a client that sends a
        # malformed `context` is told now rather than on the day we start using it.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        adventure = orchestrator.create_adventure(child)
        payload = start_payload(
            adventure,
            stream_url=reverse("adventure-stream", kwargs={"adventure_id": adventure.id}),
        )
        return Response(
            AdventureStartSerializer(payload).data,
            status=status.HTTP_201_CREATED,
        )


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
