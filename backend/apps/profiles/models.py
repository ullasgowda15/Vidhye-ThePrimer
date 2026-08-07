"""The child profile (PRODUCT.md §17.2 `child`).

These fields are the **cold start** only. The rich personalization lives in the
derived interest graph and memory (§11); what the parent types at onboarding
just gets the first story pointed in the right direction.

`avoid_topics` / `fears` / `intensity_ceiling` are safety inputs, not
preferences — the safety layer (Day 7) reads them on every generation.
"""

from django.conf import settings
from django.db import models

from apps.common.models import UUIDModel


class Child(UUIDModel):
    parent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="children",
    )
    first_name = models.CharField(max_length=80)
    birthdate = models.DateField()
    locale = models.CharField(max_length=16, default="en-US")

    # Seed personalization (JSON, not tables — these are unstructured hints the
    # story engine reads, never joined on).
    interests = models.JSONField(default=list, blank=True)
    fears = models.JSONField(default=list, blank=True)
    avoid_topics = models.JSONField(default=list, blank=True)

    # Safety + wellbeing controls (§13.6, §13.7).
    intensity_ceiling = models.SmallIntegerField(default=2)
    session_cap_min = models.SmallIntegerField(default=15)
    pre_reader = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "children"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["parent"])]

    def __str__(self) -> str:
        return self.first_name
