"""The companion (PRODUCT.md §17.2 `companion`, §9).

One child, one companion at MVP. `visual_descriptor` is the canonical
appearance string appended to every illustration prompt — it is what makes Pip
the same Pip in every picture (§8.2). Inconsistency here reads as uncanny to a
four-year-old and breaks attachment instantly, so it is persisted rather than
re-invented per story.
"""

from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.common.models import UUIDModel

#: The canonical reference labels (§9.6). `portrait` is the anchor every other
#: illustration is drawn against; the five expressions are pre-rendered once and
#: reused forever, which is simultaneously the speed, cost and consistency win.
PORTRAIT = "portrait"
CANONICAL_EXPRESSIONS = ("happy", "curious", "proud", "gentle_concern", "sleepy")
GREETING_AUDIO = "greeting_audio"


class AssetStatus(models.TextChoices):
    """How far the one-time asset generation has got.

    A separate column rather than "are the ids empty?" because *partial* is a
    real, distinct state: a companion with a portrait but only three expressions
    is usable today and worth retrying later, and the child-facing greeting has
    to know the difference between "not yet" and "never".
    """

    PENDING = "pending", "Not generated yet"
    READY = "ready", "Complete"
    PARTIAL = "partial", "Some assets missing"
    FAILED = "failed", "Generation failed"


class Companion(UUIDModel):
    child = models.OneToOneField(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="companion",
    )
    archetype = models.CharField(max_length=60)
    name = models.CharField(max_length=80)

    palette = models.JSONField(default=dict, blank=True)
    personality = models.JSONField(default=dict, blank=True)
    # Nullable, not blank-by-default: "no voice assigned yet" is a distinct
    # state from "assigned the empty voice", and the TTS adapter branches on it.
    voice_id = models.CharField(max_length=120, null=True, blank=True)  # noqa: DJ001
    visual_descriptor = models.TextField(blank=True)
    evolution_state = models.JSONField(default=dict, blank=True)

    # §17.2's `reference_asset_ids (uuid[])` — every Media row that belongs to
    # this companion's identity, in one flat list for the illustration pipeline.
    reference_asset_ids = ArrayField(models.UUIDField(), default=list, blank=True)

    # Which reference is which. The flat array says "these six pictures are
    # Pip"; this says "and *that* one is Pip looking proud", which is what a
    # story beat actually needs when it wants an expression. Written together
    # with `reference_asset_ids` by `apps.companion.service`, never separately.
    reference_manifest = models.JSONField(default=dict, blank=True)

    assets_status = models.CharField(
        max_length=16,
        choices=AssetStatus.choices,
        default=AssetStatus.PENDING,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.archetype})"

    @property
    def assets_ready(self) -> bool:
        """True when there is enough of a companion to meet a child.

        Deliberately "portrait plus a voice", not "all six expressions": the
        imprint moment (§4.3) needs a face and a hello, and holding the child at
        a waiting screen because the *sleepy* expression is still rendering
        would be optimising for completeness over the thing that matters.
        """
        return bool(
            self.reference_manifest.get(PORTRAIT) and self.reference_manifest.get(GREETING_AUDIO)
        )
