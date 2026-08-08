"""Adventure serialization (PRODUCT.md §18.4).

Field names here are **camelCase**, unlike the rest of the API. That is not a
slip: §18.4 and §18.5 write the adventure and beat payloads in camelCase, and the
SSE frames — which are hand-written JSON, not DRF output — have to match them. A
`streamUrl` in the POST response and a `stream_url` in the event that follows it
would be one field with two names, so the whole story surface uses the spec's
spelling and the seam is here rather than in the client.
"""

from rest_framework import serializers

from apps.story.models import Adventure


class AdventureCompanionSerializer(serializers.Serializer):
    """The companion, reduced to what the story client needs up front (§18.4).

    `voiceId` so the client can pre-warm audio; `name` so the loading scene can
    say who is thinking. Nothing else — the portrait and expressions are already
    on the companion endpoint.
    """

    name = serializers.CharField()
    voiceId = serializers.CharField(allow_null=True)


class AdventureStartSerializer(serializers.Serializer):
    """The response to `POST /children/{id}/adventures` (§18.4)."""

    adventureId = serializers.UUIDField()
    streamUrl = serializers.CharField()
    companion = AdventureCompanionSerializer()
    estimatedBeats = serializers.IntegerField()


class AdventureStartRequestSerializer(serializers.Serializer):
    """The optional client hints on `POST /adventures` (§18.4).

    Validated but not yet used: mood- and time-of-day-aware planning is Day 11's
    work. Declared now so a client can send it from the start, and so a malformed
    `context` is rejected today rather than on the day we begin reading it.

    The field is built in `get_fields` rather than declared as a class attribute
    because §18.4 names it `context`, and a class attribute by that name shadows
    `Serializer.context` — the request/view dictionary every serializer relies on.
    The wire name is what the spec fixes; where the field object lives is ours.
    """

    def get_fields(self) -> dict[str, serializers.Field]:
        return {
            "context": serializers.DictField(
                required=False,
                help_text="e.g. {timeOfDay, mood_hint}",
            )
        }


def start_payload(adventure: Adventure, *, stream_url: str) -> dict:
    """Build the §18.4 body from a persisted adventure."""
    companion = adventure.child.companion
    return {
        "adventureId": adventure.id,
        "streamUrl": stream_url,
        "companion": {"name": companion.name, "voiceId": companion.voice_id},
        "estimatedBeats": adventure.estimated_beats,
    }
