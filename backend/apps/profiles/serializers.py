"""Child profile serialization (PRODUCT.md §4.2, §17.2).

The seed fields are the only personalization the engine has on day one, and
three of them (`fears`, `avoid_topics`, `intensity_ceiling`) are *safety*
inputs the prompt assembler reads verbatim. So validation here is not tidiness
— it is the boundary that keeps a 400-character blob or a `null` out of a
safety layer.
"""

import datetime
from typing import Any, cast

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.profiles import settings as child_settings
from apps.profiles.models import Child

# A love or a fear is a noun phrase ("dinosaurs", "the dark"), not a paragraph.
# Bounding both length and count bounds the safety layer's token cost too.
MAX_SEED_LENGTH = child_settings.MAX_TOPIC_CHARS
MAX_SEED_COUNT = child_settings.MAX_TOPIC_COUNT

MIN_AGE = 3
MAX_AGE = 7
MIN_INTENSITY = child_settings.MIN_CEILING
MAX_INTENSITY = child_settings.MAX_CEILING
MIN_SESSION_CAP_MIN = child_settings.MIN_SESSION_CAP_MIN
MAX_SESSION_CAP_MIN = child_settings.MAX_SESSION_CAP_MIN


class SeedListField(serializers.ListField):
    """A short list of short strings, defaulting to `[]` and never `null`.

    Downstream code renders these into prompts without further checking, so
    `None` must be impossible rather than merely unlikely.
    """

    def __init__(self, **kwargs: Any) -> None:
        # `allow_blank` so a half-filled onboarding form is *cleaned* rather than
        # rejected: a parent who tabbed past an empty seed box should get a child
        # profile, not a validation error.
        kwargs.setdefault(
            "child", serializers.CharField(max_length=MAX_SEED_LENGTH, allow_blank=True)
        )
        kwargs.setdefault("max_length", MAX_SEED_COUNT)
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_empty", True)
        kwargs.setdefault("default", list)
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> list[str]:
        items: list[str] = super().to_internal_value(data)
        # Trim and drop blanks so `["dinosaurs", "  ", ""]` becomes one seed
        # rather than three, one of which prints as an empty bullet to the model.
        return [item.strip() for item in items if item.strip()]


