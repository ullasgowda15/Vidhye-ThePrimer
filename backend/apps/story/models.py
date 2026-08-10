"""Story state — sessions, adventures, beats, choices (PRODUCT.md §17.2, §17.4).

A **session** is one sitting at the app; an **adventure** is the story told
inside it. They are separate because a session can contain zero or several
adventures, and because `ended_reason` is a wellbeing signal we report to
parents (§13.6) independent of narrative completion.

A **beat** is the atomic unit the whole engine trades in (§8.1) — one narrated
moment with an illustration, a voice line, and something for the child to do.
Beats are persisted rather than held in memory for three reasons: the running
summary and the parent's transparency log (§12.6) are built from them, the media
rows have to hang off something durable, and a child who closes the tab mid-story
must be able to come back to the same forest.

The DB `Beat` deliberately **mirrors** the generated contract in
`apps.story.ai.schemas` rather than embedding it whole: the generated object is
what a model produced, and this is what we chose to keep. `continuity_refs` and
`next_hint` steer the *next* call, so they are persisted from Day 5 on — a
consequence beat is conditioned on the hint the beat before it left behind
(§8.5), and a hint that lived only in a response object could not survive the
round trip through the child's tap.
"""

import logging
from collections.abc import Mapping
from typing import Any

from django.db import models
from django.utils.text import slugify

from apps.common.models import UUIDModel

logger = logging.getLogger(__name__)

# What a child is told to expect from one sitting (§8.1's 5-7 beat arc). The
# bounds exist because `estimatedBeats` is a promise made to a client before a
# single beat has been generated.
MIN_ESTIMATED_BEATS = 5
MAX_ESTIMATED_BEATS = 7


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

    #: The session-row half of §11.1's "Redis + session row" short-term memory.
    #: A small, bounded mirror of the working memory in Redis: counters, and the
    #: things the *child* originated (what they said, what they named, what they
    #: picked). Written by `apps.memory.stm.mirror`.
    #:
    #: Not a duplicate of the Redis log and not a place to grow one. Redis holds
    #: the full ordered sequence and expires it in hours; this holds only the parts
    #: consolidation would be sorry to lose, because they are the highest-salience
    #: candidates there are (§11.4) and Redis is allowed to restart. Bounded in
    #: `stm.MAX_DIGEST_HIGHLIGHTS` so a long sitting cannot turn one JSON column
    #: into a transcript.
    stm_digest = models.JSONField(default=dict, blank=True)

    #: When long-term memory was written from this sitting (§11.4), or null.
    #:
    #: The idempotency gate on `memory.tasks.consolidate`. Celery is at-least-once,
    #: so a redelivered task would otherwise double a child's memories — and
    #: "remembers the same afternoon twice" is a specific, visible way for the
    #: companion to feel broken. Stamped even when a sitting produced nothing worth
    #: keeping, because "we looked and there was nothing" is a completed
    #: consolidation and leaving it null would make it a permanent retry.
    consolidated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["child", "-started_at"])]

    def __str__(self) -> str:
        return f"{self.child.first_name} @ {self.started_at:%Y-%m-%d %H:%M}"


class Adventure(UUIDModel):
    """One story, from hook to resolution (§8.1, §17.2 `adventure`)."""

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    child = models.ForeignKey(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="adventures",
    )
    # SET_NULL rather than CASCADE: an adventure outlives the sitting it began
    # in. A session row may be pruned by a retention job (§13.8) long before the
    # story it contained stops mattering to the child who remembers it.
    session = models.ForeignKey(
        PlaySession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="adventures",
    )
    title = models.CharField(max_length=200, blank=True)

    #: The skeleton (§8.1): `{title, skeleton: [...], branch_themes: [...]}`.
    #: Generated once, up front, and then held constant — every beat is generated
    #: *against* it, which is what stops a branching story from wandering.
    plan = models.JSONField(default=dict, blank=True)

    # Nullable today: the Learning Engine's objective selector is Day 10, so a
    # Day-4 adventure carries the placeholder objective and nothing chose it.
    # "Nobody has selected an objective yet" and "the objective is the empty
    # string" are different states, and only one of them is a bug.
    primary_objective_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    secondary_objective_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    difficulty = models.FloatField(default=0.4)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )

    #: Compacted history, kept small so the live prompt stays cheap (§8.6).
    #: Empty until Day 5 writes the first compaction.
    running_summary = models.TextField(blank=True)
    #: The emotional trajectory across the arc (§8.8), appended per beat.
    emotion_arc = models.JSONField(default=list, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["child", "-started_at"])]

    def __str__(self) -> str:
        return self.title or f"Adventure {self.id}"

    @property
    def estimated_beats(self) -> int:
        """How many beats the plan expects (§18.4 `estimatedBeats`).

        Read from the skeleton rather than stored, so it cannot disagree with the
        plan it describes. Clamped, because it is shown to a client as a progress
        hint and a model that returned a two-step skeleton must not make the
        child's story look nearly over before it starts.
        """
        steps = len(self.plan.get("skeleton") or [])
        return min(max(steps, MIN_ESTIMATED_BEATS), MAX_ESTIMATED_BEATS)


