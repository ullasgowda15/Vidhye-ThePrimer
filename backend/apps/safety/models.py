"""Prompt governance (PRODUCT.md §17.4 `prompt_version`, §19.2).

System prompts are code that ships to a child, so they are versioned, reviewed,
and **never edited live**. Keeping them in a table rather than a Python constant
buys three things: an incident can be traced to the exact text that produced it,
a bad prompt can be rolled back without a deploy, and the red-team suite can pin
a version while a new one is evaluated.

Only the *immutable* layers live here. The per-child safety layer is generated
at request time from that child's age band, ceiling and avoid-list — it has no
version of its own because it has no author.
"""

from django.db import models

from apps.common.models import UUIDModel

# Layer names the assembler knows how to look up. Adding one is a deliberate act.
SYSTEM_IDENTITY = "system_identity"


class PromptVersion(UUIDModel):
    name = models.CharField(max_length=64, help_text="Layer this template supplies.")
    version = models.CharField(max_length=32, help_text="Semver-ish, e.g. '1.0.0'.")
    template = models.TextField()
    model_config = models.JSONField(default=dict, blank=True)
    created_by = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=False)

    class Meta:
        ordering = ["name", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["name", "version"], name="uniq_prompt_name_version"),
            # At most one active version per layer. Enforced in the database
            # because "which prompt is live?" must never have two answers — a
            # race between two admins would otherwise make generation
            # nondeterministic in a way no test would catch.
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(active=True),
                name="uniq_active_prompt_per_name",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.version}{' (active)' if self.active else ''}"
