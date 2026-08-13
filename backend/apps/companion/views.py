"""Companion endpoints (PRODUCT.md §18.3, §9.1).

    POST /api/v1/children/{id}/companion   create the child's one companion
    GET  /api/v1/children/{id}/companion   fetch it
    GET  /api/v1/children/{id}/greeting    what the companion says on arrival (§4.5)
    GET  /api/v1/companion-catalog         the picker's options

Three rules are enforced here, all server-side:

1. **Consent first.** A companion is child data. The same gate that guards child
   creation guards this (§13.7).
2. **One companion per child**, enforced by the OneToOne *and* by a clear 409
   rather than an integrity error, because "already has one" is a normal thing
   for a client to do twice.
3. **The parent owns the child.** Ownership is checked against the child before
   the companion is touched at all.

The two methods are rate-limited separately — see `CompanionView.get_throttles`.
"""

import logging
from typing import Any, cast

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.consent import require_consent
from apps.accounts.models import User
from apps.analytics import cost, events
from apps.analytics.models import Event
from apps.common.permissions import IsParentOwner
from apps.common.throttling import CompanionCreateThrottle, CompanionReadThrottle
from apps.companion import greeting
from apps.companion.catalog import catalog
from apps.companion.models import Companion
from apps.companion.serializers import (
    ArrivalSerializer,
    CompanionCatalogSerializer,
    CompanionSerializer,
)
from apps.companion.tasks import generate_companion_assets
from apps.profiles.models import Child

logger = logging.getLogger(__name__)


