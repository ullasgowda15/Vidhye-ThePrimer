"""The companion (PRODUCT.md §17.2 `companion`, §9).

One child, one companion at MVP. `visual_descriptor` is the canonical
appearance string appended to every illustration prompt — it is what makes Pip
the same Pip in every picture (§8.2). Inconsistency here reads as uncanny to a
four-year-old and breaks attachment instantly, so it is persisted rather than
re-invented per story.
"""

from django.db import models

from apps.common.models import UUIDModel


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

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.archetype})"