class ChildSerializer(serializers.ModelSerializer):
    interests = SeedListField(help_text="Things this child loves, e.g. ['dinosaurs', 'the sea'].")
    fears = SeedListField(
        help_text="Things they are a little scared of. Handled gently, never used as a threat."
    )
    avoid_topics = SeedListField(
        help_text="Topics never to include. Copied verbatim into the safety layer."
    )

    intensity_ceiling = serializers.IntegerField(
        min_value=MIN_INTENSITY,
        max_value=MAX_INTENSITY,
        required=False,
        help_text="0 = gentlest, 3 = most adventurous. An int, never free text.",
    )
    session_cap_min = serializers.IntegerField(
        min_value=MIN_SESSION_CAP_MIN,
        max_value=MAX_SESSION_CAP_MIN,
        required=False,
        help_text="Minutes per sitting before the story starts winding down (§13.6).",
    )

    age = serializers.IntegerField(read_only=True)
    age_band = serializers.CharField(read_only=True)
    age_out_of_range = serializers.SerializerMethodField()

    class Meta:
        model = Child
        fields = [
            "id",
            "first_name",
            "birthdate",
            "locale",
            "pre_reader",
            "interests",
            "fears",
            "avoid_topics",
            "intensity_ceiling",
            "session_cap_min",
            "age",
            "age_band",
            "age_out_of_range",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    @staticmethod
    def _age_on(birthdate: datetime.date, today: datetime.date) -> int:
        had_birthday = (today.month, today.day) >= (birthdate.month, birthdate.day)
        return today.year - birthdate.year - (0 if had_birthday else 1)

    @extend_schema_field(serializers.BooleanField())
    def get_age_out_of_range(self, obj: Child) -> bool:
        """True when the child is outside the 3-7 design range.

        Deliberately a *flag*, not a rejection: the product is designed for 3-7,
        but a parent with a just-turned-eight-year-old should be told that
        plainly rather than blocked at the door, and QA needs to create profiles
        of any age.
        """
        return not (MIN_AGE <= obj.age <= MAX_AGE)

    def validate_birthdate(self, value: datetime.date) -> datetime.date:
        today = timezone.localdate()
        if value > today:
            raise serializers.ValidationError("Birthdate cannot be in the future.")
        if self._age_on(value, today) > 18:
            # Not an age-range opinion — a "this is not a child" guard, which
            # also catches the classic typo of entering the parent's birth year.
            raise serializers.ValidationError("Birthdate does not describe a child.")
        return value

    def validate_first_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("A first name (or nickname) is required.")
        return name


class AccessibilitySerializer(serializers.Serializer):
    """§14.6's flags, as a declared shape rather than a free JSON blob.

    Declared so the parent screen is generated from the schema and so an unknown
    key is a 400 rather than a setting that silently does nothing — a toggle a
    parent flips that has no effect is the exact failure §12.7 forbids.
    """

    captions = serializers.BooleanField(
        required=False,
        help_text="Show everything spoken as text on the child's screen.",
    )
    reduced_motion = serializers.BooleanField(
        required=False,
        help_text="Still scenes: transitions, breathing and flourishes are disabled.",
    )
    dyslexia_font = serializers.BooleanField(
        required=False, help_text="Render the child's story text in a dyslexia-friendly face."
    )
    high_contrast = serializers.BooleanField(
        required=False, help_text="Raise contrast on the child surface."
    )
    audio_volume = serializers.FloatField(
        required=False,
        min_value=child_settings.MIN_VOLUME,
        max_value=child_settings.MAX_VOLUME,
        help_text="Narration volume, 0.0 to 1.0.",
    )

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        """Refuse a key this serializer does not declare.

        DRF drops unknown fields silently, which is the right default almost
        everywhere and is wrong here. This blob is *merged* into a stored column,
        so a client sending `{"reduce_motion": true}` — a typo, an older build, a
        flag renamed on one side of the stack — would get a `200`, see nothing
        change, and have set nothing at all. That is precisely the "a toggle that
        does nothing" failure §12.7 forbids, and the only place it could hide.

        **`to_internal_value` rather than `validate`**, because this serializer is
        used *nested*. `self.initial_data` is only set when a serializer is
        constructed with `data=`, which a nested field never is — a `validate` that
        read it would raise `AttributeError` on the one path that matters, not
        reject the key.
        """
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError(
                    {name: "Not an accessibility setting we know about." for name in unknown}
                )
        return super().to_internal_value(data)


class ChildSettingsSerializer(serializers.ModelSerializer):
    """The parent's controls for one child (§7.13, §12.7).

    A serializer of its own rather than a subset of `ChildSerializer`, for two
    reasons that are really one. First, this is the write surface for the *safety*
    controls, and a PATCH that could also move `birthdate` would let the settings
    page silently change the age band every safety decision is keyed on. Second,
    the settings screen is its own page with its own schema, and generating a form
    from a serializer that also carries `created_at` and `age_out_of_range` means
    hand-maintaining the list of what to render.

    **Everything here is honored on the very next generation.** There is no cache
    to invalidate and no denormalised copy to update: the assembler, the classifier
    and the deterministic policy all read `profiles.settings.settings_for(child)`
    at the moment a beat is generated or graded, and the orchestrator asks the same
    function on every stream open. That is what makes "honored immediately" a
    property of the architecture rather than a promise about cache TTLs.
    """

    fears = SeedListField(
        help_text="Things they are a little scared of. Handled gently, never used as a threat."
    )
    avoid_topics = SeedListField(
        help_text="Topics never to include. Copied verbatim into the safety layer."
    )
    intensity_ceiling = serializers.IntegerField(
        min_value=MIN_INTENSITY,
        max_value=MAX_INTENSITY,
        required=False,
        help_text="0 = gentlest, 3 = most adventurous. Enforced in generation and in the guard.",
    )
    session_cap_min = serializers.IntegerField(
        min_value=MIN_SESSION_CAP_MIN,
        max_value=MAX_SESSION_CAP_MIN,
        required=False,
        help_text="Minutes per sitting, after which the story winds down to a warm close.",
    )
    # `allow_null` because clearing the window is how a parent says "any time", and
    # a control with no off switch is a trap.
    time_window_start = serializers.TimeField(
        required=False, allow_null=True, help_text="Local start of the daily window, or null."
    )
    time_window_end = serializers.TimeField(
        required=False, allow_null=True, help_text="Local end of the daily window, or null."
    )
    voice_on = serializers.BooleanField(required=False)
    text_on = serializers.BooleanField(required=False)
    accessibility = AccessibilitySerializer(required=False)

    class Meta:
        model = Child
        fields = [
            "id",
            "first_name",
            "fears",
            "avoid_topics",
            "intensity_ceiling",
            "session_cap_min",
            "time_window_start",
            "time_window_end",
            "voice_on",
            "text_on",
            "accessibility",
            "updated_at",
        ]
        read_only_fields = ["id", "first_name", "updated_at"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Reject a half-set window.

        A start with no end is not a window, and the two obvious readings of it —
        "from 16:00 until whenever" and "no window at all" — are opposite
        behaviours. Rather than pick one silently, this asks the parent. Equal ends
        *are* accepted and read as "any time" (`settings.TimeWindow`), because that
        is what a parent who dragged both handles together means.
        """
        # DRF types `self.instance` as `Any | Sequence[Any]`, because a serializer
        # constructed with `many=True` holds a list. This one is always bound to a
        # single `Child` — it is reached through `ChildSettingsView`, a
        # `RetrieveUpdateAPIView` — so the annotation says so rather than every
        # attribute access below reading as a possible list.
        stored = cast("Child | None", self.instance)
        start = attrs.get("time_window_start", stored.time_window_start if stored else None)
        end = attrs.get("time_window_end", stored.time_window_end if stored else None)
        if (start is None) != (end is None):
            raise serializers.ValidationError(
                {
                    "time_window_end": (
                        "A daily window needs both a start and an end. "
                        "Clear both to allow play at any time."
                    )
                }
            )
        return attrs

    def to_representation(self, instance: Child) -> dict[str, Any]:
        """Answer with the *normalised* settings, not the raw columns.

        So the parent screen shows what the safety layer will actually enforce. A
        page that echoed a stored ceiling of `9` back while the guard clamped it to
        `0` would be a dashboard lying about the one thing this product sells
        (§12.6), and the clamp is deliberately silent everywhere else.
        """
        data = super().to_representation(instance)
        controls = child_settings.settings_for(instance)
        data["fears"] = list(controls.fears)
        data["avoid_topics"] = list(controls.avoid_topics)
        data["intensity_ceiling"] = controls.intensity_ceiling
        data["session_cap_min"] = controls.session_cap_min
        data["accessibility"] = controls.accessibility.as_json()
        return data

    def update(self, instance: Child, validated_data: dict[str, Any]) -> Child:
        """Merge the accessibility blob rather than replacing it.

        A PATCH that sends one flag means one flag. Replacing the column would
        reset the other four to their defaults, which on this particular object
        means a parent turning on high contrast silently switching captions back on
        for a child who needs them off — the class of bug where a settings page
        undoes settings.
        """
        if (patch := validated_data.pop("accessibility", None)) is not None:
            merged = child_settings.Accessibility.from_json(instance.accessibility).as_json()
            instance.accessibility = {**merged, **patch}
            instance.save(update_fields=["accessibility", "updated_at"])
        return super().update(instance, validated_data)
