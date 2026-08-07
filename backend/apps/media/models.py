"""Generated media (PRODUCT.md §17.2 `media`, §16.6).

One row per stored asset — an illustration or a line of narration. The row is
the *pointer*: bytes live in object storage behind a CDN and are never held in
Postgres, never streamed by the app (§16.6).

The three hash columns are the product's main unit-economics lever (§22.4). An
illustration is fully determined by three things — the fixed art direction, the
characters in it, and the scene — so an identical triple can reuse an existing
render instead of paying an image provider again. They are indexed as a tuple
because that is how the cache asks the question, and never individually.
"""

from django.db import models

from apps.common.models import UUIDModel


class MediaType(models.TextChoices):
    IMAGE = "image", "Image"
    AUDIO = "audio", "Audio"


class SafetyStatus(models.TextChoices):
    """The verdict of the pre-store scan (§13.8).

    `PENDING` exists so "not scanned yet" can never be confused with "scanned
    and fine" — a row that reaches a child must say `PASSED`, and a column with
    no unscanned state would make a forgotten scan invisible.
    """

    PENDING = "pending", "Not yet scanned"
    PASSED = "passed", "Passed"
    FLAGGED = "flagged", "Flagged for review"
    BLOCKED = "blocked", "Blocked"


class Media(UUIDModel):
    child = models.ForeignKey(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="media",
    )
    type = models.CharField(max_length=16, choices=MediaType.choices)

    # CharField, not URLField: in development the media alias is the local disk
    # and this holds a relative path (`/media/…`), which URLField rejects. The
    # value is always what the client should fetch, whatever produced it.
    url = models.CharField(max_length=500)
    provider = models.CharField(max_length=60)

    prompt_hash = models.CharField(max_length=64, blank=True)
    style_hash = models.CharField(max_length=64, blank=True)
    character_hash = models.CharField(max_length=64, blank=True)
    scene_hash = models.CharField(max_length=64, blank=True)

    safety_status = models.CharField(
        max_length=16,
        choices=SafetyStatus.choices,
        default=SafetyStatus.PENDING,
    )
    bytes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The cache lookup, in the order the service asks it (§16.6).
            models.Index(
                fields=["style_hash", "character_hash", "scene_hash"],
                name="media_cache_tuple_idx",
            ),
            models.Index(fields=["child", "type"], name="media_child_type_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.type} {self.id} ({self.provider})"

    @property
    def is_servable(self) -> bool:
        """True when this asset may be shown to a child.

        Anything short of a clean scan is withheld. Read this rather than the
        raw column at call sites, so "flagged also means do not serve" is stated
        once instead of remembered everywhere.
        """
        return self.safety_status == SafetyStatus.PASSED
