"""Play session (PRODUCT.md §17.4 `session`).

A session is one sitting at the app; an *adventure* (Day 4) is the story told
inside it. They are separate because a session can contain zero or several
adventures, and because `ended_reason` is a wellbeing signal we report to
parents (§13.6) independent of narrative completion.
"""

from django.db import models

from apps.common.models import UUIDModel


class PlaySession(UUIDModel):
    class EndedReason(models.TextChoices):
        COMPLETED = "completed", "Completed"
        CAP = "cap", "Session cap reached"
        EXIT = "exit", "Child exited"

    child = models.ForeignKey(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_s = models.IntegerField(null=True, blank=True)
    # Both are nullable rather than blank-defaulted: an in-flight session has no
    # end reason yet, and "device unknown" must not be confused with a device
    # literally named "". Analytics counts on that distinction.
    device = models.CharField(max_length=60, null=True, blank=True)  # noqa: DJ001
    ended_reason = models.CharField(  # noqa: DJ001
        max_length=16,
        choices=EndedReason.choices,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["child", "-started_at"])]

    def __str__(self) -> str:
        return f"{self.child.first_name} @ {self.started_at:%Y-%m-%d %H:%M}"
