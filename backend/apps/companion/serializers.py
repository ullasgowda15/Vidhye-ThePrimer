"""Companion serialization (PRODUCT.md §18.3, §9.1).

The wire format is deliberately *keys in, keys out*: `archetype`, `palette` and
`personality_preset` are catalogue keys, never free text and never raw colour
values. Three reasons, in order of importance:

1. A palette or archetype the catalogue does not know would flow straight into
   an illustration prompt. Closed sets keep unvalidated text out of the image
   model, and out of the descriptor prompt with it.
2. The stored `personality` JSON is injected into every generation (§9.2). It
   must be text we wrote.
3. The child-facing surfaces can render a key; they cannot be trusted to have
   an opinion about what "ember" means.

Read responses add the resolved colours and the asset URLs, because the picker
and the greeting scene need to paint something.
"""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.companion.catalog import (
    ARCHETYPE_KEYS,
    DEFAULT_PALETTE,
    DEFAULT_PRESET,
    PALETTE_KEYS,
    PRESET_KEYS,
)
from apps.companion.models import (
    CANONICAL_EXPRESSIONS,
    GREETING_AUDIO,
    PORTRAIT,
    Companion,
)
from apps.companion.service import create_companion, greeting_line
from apps.media.models import Media

MAX_NAME_LENGTH = 40


class PaletteColorsSerializer(serializers.Serializer):
    """The resolved palette.

    Declared field by field rather than left as free JSON so the generated
    TypeScript client knows a swatch has a `primary` — and so the catalogue keys
    arrive in the picker as unions of literals rather than bare strings, which
    is what makes an invalid choice a build error instead of a 400.
    """

    key = serializers.ChoiceField(choices=PALETTE_KEYS)
    # `label` is also an attribute of DRF's `Field`, so the stubs read this as a
    # shadowed assignment. It is not: the serializer metaclass pops declared
    # fields off the class, and `self.label` still comes from `Field.__init__`.
    label = serializers.CharField()  # type: ignore[assignment]
    primary = serializers.CharField()
    secondary = serializers.CharField()
    accent = serializers.CharField()
    description = serializers.CharField()


class GreetingSerializer(serializers.Serializer):
    text = serializers.CharField()
    audio_url = serializers.CharField(allow_null=True)


class ArchetypeOptionSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=ARCHETYPE_KEYS)
    # `label` is also an attribute of DRF's `Field`, so the stubs read this as a
    # shadowed assignment. It is not: the serializer metaclass pops declared
    # fields off the class, and `self.label` still comes from `Field.__init__`.
    label = serializers.CharField()  # type: ignore[assignment]
    blurb = serializers.CharField()
    default_palette = serializers.ChoiceField(choices=PALETTE_KEYS)
    default_personality_preset = serializers.ChoiceField(choices=PRESET_KEYS)


class PersonalityPresetOptionSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=PRESET_KEYS)
    # `label` is also an attribute of DRF's `Field`, so the stubs read this as a
    # shadowed assignment. It is not: the serializer metaclass pops declared
    # fields off the class, and `self.label` still comes from `Field.__init__`.
    label = serializers.CharField()  # type: ignore[assignment]
    traits = serializers.ListField(child=serializers.CharField())


class PaletteKeyField(serializers.ChoiceField):
    """A palette as a catalogue key on the wire, a colour dict in the database.

    §17.2 stores `palette` as JSON so a per-world or seasonal palette can grow
    extra fields later without a migration. The API stays a key in both
    directions, so the OpenAPI type is a plain enum string and the generated TS
    client gets a union of literals rather than `Record<string, unknown>`.
    The resolved colours ride along read-only as `palette_colors`.
    """

    def to_representation(self, value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("key", DEFAULT_PALETTE))
        return str(value)


