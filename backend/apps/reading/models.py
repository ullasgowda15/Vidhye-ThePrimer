"""What happened when a child read a line aloud (Docs/Build/Reading-Integration.md §8).

**Phase 1 records that reading happened, and nothing about how it went.** There
is no verdict column here, no score, no per-word outcome — not because they are
hard, but because the detector has never heard a child and every threshold in it
was tuned against an adult imitating one. A row that said `correct=False` today
would be a guess with a schema around it, and the first thing anyone would do
with a stored guess is show it to a parent.

So this table answers two questions and stops: *did this child read aloud, and
where is the recording if they consented to us keeping it.* Phase 2 adds the
verdict, against thresholds re-tuned on the corpus this table indexes.

`passage_text` is frozen into the row rather than joined from the curated bank.
The bank is code and code changes; a corpus entry whose text is looked up at read
time would silently start describing a different sentence from the one the child
actually saw, which is the one property the whole corpus depends on.
"""

from __future__ import annotations

from django.db import models

from apps.common.models import UUIDModel


class ReadingAttempt(UUIDModel):
    """One invitation to read, and what came back.

    Rows exist for attempts that produced no audio too — a child who was offered
    a line and said nothing is a fact worth having, and it is the difference
    between "they will not do this" and "we never asked".
    """

    child = models.ForeignKey(
        "profiles.Child", on_delete=models.CASCADE, related_name="reading_attempts"
    )
    #: Null when the reading moment happened outside a tracked sitting. Kept
    #: nullable rather than required so a session that ended first cannot lose
    #: the attempt.
    session = models.ForeignKey(
        "story.PlaySession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reading_attempts",
    )

    #: The curated passage's own id (`engine.content.passages`), for grouping the
    #: corpus by sentence, and the text as the child saw it.
    passage_id = models.CharField(max_length=40)
    passage_text = models.TextField()

    #: Age in whole years at the moment of reading. Denormalised on purpose: the
    #: corpus is for tuning a detector against real child speech, and "how old
    #: was this speaker" is the single most useful axis in it. Derived from
    #: `birthdate` at read time, so a row written last year still says five.
    child_age = models.PositiveSmallIntegerField()

    #: Whether the child's device sent us any audio at all. False covers a child
    #: who declined, went quiet, or whose microphone was refused — all different
    #: from a child who was never invited, which is simply the absence of a row.
    spoke = models.BooleanField(default=False)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    #: Consent, and the path only when it was given.
    #:
    #: Audio is not written at all unless a grown-up has opted in
    #: (`Child.reading_audio_consent`). Vidhye wrote the file and deleted it after
    #: analysis; here there is no analysis yet, so the safer shape is available:
    #: never create the file. An empty `audio_path` on a row is therefore a fact
    #: about consent, not a deletion that may or may not have run.
    audio_kept = models.BooleanField(default=False)
    audio_path = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["child", "-created_at"]),
            models.Index(fields=["passage_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.child.first_name} read {self.passage_id}"
