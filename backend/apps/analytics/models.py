"""Analytics, in two halves that must never meet (PRODUCT.md §5.6, §12.1, §17.4).

§5.6 puts a hard boundary down the middle of this app, and the models are where
it is easiest to see:

* **`SessionReport`** is the *parent-facing* half — a curated, child-appropriate
  recap, written once, read on the dashboard. Everything in it is chosen to be
  read by the child's parent.
* **`Event` and `GenerationCost`** are the *product/ops* half — raw behavioural
  telemetry and per-generation spend. Neither is ever serialised onto a parent
  route; both are read only by staff (`apps.analytics.internal`).

Nothing in this file enforces that on its own — a boundary that is only a
docstring is a boundary — so it is also enforced twice in code:
`apps.analytics.internal` is staff-only, and
`tests/test_analytics_pipeline.py::TestTheSeparationIsStructural` asserts that no
parent-facing module imports the product-analytics modules.

---

**What the parent is shown, as a durable record (§12.1, §17.3).**

A `SessionReport` is the short, warm, specific recap §12.1 asks for,
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


class Event(UUIDModel):
    """One thing that happened, as append-only telemetry (§17.4, §5.6).

    The product/ops half of §5.6. Every metric in §22.3 — activation, day-2
    return, W1 retention, completion rate, adventures per child per week — is
    computed from these rows and from nothing else (`apps.analytics.metrics`).

    **Append-only is a property of the writer, not of Postgres.** There is no
    update path: `events.track` only ever calls `create`, the admin registration
    is read-only in every field, and nothing in the codebase writes a second time
    to a row it has written. Postgres would happily let a migration rewrite
    history; the point is that no running code can.

    **`props` is deliberately small and deliberately not free text.** §13.8 asks
    for data minimisation on raw child behavioural events, and the single easiest
    way to breach it is a `props` blob that quietly carries what a child said.
    `events.track` therefore accepts only scalars, truncates strings hard, and
    drops anything else — so a caller who passes a narration string gets a
    truncated one they will notice in the dashboard rather than a silent archive
    of a four-year-old's sentences. What belongs in here is shape (*a choice was
    made, it was the second card, it took 4 seconds*), never content.

    **`child` and `parent` CASCADE; `session` does not.** The two halves of that
    sentence answer different questions and it is worth being explicit about
    which.

    A child being deleted is §13.7, and §17.5 makes it one operation: *everything*
    child-linked goes with the `child_id` cascade. The first draft of this table
    used `SET_NULL` and kept a bare `child_key`, on the reasoning that a
    de-identified row would let a September completion rate stay the number it was
    after a family left in October. Two existing invariant tests refused it —
    `test_children.py::test_every_fk_to_child_cascades_at_the_model_level` and its
    sibling in `test_privacy.py` — and they were right to. The privacy disclosure
    a parent reads says "when you delete a child or your account, the data goes at
    once", and "except for the analytics" is not a footnote we get to add to that
    sentence. Metric stability is a real cost and it is the smaller one; the
    warehouse export (`manage.py drain_events`) is where a historical series
    lives, and it holds no name, no birthdate and no free text.

    A *session* being pruned is not a delete — §17.5 discards raw beats long
    before the derived meaning stops mattering — so `session` is `SET_NULL` with
    `session_key` kept alongside, exactly as `SessionReport` does it. That is what
    keeps cost-per-session computable for a week whose sittings have aged out.
    """

    #: The names `events.track` will accept. A closed set on purpose: a typo in a
    #: metric's event name is a metric that silently reads zero, and the metrics
    #: module resolves its queries through these constants rather than through
    #: string literals, so a rename cannot leave half the codebase behind.
    PARENT_SIGNED_UP = "parent.signed_up"
    PARENT_CONSENT_RECORDED = "parent.consent_recorded"
    CHILD_CREATED = "child.created"
    COMPANION_CREATED = "companion.created"
    ONBOARDING_COMPLETED = "onboarding.completed"
    ADVENTURE_STARTED = "adventure.started"
    ADVENTURE_COMPLETED = "adventure.completed"
    BEAT_DELIVERED = "beat.delivered"
    CHOICE_MADE = "choice.made"
    SESSION_ENDED = "session.ended"
    SAFETY_INCIDENT = "safety.incident"

    NAMES = frozenset(
        {
            PARENT_SIGNED_UP,
            PARENT_CONSENT_RECORDED,
            CHILD_CREATED,
            COMPANION_CREATED,
            ONBOARDING_COMPLETED,
            ADVENTURE_STARTED,
            ADVENTURE_COMPLETED,
            BEAT_DELIVERED,
            CHOICE_MADE,
            SESSION_ENDED,
            SAFETY_INCIDENT,
        }
    )

    #: Nullable because plenty of events are about an account and not a child
    #: (`parent.signed_up`), never because the row outlives the child.
    child = models.ForeignKey(
        "profiles.Child",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="events",
    )

    parent = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="events",
    )

    session = models.ForeignKey(
        "story.PlaySession",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    session_key = models.UUIDField(null=True, blank=True)

    name = models.CharField(max_length=64)
    props = models.JSONField(default=dict, blank=True)

    #: The moment, not the row's creation. They are the same today, and they stop
    #: being the same the first time an event is replayed from a client buffer.
    ts = models.DateTimeField()

    #: When this row was drained to the warehouse export
    #: (`manage.py drain_events`). Null means "not yet exported", which is the one
    #: query the drain makes. It is on the row rather than in a cursor table
    #: because a cursor that jumps a gap loses events silently, and a null column
    #: cannot.
    exported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-ts"]
        indexes = [
            # The three shapes every query in `metrics.py` has: a funnel step
            # across everyone, one child's history, and the drain's backlog.
            models.Index(fields=["name", "ts"], name="event_name_ts"),
            models.Index(fields=["child", "ts"], name="event_child_ts"),
            models.Index(fields=["exported_at"], name="event_exported"),
        ]

    def __str__(self) -> str:
        # The name and the moment. No `props`, for the reason `SessionReport` keeps
        # its highlight out of `__str__`: this reaches log lines (§13.8).
        return f"{self.name} @ {self.ts:%Y-%m-%d %H:%M:%S}"


class CostKind(models.TextChoices):
    """The three things a generation can spend money on (§19.9, §22.4)."""

    LLM = "llm", "Language model"
    IMAGE = "image", "Illustration"
    TTS = "tts", "Voice"


class GenerationCost(UUIDModel):
    """One traced generation span, with what it cost (§19.9, §22.4).

    §19.9's requirement is one sentence — "every generation is traced with its cost
    so unit economics are always visible" — and this table is it. One row per real
    provider call, written by `apps.analytics.cost.record` at the three seams where
    money is actually spent: `ai.router.ModelRouter.generate_structured`,
    `media.service._get_or_generate` and `media.adapters.embedding.embed`. A caller
    cannot opt out of it, because a caller never calls it.

    **An embedding is a `kind=llm` row with `operation="embedding"`**, not a kind of
    its own: it is a token-billed model call, `cost._by_operation` keeps it
    separable, and a fourth `CostKind` would have meant every row written before it
    disagreeing with every row written after.

    **A cache hit writes nothing, and that is the design.** The point of the media
    cache is that the second identical picture is free; a zero-cost span for it
    would make `cost_per_session` fall for a reason unrelated to what a session
    costs, and would double the table for no information. The hit counters in
    `media.service.STATS` are where hit-rate lives.

    **`priced` is what stops this table from lying.** A model with no entry in
    `apps.analytics.pricing` gets `cost_usd = 0` and `priced = False`, and every
    aggregate reports the unpriced count next to the total. Silently pricing an
    unknown model at zero would mean the day we swap in a paid route, cost/session
    reads 0.00 and looks like good news. Free routes are `priced=True` with a zero
    rate, which is a different claim and is stored as one.

    **Attribution is by session, and it is best-effort by design.** The unit §22.4
    asks for is cost *per session*, so `session_key` is what everything aggregates
    on. Media is generated in a Celery worker off the request that made it, so the
    attribution rides a context (`cost.trace`) rather than an argument — and a span
    that arrives with no context is written anyway, with a null session, and
    counted separately. An unattributed span is a real cost and hiding it would
    understate the metric; what it must not do is silently land in some other
    child's session.

    **`child` cascades and `session` does not**, for the reasons spelled out on
    `Event`. It costs us a real thing here — a family leaving rewrites the spend
    history a pricing decision was judged against — and it is still the right way
    round: §13.7's delete is not allowed to make an exception for the table that
    happens to be convenient.
    """

    child = models.ForeignKey(
        "profiles.Child",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="generation_costs",
    )

    session = models.ForeignKey(
        "story.PlaySession",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generation_costs",
    )
    #: The denominator of `cost_per_session`. Null means the span could not be
    #: attributed to a sitting — see the class docstring.
    session_key = models.UUIDField(null=True, blank=True)

    kind = models.CharField(max_length=8, choices=CostKind.choices)
    #: What was being generated: `beat`, `plan`, `safety`, `memories`, `greeting`,
    #: `highlight`, `illustration`, `narration`. The span name, in Langfuse terms.
    #: Free-form rather than a choices field because the set grows with every new
    #: generation and a migration is the wrong ceremony for naming one.
    operation = models.CharField(max_length=32)

    provider = models.CharField(max_length=60)
    model = models.CharField(max_length=120, blank=True)

    #: What was consumed, in the units the price table charges for:
    #: `{"input_tokens": n, "output_tokens": n}` for an LLM, `{"images": 1}` for a
    #: picture, `{"characters": n}` for a line of speech. Stored rather than
    #: derived so a re-price against a corrected table is arithmetic on this
    #: column and not a re-run of the week.
    units = models.JSONField(default=dict, blank=True)

    #: US dollars. `Decimal` and not a float: these are summed across thousands of
    #: rows, and a float sum of many small numbers is how a cost dashboard drifts.
    #: Six places because a beat on a cheap model costs a fraction of a cent.
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    #: False when no price is known for this provider/model — see the docstring.
    priced = models.BooleanField(default=False)

    latency_ms = models.IntegerField(null=True, blank=True)
    occurred_at = models.DateTimeField()

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["occurred_at"], name="cost_occurred"),
            models.Index(fields=["session_key"], name="cost_session"),
            models.Index(fields=["kind", "occurred_at"], name="cost_kind_occurred"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.operation} ${self.cost_usd}"