class CompanionSerializer(serializers.ModelSerializer):
    archetype = serializers.ChoiceField(choices=ARCHETYPE_KEYS)
    palette = PaletteKeyField(choices=PALETTE_KEYS, required=False, default=DEFAULT_PALETTE)
    personality_preset = serializers.ChoiceField(
        choices=PRESET_KEYS,
        required=False,
        default=DEFAULT_PRESET,
        write_only=True,
        help_text="Catalogue key; expands into the stored personality sheet (§9.2).",
    )
    name = serializers.CharField(max_length=MAX_NAME_LENGTH)

    palette_colors = PaletteColorsSerializer(source="palette", read_only=True)
    personality = serializers.JSONField(read_only=True)
    evolution_state = serializers.JSONField(read_only=True)
    reference_asset_ids = serializers.ListField(child=serializers.UUIDField(), read_only=True)

    assets_ready = serializers.BooleanField(read_only=True)
    portrait_url = serializers.SerializerMethodField()
    expressions = serializers.SerializerMethodField()
    greeting = serializers.SerializerMethodField()

    class Meta:
        model = Companion
        fields = [
            "id",
            "child",
            "archetype",
            "name",
            "palette",
            "palette_colors",
            "personality_preset",
            "personality",
            "voice_id",
            "visual_descriptor",
            "evolution_state",
            "reference_asset_ids",
            "assets_status",
            "assets_ready",
            "portrait_url",
            "expressions",
            "greeting",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "child",
            "voice_id",
            "visual_descriptor",
            "assets_status",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data: dict[str, Any]) -> Companion:
        """Delegate to the service rather than to `Model.objects.create`.

        Everything that makes a companion *coherent* — expanding the preset into
        a personality sheet, fixing the voice, seeding the evolution state —
        lives in one function, so a companion created by the dev command and one
        created through the API are the same object.
        """
        return create_companion(
            child=validated_data["child"],
            archetype=validated_data["archetype"],
            name=validated_data["name"],
            palette=validated_data.get("palette", DEFAULT_PALETTE),
            personality_preset=validated_data.get("personality_preset", DEFAULT_PRESET),
        )

    def validate_name(self, value: str) -> str:
        """A companion's name is chosen by a child, so it is barely validated.

        Trimmed and length-bounded only. Anything cleverer — a profanity list, a
        real-name check — would reject "Poopy the Brave" from a five-year-old,
        which is a name they will love for a year. It is never shown to anyone
        outside the family.
        """
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Your companion needs a name.")
        return name

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_portrait_url(self, obj: Companion) -> str | None:
        return self._urls(obj).get(PORTRAIT)

    @extend_schema_field(serializers.DictField(child=serializers.CharField()))
    def get_expressions(self, obj: Companion) -> dict[str, str]:
        """The canonical expression set, label -> CDN URL (§9.6)."""
        urls = self._urls(obj)
        return {label: urls[label] for label in CANONICAL_EXPRESSIONS if label in urls}

    @extend_schema_field(GreetingSerializer)
    def get_greeting(self, obj: Companion) -> dict[str, str | None]:
        """The hello: its audio and its text.

        The text ships alongside the audio because narration is captioned
        (§14.6) and because a device that will not autoplay audio must still be
        able to show what the companion said.
        """
        return {
            "text": greeting_line(obj),
            "audio_url": self._urls(obj).get(GREETING_AUDIO),
        }

    def _urls(self, obj: Companion) -> dict[str, str]:
        """Resolve the manifest's media ids to servable URLs, in one query.

        Cached per serializer instance: a single companion response asks for the
        portrait, the five expressions and the greeting, and that is one lookup,
        not seven.
        """
        cache: dict[str, dict[str, str]] = self.context.setdefault("_media_urls", {})
        if (resolved := cache.get(str(obj.pk))) is not None:
            return resolved

        manifest = obj.reference_manifest or {}
        rows = Media.objects.filter(id__in=list(manifest.values()))
        # Only assets that passed the scan are ever handed to a client (§13.8).
        by_id = {str(row.id): row.url for row in rows if row.is_servable}

        resolved = {label: by_id[mid] for label, mid in manifest.items() if mid in by_id}
        cache[str(obj.pk)] = resolved
        return resolved


class CompanionCatalogSerializer(serializers.Serializer):
    """The picker's options (§14.5). Read-only, and the same list the API validates against."""

    archetypes = ArchetypeOptionSerializer(many=True)
    palettes = PaletteColorsSerializer(many=True)
    personality_presets = PersonalityPresetOptionSerializer(many=True)
