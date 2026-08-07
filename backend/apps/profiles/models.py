"""The child profile (PRODUCT.md §17.2 `child`).

These fields are the **cold start** only. The rich personalization lives in the
derived interest graph and memory (§11); what the parent types at onboarding
just gets the first story pointed in the right direction.

`avoid_topics` / `fears` / `intensity_ceiling` are safety inputs, not
preferences — the safety layer (Day 7) reads them on every generation.
"""

import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import UUIDModel

# Two bands, not five. The split is where narrative capability actually changes
# — sentence length, whether suspense can survive a page turn — and every extra
# band is another combination the safety evals have to cover (§19.2).
AGE_BAND_YOUNGER = "3-4"
AGE_BAND_OLDER = "5-7"


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

    def age_at(self, on: datetime.date) -> int:
        """Whole years old on `on`. Derived, never stored — a stored age is wrong
        by up to a year the day after it's written."""
        had_birthday = (on.month, on.day) >= (self.birthdate.month, self.birthdate.day)
        return on.year - self.birthdate.year - (0 if had_birthday else 1)

    @property
    def age(self) -> int:
        return self.age_at(timezone.localdate())

    @property
    def age_band(self) -> str:
        """The safety/narration band (§17.2 `age_band (derived)`).

        Anything below the younger bound also lands in `3-4`: the bands only
        ever loosen guidance as they go up, so an out-of-range age falling to
        the most conservative band is the safe failure.
        """
        return AGE_BAND_YOUNGER if self.age <= 4 else AGE_BAND_OLDER