def _as_option(option: Any) -> dict[str, Any] | None:
    """One stored choice, read as the structured option the client expects.

    A generated option has been `{key, label, icon_prompt}` since choices became
    illustrated (§14.4, `ai.schemas.ChoiceOption`), and that is what this returns
    untouched. Beats written before that stored the choices as bare strings, and
    those rows are still on disk and still replayable — a child mid-adventure
    when the shape changed keeps their forest.

    A legacy string becomes an option with no `icon_prompt`, which is what makes
    it safe rather than merely non-crashing: `orchestrator.media_stages` skips an
    option with no prompt, so nothing tries to illustrate a card from a label
    that was never written to be drawn, and `choice_payload` renders it with a
    null `imageUrl` — the picture-less card it already handles.

    Anything else is dropped with a warning rather than guessed at. An option the
    engine cannot describe is one the child cannot be asked to take.
    """
    if isinstance(option, Mapping):
        return dict(option)
    if isinstance(option, str) and option.strip():
        label = option.strip()
        # `chosen_key` is 64 chars, and the key is what a tap sends back.
        key = slugify(label).replace("-", "_")[:64]
        return {"key": key or "option", "label": label, "icon_prompt": ""}
    logger.warning("story.choice_option_unreadable type=%s", type(option).__name__)
    return None


class Beat(UUIDModel):
    """One narrated moment (§8.1). Mirrors the generated beat contract."""

    class SafetyStatus(models.TextChoices):
        """Whether the runtime safety layer has seen this beat.

        `UNCHECKED` is the Day-4 truth and it is written down rather than
        implied: the output classifier lands on Day 7, and a column with no
        unchecked state would make every unmoderated beat look moderated.

        `DEGRADED` is that same argument applied one level in, and it exists
        because without it the question "which beats did the *independent*
        classifier actually approve?" had no answer in the database. A beat that
        the keyword policy passed while the classifier was unreachable was stored
        as `passed`, identical to one both guards read. It is written only when
        the classifier was configured to run and could not — a deployment that
        switched it off chose that, and `prod.py` will not boot having chosen it.
        """

        UNCHECKED = "unchecked", "Not yet checked"
        PASSED = "passed", "Passed"
        DEGRADED = "degraded", "Passed the deterministic guard; the classifier could not run"
        REWRITTEN = "rewritten", "Rewritten by the safety layer"
        BLOCKED = "blocked", "Blocked"

    adventure = models.ForeignKey(
        Adventure,
        on_delete=models.CASCADE,
        related_name="beats",
    )
    seq = models.PositiveIntegerField()
    narration = models.TextField()

    #: `{setting, illustration_prompt, characters_present}` (§8.1).
    scene = models.JSONField(default=dict, blank=True)
    #: `{type, prompt_audio, expected, choices}` — null when the beat asks nothing.
    interaction = models.JSONField(null=True, blank=True)
    #: `{objective_id, difficulty, skill_evidence_key}` — null when nothing is
    #: being practised. Day 10 reads these back as mastery evidence.
    learning = models.JSONField(null=True, blank=True)
    emotion = models.CharField(max_length=32, blank=True)

    #: Who wrote this beat — `provider:model`, e.g. `openrouter:nvidia/nemotron-…`.
    #: Blank for every beat written before the column existed, and for a beat no
    #: caller could attribute.
    #:
    #: Recorded because the qualities this engine is judged on are model-dependent
    #: and the router has a fallback chain: "did this model narrate an action the
    #: child never took" is a question about a set of rows, and without a stamp it
    #: has to be inferred from phrasing. Un-backfillable after the fact, which is
    #: why it is written from the first beat rather than when it is first needed.
    model = models.CharField(max_length=120, blank=True)

    #: The model's own steer for what should happen next, e.g. "if the child
    #: counts -> cross the river". Persisted because the beat that reads it is
    #: generated in a *different* request — after the child has tapped — so it has
    #: to survive the round trip (§8.5).
    next_hint = models.TextField(blank=True)
    #: Entities this beat leaned on, e.g. `["companion:pip", "world:river"]`.
    #: Written now and read by the memory service on Day 8; keeping it from the
    #: first branching beat is worth more than backfilling it later.
    continuity_refs = models.JSONField(default=list, blank=True)

    # `+` reverse names: a Media row is reached through its beat, never the other
    # way round, and two FKs to the same table would otherwise collide.
    media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    audio = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    #: `{choice_key: media_id}` — the little picture on each choice card (§14.4).
    #: A JSON map rather than a table: these rows are only ever read as a set
    #: belonging to one beat, they are written by the same task that writes the
    #: two columns above, and a child who cannot read needs the picture to arrive
    #: with the option, not to be joined to it.
    choice_media = models.JSONField(default=dict, blank=True)

    safety_status = models.CharField(
        max_length=16,
        choices=SafetyStatus.choices,
        default=SafetyStatus.UNCHECKED,
    )

    class Meta:
        ordering = ["adventure", "seq"]
        constraints = [
            # One beat per position. The stream generates beats sequentially, but
            # a client that opens the stream twice (a reload mid-generation is the
            # obvious way) would otherwise write two beat-1s into one story and
            # the child would be told the opening twice.
            models.UniqueConstraint(fields=["adventure", "seq"], name="beat_adventure_seq_uniq"),
        ]

    def __str__(self) -> str:
        return f"beat {self.seq} of {self.adventure_id}"

    @property
    def options(self) -> list[dict[str, Any]]:
        """The illustrated options this beat offers (§8.5), or an empty list.

        Read off `interaction` rather than stored twice: the generated beat is the
        only place options are decided, and a second copy is a second thing that
        can disagree about what the child was shown.

        Tolerant of a stored shape that is not the current one, because this is
        read on the SSE path (`streaming.choice_payload`,
        `orchestrator.media_stages`) where raising would abort a half-sent
        response and leave the child looking at a dead screen (§13). Beats
        written before options became structured hold bare strings here; those
        are read as label-only options rather than dropped, so an old adventure
        still plays instead of silently losing its branch.
        """
        coerced = (_as_option(option) for option in (self.interaction or {}).get("choices") or [])
        return [option for option in coerced if option is not None]

    def option(self, key: str) -> dict[str, Any] | None:
        """The option with this key, if this beat offered it."""
        return next((option for option in self.options if option.get("key") == key), None)


