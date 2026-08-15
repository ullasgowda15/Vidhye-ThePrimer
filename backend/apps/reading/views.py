"""The reading moment (Docs/Build/Reading-Integration.md §8).

    GET  /api/v1/children/{id}/reading    a line to read, or "not this child"
    POST /api/v1/children/{id}/reading    what came back

Two endpoints and no third. There is deliberately nowhere to send a transcript,
nowhere to receive a score, and no field on the way out that a client could
render as a mark. Phase 1 asks a child to read to their companion and answers
warmly; the verdict arrives in Phase 2, against thresholds re-tuned on the corpus
these two endpoints exist to build.

The invitation is a GET because it is a read with no side effects — a child who
opens the warm close twice has not read twice — and because the client needs the
text on screen before the microphone is ever touched.
"""

from __future__ import annotations

import logging
import random
from typing import Any, cast

from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.consent import require_consent
from apps.accounts.models import User
from apps.common.permissions import IsParentOwner
from apps.profiles.models import Child
from apps.reading import service
from apps.reading.serializers import (
    ReadingAttemptRequestSerializer,
    ReadingAttemptSerializer,
    ReadingInvitationSerializer,
)
from apps.safety import pipeline as safety
from apps.story.models import PlaySession

logger = logging.getLogger(__name__)

#: What the companion says to invite a line. Plain, short, and phrased as a
#: favour rather than a test — "read this to me" is a thing you ask a friend.
INVITATIONS = (
    "Will you read this one to me?",
    "Can you read this bit? I love hearing you.",
    "Your turn to read to me!",
)

#: And what it says afterwards — **unconditionally**, whatever came back.
#:
#: Nothing here is graded, so nothing here may sound graded. "Well done" is a
#: verdict; "I liked hearing you" is a response. A child who stumbled through it
#: and a child who read it perfectly get the same warmth, because in Phase 1 we
#: genuinely do not know which happened and would be guessing if we implied we
#: did.
THANKS = (
    "Thank you for reading to me.",
    "I liked hearing your voice.",
    "That was lovely. Thank you.",
)

#: For a child who was invited and stayed quiet. Not a nudge and not a
#: disappointment — going quiet is an answer, and the next invitation is
#: tomorrow.
QUIET = (
    "That's alright. Another time.",
    "No worries. Shall we go?",
)


class ReadingView(GenericAPIView):
    """Both halves of one moment, scoped to one child."""

    permission_classes = [IsAuthenticated, IsParentOwner]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "adventure_read"
    serializer_class = ReadingAttemptRequestSerializer

    def child_for(self, request: Request, child_id: str) -> Child:
        child = get_object_or_404(Child, pk=child_id)
        self.check_object_permissions(request, child)
        require_consent(cast(User, request.user))
        return child

    @extend_schema(responses=ReadingInvitationSerializer)
    def get(self, request: Request, child_id: str, *args: Any, **kwargs: Any) -> Response:
        child = self.child_for(request, child_id)

        if not service.may_read(child):
            # A complete, correct answer — not an error and not an empty body.
            # Most children are pre-readers and this is what they get.
            return Response(ReadingInvitationSerializer({"offered": False}).data)

        line = service.choose_line(child)
        if line is None:
            return Response(ReadingInvitationSerializer({"offered": False}).data)

        return Response(
            ReadingInvitationSerializer(
                {
                    "offered": True,
                    "passageId": line["id"],
                    "text": line["text"],
                    "prompt": random.choice(INVITATIONS),
                }
            ).data
        )

    @extend_schema(request=ReadingAttemptRequestSerializer, responses=ReadingAttemptSerializer)
    def post(self, request: Request, child_id: str, *args: Any, **kwargs: Any) -> Response:
        child = self.child_for(request, child_id)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The line has to be one we offered. Without this the corpus records
        # whatever text a client claims the child was looking at, and a corpus
        # whose transcripts are attacker-supplied is worse than no corpus —
        # Phase 2 tunes a detector against exactly this field.
        line = service.passages.get(data["passageId"])
        if line is None:
            return Response({"detail": "unknown passage"}, status=400)

        audio = data.get("audio")
        if audio is not None and audio.size > settings.READING_MAX_UPLOAD_BYTES:
            # A stuck recorder or somebody exploring the endpoint. Treated as a
            # quiet turn rather than an error: the child did nothing wrong and
            # must not see a failure (§13).
            logger.warning("reading.upload_too_large child=%s bytes=%d", child.pk, audio.size)
            audio = None

        session = None
        if data.get("sessionId"):
            session = PlaySession.objects.filter(pk=data["sessionId"], child=child).first()

        attempt = service.record(
            child=child,
            passage_id=line["id"],
            # The bank's text, never the client's. Same reason as above.
            passage_text=line["text"],
            session=session,
            audio=audio,
            duration_ms=data.get("durationMs"),
        )

        # §13.10's ingress rule: every point a child's words enter the system.
        #
        # What we have here is the *passage*, not a transcript — nothing has
        # listened to the audio and Phase 1 never will. Passing it anyway is
        # deliberate rather than theatre: this is a real ingress path, it will
        # start carrying a transcript in Phase 2, and a guard added at the same
        # time as the thing it guards is a guard nobody forgets to add.
        safety.guard_child_utterance(
            line["text"],
            child,
            ref=str(attempt.pk),
            surface="reading_aloud",
        )

        if not attempt.spoke:
            return Response(
                ReadingAttemptSerializer(
                    {"attemptId": attempt.pk, "say": random.choice(QUIET), "kept": False}
                ).data
            )

        return Response(
            ReadingAttemptSerializer(
                {
                    "attemptId": attempt.pk,
                    "say": random.choice(THANKS),
                    "kept": attempt.audio_kept,
                }
            ).data
        )