class CompanionExists(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This child already has a companion."
    default_code = "companion_exists"


def _has_onboarded_before(child: Child) -> bool:
    """Has this parent already finished §4.2's flow, for an earlier child?

    Read off the event log rather than off a column on the account, because the
    fact being recorded is "this parent has been through onboarding", and the
    event log is where that fact already lives. A boolean on `User` would be a
    second source that a backfill or a deleted child could put out of step with
    the funnel it exists to feed.

    Costs one indexed count on the last step of a flow a parent completes once.
    """
    return Event.objects.filter(name=Event.ONBOARDING_COMPLETED, parent_id=child.parent_id).exists()


@extend_schema_view(
    get=extend_schema(
        operation_id="companion_retrieve",
        summary="Fetch this child's companion",
        responses=CompanionSerializer,
    ),
    post=extend_schema(
        operation_id="companion_create",
        summary="Create this child's companion",
        description=(
            "Returns immediately with the companion's identity. The reference "
            "portrait, canonical expressions and spoken greeting are generated on "
            "a worker; poll this endpoint and watch `assets_ready`."
        ),
        request=CompanionSerializer,
        responses={201: CompanionSerializer},
    ),
)
class CompanionView(GenericAPIView):
    serializer_class = CompanionSerializer
    permission_classes = [IsAuthenticated, IsParentOwner]
    queryset = Companion.objects.all()

    def get_throttles(self) -> list:
        """A scope per method, because the two methods cost wildly different things.

        `POST` is six paid illustrations and a voice line. `GET` is one indexed row
        lookup that the greeting scene polls every couple of seconds while those
        illustrations render (§4.3).

        They used to share one `ScopedRateThrottle` at the create rate, and the
        consequence was not a slow poll: twenty polls spent the hour's entire
        create budget in forty seconds, every request after that was a 429, and
        since the poll is how the child's screen learns the companion is ready, the
        companion never woke up. A limit that shares a budget with the UI that
        reads it will eventually be spent by that UI.
        """
        if self.request.method == "POST":
            return [CompanionCreateThrottle()]
        return [CompanionReadThrottle()]

    def get_child(self) -> Child:
        """The child in the URL, once ownership has been proven."""
        child = get_object_or_404(Child, pk=self.kwargs["child_id"])
        # Checked against the *child*, before any companion row is read or
        # written: a request from the wrong parent must not be able to learn
        # whether a companion exists.
        self.check_object_permissions(self.request, child)
        return child

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        child = self.get_child()
        companion = get_object_or_404(Companion.objects.select_related("child"), child=child)
        return Response(self.get_serializer(companion).data)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        child = self.get_child()
        require_consent(cast(User, request.user))

        if Companion.objects.filter(child=child).exists():
            raise CompanionExists

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        companion = serializer.save(child=child)

        # Identity is saved; the art and the voice follow on a worker (§5.8).
        # A broker that is down must not cost the family their companion, so the
        # enqueue failure is logged and the assets are built on the next call to
        # the dev command — never surfaced to the child.
        _enqueue_assets(str(companion.id))

        # The last two funnel steps (§22.3), and they are two rather than one on
        # purpose. `companion.created` is *this* child getting a friend;
        # `onboarding.completed` is the parent finishing §4.2's flow, which happens
        # once per account and is what activation's first half asks about. A second
        # child a month later fires the first and not the second, which is why the
        # gate below is a query and not a flag on this request.
        events.track(
            Event.COMPANION_CREATED,
            child=child,
            archetype=getattr(companion, "archetype", ""),
        )
        if not _has_onboarded_before(child):
            events.track(Event.ONBOARDING_COMPLETED, child=child)

        return Response(
            self.get_serializer(companion).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="companion_greeting",
        summary="What the companion says when this child arrives",
        description=(
            "The day-two payoff (§4.5). For a child with memories this is generated "
            "fresh: the companion greets them by name and refers to one specific "
            "thing they did — something they named, chose, counted or loved — before "
            "opening today's adventure.\n\n"
            "For a brand-new child, and for any failure on the generated path, it "
            "returns the fixed first-meeting hello (§4.3) with the audio already "
            "attached to the companion. `kind` says which one you are holding; a "
            "client must never have to infer that from the text.\n\n"
            "It costs a language call and a voice call, so it is throttled like a "
            "write. `audio_url` may be null — the scene is designed to work in "
            "silence, and a child is never shown that anything is missing."
        ),
        responses={200: ArrivalSerializer},
    )
)
class GreetingView(GenericAPIView):
    """The arrival scene's one request.

    A `GenericAPIView` rather than an action on `CompanionView`, because it is a
    different thing with a different cost: reading a companion is an indexed row
    lookup that the imprint scene polls, and this generates text and speech. They
    would have had to share a throttle scope, which is the exact mistake
    `companion_read` exists to undo.
    """

    serializer_class = ArrivalSerializer
    permission_classes = [IsAuthenticated, IsParentOwner]
    queryset = Companion.objects.all()
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "greeting"

    def get(self, request: Request, child_id: str, *args: Any, **kwargs: Any) -> Response:
        child = get_object_or_404(Child, pk=child_id)
        # Against the *child*, before any memory is read: retrieval is the most
        # sensitive read in the product, and a request from the wrong parent must
        # not reach it at all (§11.10).
        self.check_object_permissions(request, child)
        require_consent(cast(User, request.user))

        # A language call, a voice call and the two classifier calls guarding them
        # (§19.9). Attributed to the child and to no sitting, for the reason
        # `companion.tasks` gives at length: the arrival scene happens before a
        # story does, and the child id is what makes the spend both answerable
        # ("what does a relationship cost") and deletable (Day-14 review, P1-2).
        with cost.trace(child=child):
            arrival = greeting.build(child)
        return Response(
            ArrivalSerializer(
                {
                    "kind": arrival.kind,
                    "text": arrival.text,
                    "audio_url": arrival.audio_url,
                    "companion_name": arrival.companion_name,
                    "remembered": arrival.remembered,
                }
            ).data
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="companion_catalog",
        summary="Archetypes, palettes and personality presets for the picker",
        responses=CompanionCatalogSerializer,
    )
)
class CompanionCatalogView(APIView):
    """The picker's options, served rather than duplicated in the client (§14.5)."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(CompanionCatalogSerializer(catalog()).data)


def _enqueue_assets(companion_id: str) -> None:
    try:
        generate_companion_assets.delay(companion_id)
    except Exception:
        # Broker unreachable. The companion exists and is coherent; only its art
        # is missing, and `manage.py render_companion` (or a later retry) will
        # fill it in. This is exactly the failure that must not reach a child.
        logger.exception("companion.enqueue_failed companion=%s", companion_id)