class Choice(UUIDModel):
    """What the child picked, and what the world did about it (§8.5).

    Written on **every** interaction, not only on a two-or-three-way branch: a tap
    to carry on is still the child acting, and `latency_ms`/`retries`/`assisted`
    are the wellbeing and difficulty signals (§8.9, §10.4) whatever the beat
    asked. It is also what makes "which beat is the child on" answerable — a beat
    with a choice against it has been played, and one without it is where they are
    now (`orchestrator.next_position`).

    Deliberately **not** a score. There is no correct column and no wrong column,
    because there are no losing choices (§8.5). `assisted` records that the
    companion helped, which is an input to difficulty, never a mark against the
    child.
    """

    adventure = models.ForeignKey(
        Adventure,
        on_delete=models.CASCADE,
        related_name="choices",
    )
    beat = models.ForeignKey(Beat, on_delete=models.CASCADE, related_name="choices")

    options = models.JSONField(default=list, blank=True)
    chosen_key = models.CharField(max_length=64, blank=True)
    #: What the child actually answered, when the beat asked for something —
    #: `3` for a count, a word for a `say`. JSON rather than a typed column
    #: because §18.6's `value` is whatever the interaction type meant it to be,
    #: and null because most beats ask only which way to go.
    value = models.JSONField(null=True, blank=True)
    consequence_summary = models.TextField(blank=True)

    #: How long the child took, how many attempts, and whether the companion
    #: counted along. Never a score — struggle is scaffolded, never punished (§8.5).
    latency_ms = models.IntegerField(null=True, blank=True)
    retries = models.IntegerField(default=0)
    assisted = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["adventure", "-created_at"])]
        constraints = [
            # One answer per beat. A double-tap on a big forgiving target is the
            # normal way a four-year-old submits twice, and two rows here would
            # advance the story two beats at once — the child would watch a beat
            # they never got to play. Retries live in the `retries` column, which
            # is what "the child tried again" actually means.
            models.UniqueConstraint(fields=["beat"], name="choice_beat_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.chosen_key or '(unanswered)'} on beat {self.beat_id}"
