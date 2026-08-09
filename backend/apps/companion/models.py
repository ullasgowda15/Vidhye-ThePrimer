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


class Character(UUIDModel):
    """One member of the recurring cast (§8.2, §17.2 `character`).

    Everyone in a child's world who is *not* the companion and who might come
    back: the frog they helped in beat two, the lantern-keeper at the bridge.
    §8.2 is explicit that these are **first-class persisted entities, not
    re-invented per story** — and the reason is the same as the companion's. A
    creature that looks different the second time it appears is not the same
    creature, and a four-year-old reads that as uncanny rather than as a
    rendering artefact.

    So the row exists to answer one question at illustration time: *have we drawn
    this before, and what did it look like?* `visual_descriptor` is the answer and
    it is written once, by the beat that introduced the character
    (`ai.schemas.Scene.new_characters`), then never regenerated — regenerating it
    is precisely how a character stops being itself.

    Scoped to the child rather than global. Two children who both meet "a frog"
    have met different frogs, and one family's cast must never leak into another's
    pictures (§17.5).

    What this is *not* yet: memory. §11's entity graph, salience decay and
    cross-adventure retrieval are Days 8-9. `salience` is here because §17.2 names
    it and because a column added later cannot be backfilled with the history it
    was meant to record — nothing reads it today.
    """

    child = models.ForeignKey(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="characters",
    )

    #: The stable id a beat refers to this character by — the slug of whatever the
    #: model put in `scene.characters_present`. It is the join between a generated
    #: beat and this row, which is why it is constrained unique per child rather
    #: than left as a label: two rows answering to "frog" would make "which frog"
    #: unanswerable at exactly the moment the picture is being drawn.
    key = models.CharField(max_length=64)
    name = models.CharField(max_length=80)
    role = models.CharField(max_length=60, blank=True)
    personality = models.JSONField(default=dict, blank=True)

    #: The canonical appearance, in the same register as `Companion.visual_descriptor`
    #: and used the same way: appended to every illustration prompt this character
    #: appears in (`media.prompts.CharacterRef`).
    visual_descriptor = models.TextField(blank=True)

    #: §17.2's `reference_asset_ids`. For a companion these are purpose-rendered
    #: portraits; for a recurring character it is the picture that *established* the
    #: look — the beat illustration they were introduced in — recorded rather than
    #: re-rendered, because a dedicated portrait per passing frog is a provider call
    #: per passing frog (§22.4).
    reference_asset_ids = ArrayField(models.UUIDField(), default=list, blank=True)

    # SET_NULL for the same reason `Adventure.session` is: the character outlives
    # the story that introduced them, which is the entire point of the table.
    first_seen_adventure = models.ForeignKey(
        "story.Adventure",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    salience = models.FloatField(default=0.0)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["child", "key"], name="character_child_key_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.key})"
