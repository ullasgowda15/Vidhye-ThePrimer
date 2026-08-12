"""What the parent is shown, as a durable record (PRODUCT.md §12.1, §17.3).

One table. A `SessionReport` is the short, warm, specific recap §12.1 asks for,
written once after a sitting ends and never rewritten.

**Why it is stored rather than computed on read.** Three reasons, in order of how
much they matter.

*Honesty over time.* §12.1's report is a claim about one afternoon. Recomputing it
next month against a changed catalog, a changed prompt or a changed milestone
rollup would quietly rewrite what a parent was told — and the whole product
promise here is that the record is true (§12.6). A stored row is a record; a
recomputed one is an opinion that keeps changing its mind.

*The raw rows are disposable.* §17.5 prunes beats and choices aggressively and
keeps derived meaning. This is derived meaning, and it is the last thing that
should go when the transcript does. That is also why `session` is nullable while
`session_key` is not — see those fields.

*It costs a model call.* The highlight is generated (`apps.analytics.prompts`), and
a report regenerated on every dashboard refresh would pay for that every time.

**The weekly report is deliberately *not* a table.** It is an aggregation of these
rows plus the live skill model and interest graph, computed on read
(`reports.generate_weekly_report`). A stored weekly row would be a second thing
that can disagree with the sessions it summarises, and a second idempotency
problem, to save a query that is already cheap.

**No mastery number is stored here.** `skills` records what the child was *asked*
and what they *did* — the evidence, which is a fact about the sitting. What we
currently believe they can do lives in `learning.LearningProgress` and is read live
(§10.2): one home for the skill model, and a report that cannot drift from it.
"""

from __future__ import annotations

from django.db import models

from apps.common.models import UUIDModel


class HighlightSource(models.TextChoices):
    """Where the one specific sentence in a report came from.

    Written down, and *shown* in the API, because §13.9 makes fabrication the
    named failure of this surface. A parent-facing sentence that a model wrote is
    a different kind of claim from one assembled out of columns, and "which of
    those am I reading" must be answerable without diffing prose.

    It is also the only way to watch the grounding gate in production: a sudden
    fall in `model` means the check is rejecting everything, and a `quiet` rate of
    zero across a week means it is rejecting nothing.
    """

    MODEL = "model", "Written by the model, checked against the session"
    DATA = "data", "Assembled from what the child actually did"
    QUIET = "quiet", "Nothing notable happened, and we said so"


class SessionReport(UUIDModel):
    """One sitting, as its parent reads it (§12.1)."""

    child = models.ForeignKey(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="session_reports",
    )

    #: The sitting this is about. SET_NULL for the reason every FK into `story`
    #: is: §17.5 prunes the sitting long before the parent stops wanting to read
    #: what happened in it.
    session = models.OneToOneField(
        "story.PlaySession",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="report",
    )

    #: The sitting's id, kept as a plain column.
    #:
    #: Two jobs, and the second is why it is not merely a convenience. It is the
    #: URL key — `GET /children/{id}/reports/session/{sessionId}` has to keep
    #: answering after `session` has gone null — and it is the idempotency gate: a
    #: redelivered generation must find the report it already wrote, and it cannot
    #: do that through an FK that a retention job is allowed to clear.
    session_key = models.UUIDField(unique=True)

    #: When the child played, copied off the sitting. Denormalised for the same
    #: reason as `session_key`: the weekly report buckets by week, and it must not
    #: stop being able to once the sitting rows are gone.
    occurred_at = models.DateTimeField()

    duration_s = models.IntegerField(default=0)
    #: `completed` / `cap` / `exit` (§13.6). A wellbeing signal, reported as it is:
    #: a child who wandered off after two beats is not a failed session, and a
    #: report that quietly rounded it up to "completed" would be the first lie.
    ended_reason = models.CharField(max_length=16, blank=True)
    beats = models.IntegerField(default=0)

    # --- where they went (§12.1) -------------------------------------------
    adventure_title = models.CharField(max_length=200, blank=True)
    world_name = models.CharField(max_length=120, blank=True)
    place_name = models.CharField(max_length=120, blank=True)

    #: The adventure this recap is of, for the transparency link. Nullable and
    #: SET_NULL for the same reason `session` is.
    adventure = models.ForeignKey(
        "story.Adventure",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    #: Kept alongside the FK so "read the actual story" survives the FK going null
    #: as a *dead* link rather than as a missing one — the parent is told the
    #: transcript has aged out instead of the row silently losing its button.
    adventure_key = models.UUIDField(null=True, blank=True)

    # --- the key choice (§12.1) --------------------------------------------
    #: The branch that mattered, in the child's terms: the label they took and the
    #: consequence the story gave it. Empty when the sitting had no real branch,
    #: which is a true thing to say about a two-beat session.
    key_choice = models.TextField(blank=True)

    # --- the objective, and how it went (§12.1, §10.5) ---------------------
    objective_code = models.CharField(max_length=64, blank=True)
    #: The milestone this rolls up to, in §10.6's plain language — "Counting and
    #: quantity", never `count_to_5`. Stored rather than joined so the report keeps
    #: saying what the parent was told even after the catalog is revised.
    milestone_name = models.CharField(max_length=120, blank=True)
    how_it_went = models.TextField(blank=True)

    # --- the highlight (§12.1, §13.9) --------------------------------------
    highlight = models.TextField(blank=True)
    highlight_source = models.CharField(
        max_length=8,
        choices=HighlightSource.choices,
        default=HighlightSource.QUIET,
    )

    #: What the child was asked and what they did, per objective:
    #: `[{code, domain, milestone, asks, unaided, assisted, retried}]`.
    #:
    #: Evidence, not belief. Every number in here is a count of things that
    #: happened in this sitting, so it is true forever; the running estimate those
    #: counts fed into lives in `learning.LearningProgress` and is read live.
    skills = models.JSONField(default=list, blank=True)

    #: The child's top interests as of this sitting: `[{topic, weight}]`.
    #:
    #: A snapshot, and the only reason there is one. `memory.InterestEdge` holds a
    #: weight and the date it was last reinforced — enough to say what a child
    #: loves *now*, and not enough to say what they loved in March. §11.7 promises
    #: the parent "this month: space, then dinosaurs, now ocean", which is a
    #: sentence about the past, so the past has to be written down somewhere. This
    #: is that somewhere, and it costs one small list per sitting.
    interests = models.JSONField(default=list, blank=True)

    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            # The two queries the dashboard makes: "this child's latest report"
            # and "this child's reports for the week of…".
            models.Index(fields=["child", "-occurred_at"], name="report_child_occurred"),
        ]

    def __str__(self) -> str:
        # No highlight text. Like `Memory.__str__`, this ends up in log lines and
        # admin change messages, and the one sentence in here is about a real
        # child's afternoon (§13.8).
        return f"report for {self.child_id} @ {self.occurred_at:%Y-%m-%d}"
