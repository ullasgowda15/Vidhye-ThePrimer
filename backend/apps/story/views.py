"""Adventure endpoints (PRODUCT.md §18.4, §18.5, §16.4).

    POST /api/v1/children/{id}/adventures           begin *or rejoin* an adventure
    GET  /api/v1/children/{id}/adventures/current   the in-progress one, or 204
    GET  /api/v1/adventures/{id}/stream             SSE: the beat the child is on
    POST /api/v1/adventures/{id}/choice             what the child just did -> 202
    POST /api/v1/adventures/{id}/end                close the story and the sitting

They are deliberately different kinds of view.

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
from rest_framework.exceptions import APIException, AuthenticationFailed, NotFound
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
from apps.story import orchestrator, streaming, wellbeing
from apps.story.models import Adventure
from apps.story.serializers import (
    AdventureEndRequestSerializer,
    AdventureEndSerializer,
    AdventureStartRequestSerializer,
    AdventureStartSerializer,
    ChoiceAcceptedSerializer,
    ChoiceRequestSerializer,
    WellbeingSerializer,
    start_payload,
)

logger = logging.getLogger(__name__)

STREAM_CONTENT_TYPE = "text/event-stream"


class RestingHours(APIException):
    """The parent's daily time window is closed (§13.6).

    **403 rather than 200-with-a-flag, and the child never sees it.** The child
    surface asks `GET …/wellbeing` on mount and renders the companion resting —
    a scene, with the companion's own line in it, never an error (§13). This
    status is the *backstop* under that: a client that skips the read, a second
    device, a stale tab, a script. Making the door a real refusal rather than a
    convention is what stops "we asked the client not to" from being the whole
    enforcement.

    `detail` carries the companion's line, so even a caller that renders the error
    body renders something warm.
    """

    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Stories are resting just now."
    default_code = "outside_time_window"


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

        # §13.6, and the condition is the point: the window is checked **only when
        # this request would start a new story**. A child already in one is allowed
        # to reach their warm close even if the window shut while they were playing,
        # because nothing in this product takes something away mid-sentence
        # (`story.wellbeing`). Checked before the plan call, so a refusal costs
        # nothing.
        #
        # The extra read this costs is one indexed query on the story-start path,
        # and it is the same question `resume_or_create_adventure` asks again under
        # its lock. Racing it is harmless: a second request that slips through here
        # is handed the first request's adventure rather than starting its own.
        if orchestrator.current_adventure(child) is None:
            gate = wellbeing.gate_for(child)
            if not gate.open:
                raise RestingHours(detail=gate.message)

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


@extend_schema_view(
    get=extend_schema(
        operation_id="child_wellbeing",
        summary="May a story start right now? (§13.6)",
        description=(
            "`open: true` when the child is inside the daily time window their "
            "parent set, or when no window is set — which is the default.\n\n"
            "`message` is the companion's own line for the closed case, so the "
            "child surface renders a warm scene rather than composing its own "
            "wording (§13). The child is **never** shown an error, a countdown, or "
            "a time; nothing in this response counts anything down.\n\n"
            "Advisory to the client and enforced on the server: "
            "`POST /children/{id}/adventures` refuses a *new* story outside the "
            "window regardless of whether this was asked. A story already in "
            "progress is always allowed to reach its ending."
        ),
        responses=WellbeingSerializer,
    )
)
class ChildWellbeingView(AdventureChildView):
    """What the child surface asks before it asks for a story.

    A read, so a client may call it as often as it likes, and it costs no
    generation. It shares `AdventureChildView`'s ownership + consent + companion
    checks because it is answered from the same place in the request as the start
    endpoint — a client must not be able to learn a child's play window through a
    door the start endpoint would have closed.
    """

    serializer_class = WellbeingSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "adventure_read"

    def get(self, request: Request, child_id: str, *args: Any, **kwargs: Any) -> Response:
        child = self.child_for(request, child_id)
        gate = wellbeing.gate_for(child)
        return Response(
            WellbeingSerializer(
                {"open": gate.open, "reason": gate.reason, "message": gate.message}
            ).data
        )


class AdventureScopedView(GenericAPIView):
    """Shared plumbing for the two endpoints that act on an adventure by id.

    Ownership is checked against the adventure's *child*, exactly as the
    child-scoped views do, so "this adventure belongs to another family" and "this
    child is not yours" are one rule with one implementation. A 404 for an
    adventure that exists but is not theirs would be kinder to an attacker and no
    kinder to anyone else — `IsParentOwner` already answers 403 consistently across
    the API (§18.8).
    """

    permission_classes = [IsAuthenticated, IsParentOwner]
    queryset = Adventure.objects.none()

    def adventure_for(self, request: Request, adventure_id: str) -> Adventure:
        adventure = get_object_or_404(
            Adventure.objects.select_related("child", "child__companion", "session"),
            pk=adventure_id,
        )
        self.check_object_permissions(request, adventure.child)
        require_consent(cast(User, request.user))
        return adventure


@extend_schema_view(
    post=extend_schema(
        operation_id="adventure_choice",
        summary="Submit what the child just did",
        description=(
            "Records the child's interaction with a beat and returns **202** "
            "immediately; the consequence arrives on the stream (`streamUrl`), not "
            "in this response (§18.6).\n\n"
            "It answers before anything is generated on purpose. The tap has to be "
            "acknowledged on screen within about 100ms (§14.7), and the beat that "
            "reacts to it takes seconds — so the row is written here and the "
            "generation is started on a worker, which is usually finished by the "
            "time the client has reopened the stream.\n\n"
            "Idempotent per beat: a double-tap records one choice and the first one "
            "stands (`recorded: false` on the second). Sent for **every** "
            "interaction, including a beat that only asked the child to carry on — "
            "`latencyMs`, `retries` and `assisted` are wellbeing and difficulty "
            "signals we want for all of them.\n\n"
            "There is no wrong answer to reject: nothing here is checked against "
            "the beat's `expected`, and no request to this endpoint can fail the "
            "child (§8.5)."
        ),
        request=ChoiceRequestSerializer,
        responses={202: ChoiceAcceptedSerializer},
    )
)
class AdventureChoiceView(AdventureScopedView):
    serializer_class = ChoiceRequestSerializer
    # A choice costs a beat call plus its media, exactly like starting one does,
    # so it gets a scope of its own rather than riding on the read budget. Sized
    # for several full adventures an hour and nothing like a loop.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "adventure_choice"

    def post(self, request: Request, adventure_id: str, *args: Any, **kwargs: Any) -> Response:
        adventure = self.adventure_for(request, adventure_id)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        beat = adventure.beats.filter(pk=data["beatId"]).first()
        if beat is None:
            # Scoped to the adventure in the URL, so a beat id from another
            # family's story is a 404 here rather than a cross-story write.
            raise NotFound("No such beat in this adventure.")

        choice, created = orchestrator.record_choice(
            beat,
            chosen_key=data["choiceKey"],
            value=data.get("value"),
            latency_ms=data.get("latencyMs"),
            retries=data.get("retries", 0),
            assisted=data.get("assisted", False),
        )
        if created:
            # The branch is settled, so the consequence can start generating now
            # rather than when the client next connects (§4.9).
            orchestrator.schedule_advance(adventure, after=beat)

        return Response(
            ChoiceAcceptedSerializer(
                {
                    "choiceId": choice.id,
                    "beatId": beat.id,
                    "recorded": created,
                    "streamUrl": reverse("adventure-stream", kwargs={"adventure_id": adventure.id}),
                }
            ).data,
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema_view(
    post=extend_schema(
        operation_id="adventure_end",
        summary="Close the adventure and the sitting",
        description=(
            "Marks the adventure `completed` (or `abandoned`, for `reason: exit`) "
            "and finalises the `PlaySession` with its duration and why it "
            "stopped.\n\n"
            "Idempotent: an adventure that is already finished is returned "
            "unchanged, so the child tapping 'all done' and the page unloading "
            "cannot disagree about when the sitting ended.\n\n"
            "Until this is called the adventure stays `in_progress` and "
            "`POST /children/{id}/adventures` keeps rejoining it, which is what "
            "makes a reload land back in the same story rather than paying for a "
            "new one."
        ),
        request=AdventureEndRequestSerializer,
        responses={200: AdventureEndSerializer},
    )
)
class AdventureEndView(AdventureScopedView):
    serializer_class = AdventureEndRequestSerializer
    # A write, but a cheap one — no generation. Shares the read scope's headroom
    # rather than the generation scope's, because a client that ends the same
    # adventure twice must not be locked out of starting the next one.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "adventure_read"

    def post(self, request: Request, adventure_id: str, *args: Any, **kwargs: Any) -> Response:
        adventure = self.adventure_for(request, adventure_id)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ended = orchestrator.end_adventure(adventure, reason=serializer.validated_data["reason"])
        return Response(
            AdventureEndSerializer(
                {
                    "adventureId": ended.id,
                    "status": ended.status,
                    "completedAt": ended.completed_at,
                    "beats": ended.beats.count(),
                }
            ).data
        )


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
        streaming.stream_beat(adventure),
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
