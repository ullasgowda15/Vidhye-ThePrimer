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


class AdventureWorldSerializer(serializers.Serializer):
    """Where this adventure happens, as the child's screen needs it (§8.3, §15.1).

    Three fields and each one has exactly one consumer. `key` is the palette: it is
    put on `data-world` and the stylesheet does the rest, which is why it is a
    registry key (`story.worlds`) rather than a display name — a world the CSS has
    never heard of renders as the default theme and the child's screen and their
    story quietly disagree about where they are. `name` and `place` are for the
    grown-up in the room and for the accessibility layer; nothing on the child's
    screen is read to them.
    """

    key = serializers.CharField()
    name = serializers.CharField()
    place = serializers.CharField()


class AdventureStartSerializer(serializers.Serializer):
    """The response to `POST /children/{id}/adventures` (§18.4)."""

    adventureId = serializers.UUIDField()
    streamUrl = serializers.CharField()
    companion = AdventureCompanionSerializer()
    estimatedBeats = serializers.IntegerField()
    world = AdventureWorldSerializer()


class AdventureStartRequestSerializer(serializers.Serializer):
    """The optional client hints on `POST /adventures` (§18.4).

    Validated but still not read. Day 11 personalised the world and it did so from
    the interest graph and the world map (§11.7, §8.3) rather than from anything the
    client says — a device hint is the one input to personalisation that nobody
    verified. What is left for this field is mood and time of day, which are real
    and are not today's. Declared so a client can send it from the start, and so a
    malformed `context` is rejected now rather than on the day we begin reading it.

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
    with help", and `apps.learning` reads all three as evidence (§10.4). They are
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
    """Build the §18.4 body from a persisted adventure.

    The world is read off the plan rather than recomputed from the child's graph.
    That is the same rule `Adventure.plan` follows everywhere: the story was planned
    somewhere, the child is being told about *that* place, and a screen that
    re-derived its palette would repaint mid-adventure the first time the graph
    moved. A plan written before Day 11 has no world and falls back to the base
    theme, which is what the empty key means to the client.
    """
    companion = adventure.child.companion
    plan = adventure.plan or {}
    return {
        "adventureId": adventure.id,
        "streamUrl": stream_url,
        "companion": {"name": companion.name, "voiceId": companion.voice_id},
        "estimatedBeats": adventure.estimated_beats,
        "world": {
            "key": str(plan.get("world") or ""),
            "name": str(plan.get("world_name") or ""),
            "place": str(plan.get("place") or ""),
        },
    }
