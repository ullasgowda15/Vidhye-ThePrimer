"""Child profile serialization (PRODUCT.md §4.2, §17.2).

The seed fields are the only personalization the engine has on day one, and
three of them (`fears`, `avoid_topics`, `intensity_ceiling`) are *safety*
inputs the prompt assembler reads verbatim. So validation here is not tidiness
— it is the boundary that keeps a 400-character blob or a `null` out of a
safety layer.
"""

import datetime
from typing import Any

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.profiles.models import Child

# A love or a fear is a noun phrase ("dinosaurs", "the dark"), not a paragraph.
# Bounding both length and count bounds the safety layer's token cost too.
MAX_SEED_LENGTH = 60
MAX_SEED_COUNT = 10

MIN_AGE = 3
MAX_AGE = 7
MIN_INTENSITY = 0
MAX_INTENSITY = 3
MIN_SESSION_CAP_MIN = 5
MAX_SESSION_CAP_MIN = 30


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
