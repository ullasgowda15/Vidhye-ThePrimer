"""Safety records (PRODUCT.md §17.4 `prompt_version` + `safety_incident`, §19.2).

Two tables, and they answer the two questions an incident review asks: *what
were we telling the model* and *what did the guards do about what came back*.

**`PromptVersion`** — system prompts are code that ships to a child, so they are
versioned, reviewed, and **never edited live**. Keeping them in a table rather
than a Python constant buys three things: an incident can be traced to the exact
text that produced it, a bad prompt can be rolled back without a deploy, and the
red-team suite can pin a version while a new one is evaluated.

Only the *immutable* layers live here. The per-child safety layer is generated
at request time from that child's age band, ceiling and avoid-list — it has no
version of its own because it has no author.

**`SafetyIncident`** — one row for every non-pass the pipeline makes (§13.2,
§13.11). Written on the guard's decision rather than on a human noticing, because
the rate at which the guards fire is the only measurable evidence that they are
working at all, and because "did this ever reach a child" must be answerable from
the database and not from an application log with a retention policy.
"""

from django.conf import settings
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


class Severity(models.TextChoices):
    """How much this incident matters, and therefore who has to look at it.

    Four rungs rather than a number because the boundaries are decisions, not
    measurements: `HIGH` and above is what `escalation.notify_parent` acts on,
    and `CRITICAL` is reserved for a signal about the *child* (§13.10) rather
    than about a generation — a model writing something unsafe is a defect we
    caught, and a child disclosing harm is a person who needs an adult.
    """

    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class Action(models.TextChoices):
    """What the pipeline actually did. The child-facing consequence, in one word."""

    SCRUBBED = "scrubbed", "Untrusted input neutralised"
    REWRITTEN = "rewritten", "Beat regenerated under constraint"
    BLOCKED = "blocked", "Beat replaced with a safe fallback"
    ESCALATED = "escalated", "De-escalated, logged, parent notified"
    SUBSTITUTED = "substituted", "Illustration replaced with safe stock art"
    DEGRADED = "degraded", "A guard could not run; the weaker check stood"


class SafetyIncident(UUIDModel):
    """One non-pass decision by the safety layer (§17.4 `safety_incident`).

    **`child` cascades; `adventure` and `beat` do not**, and the asymmetry is the
    design rather than an oversight.

    A family's right to erasure is absolute and §17.5 makes it one operation —
    "everything child-linked is deletable via `child_id` cascade" — so an incident
    goes when the child does, like every other row that points at them. There is
    no audit exception to a deletion request from a parent.

    A *beat*, on the other hand, is pruned aggressively while the family is still
    here (§17.5 again), and an incident that vanished with the beat it was about
    would mean the safety-trigger rate silently decays with the retention job.
    Those two nulls out and the incident stays.

    `detail` carries **no child text**: the guard records *what pattern fired*,
    never the sentence that fired it, so this table cannot become a second copy of
    the thing we refused to show anyone (§13.8, `apps.safety.incidents`).
    """

    child = models.ForeignKey(
        "profiles.Child",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="safety_incidents",
    )
    adventure = models.ForeignKey(
        "story.Adventure",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="safety_incidents",
    )
    beat = models.ForeignKey(
        "story.Beat",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="safety_incidents",
    )

    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.LOW)
    #: What kind of problem — `prompt_injection`, `violence`, `avoid_topic`, …
    #: A free CharField rather than choices: the category list grows with every
    #: red-team finding, and a migration per finding is friction on the one
    #: process that must never have any.
    category = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)
    action_taken = models.CharField(max_length=16, choices=Action.choices)
    escalated = models.BooleanField(default=False)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_safety_incidents",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The queue's own query: newest unreviewed, worst first. The admin
            # filters on severity and on `escalated`, and both are read far more
            # often than they are written.
            models.Index(fields=["-created_at"], name="incident_recent_idx"),
            models.Index(fields=["severity", "-created_at"], name="incident_severity_idx"),
            models.Index(fields=["escalated", "-created_at"], name="incident_escalated_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.severity}/{self.category} → {self.action_taken}"

    @property
    def is_reviewed(self) -> bool:
        return self.reviewed_at is not None
