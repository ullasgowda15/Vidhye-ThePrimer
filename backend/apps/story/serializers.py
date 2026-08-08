"""Adventure serialization (PRODUCT.md §18.4).

Field names here are **camelCase**, unlike the rest of the API. That is not a
slip: §18.4 and §18.5 write the adventure and beat payloads in camelCase, and the
SSE frames — which are hand-written JSON, not DRF output — have to match them. A
`streamUrl` in the POST response and a `stream_url` in the event that follows it
would be one field with two names, so the whole story surface uses the spec's
spelling and the seam is here rather than in the client.
"""

from rest_framework import serializers

from apps.story.models import Adventure, PlaySession


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


class ChoiceRequestSerializer(serializers.Serializer):
    """What the child's tap sends back (§18.6).

    Every field but `beatId` and `choiceKey` is a *signal*, not an answer. §18.6
    names `latencyMs` and `retries`; `assisted` joins them because the companion
    scaffolding is the thing that distinguishes "found it easy" from "got there
    with help", and Day 10 reads all three as learning evidence (§10.4). They are
    recorded on every interaction, including a tap that only means "carry on", so
    the evidence is not silently missing for the beats that asked something simple.

    Nothing here is validated against the beat's `expected`. There is no wrong
    answer to check against (§8.5), so `choiceKey` is accepted as sent and the
    consequence is written warmly from whatever arrived.
    """

    beatId = serializers.UUIDField(help_text="The beat the child was on.")
    choiceKey = serializers.CharField(
        max_length=64,
        help_text=(
            "The option the child picked. `continue` for a beat that only asked "
            "them to carry on."
        ),
    )
    # `JSONField` rather than a typed one: §18.6's example is an integer, a `say`
    # beat's answer is a word, and a `tap` beat's is a list of what was tapped.
    value = serializers.JSONField(
        required=False,
        allow_null=True,
        help_text="What the child actually answered, when the beat asked for one.",
    )
    latencyMs = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        help_text="How long from the beat being ready to the tap.",
    )
    retries = serializers.IntegerField(
        required=False,
        default=0,
        min_value=0,
        help_text="Attempts before this one. Never a score — a difficulty signal.",
    )
    assisted = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether the companion scaffolded the answer (§8.5).",
    )


class ChoiceAcceptedSerializer(serializers.Serializer):
    """The `202` body (§18.6). The consequence arrives on the stream, not here."""

    choiceId = serializers.UUIDField()
    beatId = serializers.UUIDField()
    recorded = serializers.BooleanField(
        help_text="False when this beat had already been answered and the first answer stands.",
    )
    streamUrl = serializers.CharField(
        help_text="Reopen this to receive the consequence beat.",
    )


class AdventureEndRequestSerializer(serializers.Serializer):
    """Why the sitting stopped (§13.6)."""

    reason = serializers.ChoiceField(
        choices=[PlaySession.EndedReason.COMPLETED, PlaySession.EndedReason.EXIT],
        required=False,
        default=PlaySession.EndedReason.COMPLETED,
        help_text=(
            "`completed` when the child reached the warm close, `exit` when they "
            "left before it. A wellbeing signal for the parent report, kept "
            "separate from whether the story finished."
        ),
    )


class AdventureEndSerializer(serializers.Serializer):
    """The state the adventure was left in."""

    adventureId = serializers.UUIDField()
    status = serializers.CharField()
    completedAt = serializers.DateTimeField(allow_null=True)
    beats = serializers.IntegerField()


def start_payload(adventure: Adventure, *, stream_url: str) -> dict:
    """Build the §18.4 body from a persisted adventure."""
    companion = adventure.child.companion
    return {
        "adventureId": adventure.id,
        "streamUrl": stream_url,
        "companion": {"name": companion.name, "voiceId": companion.voice_id},
        "estimatedBeats": adventure.estimated_beats,
    }
