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

    #: The daily window this child may play in (§13.6, §7.13), as local wall-clock
    #: times. Null/null means "any time", which is the default because a window
    #: nobody set must not lock a family out of their own product.
    #:
    #: Two nullable columns rather than one JSON blob, because the pair is read on
    #: the story-start path on every request and is the one setting whose *absence*
    #: has to be unambiguous. A window that wraps past midnight (21:00 to 07:00) is
    #: legitimate and is handled in `profiles.settings.TimeWindow`.
    time_window_start = models.TimeField(null=True, blank=True)
    time_window_end = models.TimeField(null=True, blank=True)

    #: Presentation controls (§7.13). They never touch generation or the safety
    #: guard — they decide how the beat that was already generated is *delivered*.
    voice_on = models.BooleanField(default=True)
    text_on = models.BooleanField(default=True)

    #: May we keep this child's voice? (Docs/Build/Reading-Integration.md §5.2.)
    #:
    #: Off, and off is the answer that needs no defending. When a child reads a
    #: line aloud the recording is normally never written to disk at all — the
    #: browser sends it, we count that reading happened, and the bytes go
    #: nowhere. This opt-in is the one exception, and it exists for one reason:
    #: every threshold in the reading detector was tuned against an adult
    #: imitating a five-year-old, and there is no way to fix that without
    #: recordings of the real thing.
    #:
    #: A parent grants it deliberately, per child, and can withdraw it — at which
    #: point `apps.reading.service` stops writing and the existing corpus files
    #: are removed with the rest of the child's media on deletion. It is not
    #: inherited, not defaulted on by a plan, and not bundled into another
    #: setting: consent that arrives as a side effect of agreeing to something
    #: else is not consent.
    reading_audio_consent = models.BooleanField(default=False)

    #: §14.6's accessibility flags: `captions`, `reduced_motion`, `dyslexia_font`,
    #: `high_contrast`, `audio_volume`. JSON rather than five columns because they
    #: are only ever read as one set, by one surface, and the list will grow —
    #: `profiles.settings.Accessibility` is what gives the blob a shape, defaults
    #: and validation, so nothing downstream reads a raw key.
    accessibility = models.JSONField(default=dict, blank=True)

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
