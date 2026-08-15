"""Wire shapes for the reading moment (Docs/Build/Reading-Integration.md §8)."""

from __future__ import annotations

from rest_framework import serializers


class ReadingInvitationSerializer(serializers.Serializer):
    """What the child is being asked to read, or that they are not being asked.

    `offered` is an explicit boolean rather than a null payload, because "this
    child is not a reader" and "something went wrong fetching a line" have to be
    different answers on the client — one draws nothing and is correct, the other
    is a bug. A null body cannot tell them apart.
    """

    offered = serializers.BooleanField()
    passageId = serializers.CharField(required=False)
    text = serializers.CharField(required=False)
    #: The companion's invitation, spoken. Held on the server with the rest of
    #: the companion's words so the two cannot drift into different voices.
    prompt = serializers.CharField(required=False)


class ReadingAttemptRequestSerializer(serializers.Serializer):
    """What the child's device sends back.

    No transcript field, and no place to put one. Phase 1 does not transcribe —
    the audio either becomes a consented corpus file or nothing at all — and an
    optional field for a verdict is how a verdict starts arriving from a client.
    """

    passageId = serializers.CharField(max_length=40)
    #: Absent when the child was invited and did not speak: a declined turn, a
    #: refused microphone, or a quiet child. All are worth a row.
    audio = serializers.FileField(required=False, allow_null=True)
    durationMs = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    sessionId = serializers.UUIDField(required=False, allow_null=True)


class ReadingAttemptSerializer(serializers.Serializer):
    """What comes back after a child has read.

    **`say` is warm whatever happened, and there is no field for a score.** Phase
    1's whole claim is that nothing judges the reading, and the response shape is
    where that is either true or quietly not. A `correct` boolean here would be
    read by a client, shown to a child, and believed — on a detector that has
    never heard one.
    """

    attemptId = serializers.UUIDField()
    say = serializers.CharField()
    kept = serializers.BooleanField()
