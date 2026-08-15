"""Parent dashboard payloads (PRODUCT.md §12, §18.7).

**Field naming follows the spec, per endpoint, rather than one house style.** The
weekly report is written out in §18.7 with `completionRate` next to
`interests_trend`, and it is written that way here — a client generated from this
schema has to match the document the API was specified in, and "we tidied it up"
is how a spec and an implementation start disagreeing. The transcript is
camelCase for the same reason the story payloads are (§18.4/§18.5: it is the same
surface, seen from the other side). Everything else is snake_case, like the
accounts, profiles and companion endpoints.

Nothing in this module computes anything. `apps.analytics.reports`,
`.skills` and `.transparency` decide what is true; these decide what goes on the
wire.
"""

from __future__ import annotations

from rest_framework import serializers


class SkillEvidenceSerializer(serializers.Serializer):
    """What one objective was asked and how it went, in one sitting (§12.1)."""

    code = serializers.CharField()
    domain = serializers.CharField()
    milestone = serializers.CharField()
    asks = serializers.IntegerField(help_text="How many times it came up in this sitting.")
    unaided = serializers.IntegerField(help_text="How many they managed on their own.")
    assisted = serializers.IntegerField(help_text="How many the companion helped with.")
    retried = serializers.IntegerField(help_text="How many took more than one go.")
    status = serializers.CharField(
        help_text="not_started / emerging / practising / mastered, as of this sitting.",
    )


class SessionReportSerializer(serializers.Serializer):
    """One sitting, as §12.1 asks for it: where, what, how, and one real moment."""

    session_id = serializers.UUIDField(source="session_key")
    occurred_at = serializers.DateTimeField()
    duration_s = serializers.IntegerField()
    ended_reason = serializers.CharField(
        help_text=(
            "`completed` when the child reached the warm close, `exit` when they "
            "left before it, `cap` when the session limit ended it. Reported as it "
            "is — a short session is not a failed one (§13.6)."
        ),
    )
    beats = serializers.IntegerField()

    adventure_id = serializers.UUIDField(source="adventure_key", allow_null=True)
    adventure_title = serializers.CharField()
    world_name = serializers.CharField()
    place_name = serializers.CharField()

    key_choice = serializers.CharField(
        help_text="The branch that mattered and what came of it. Empty if nothing branched.",
    )
    objective_code = serializers.CharField()
    milestone_name = serializers.CharField(
        help_text="§10.6's plain language for what was practised. Never an objective code.",
    )
    how_it_went = serializers.CharField()

    highlight = serializers.CharField()
    highlight_source = serializers.CharField(
        help_text=(
            "`model` — written by a model and checked against this session; `data` — "
            "assembled from what the child actually did; `quiet` — nothing notable "
            "happened and we said so (§13.9)."
        ),
    )
    skills = SkillEvidenceSerializer(many=True)


class SessionReportSummarySerializer(serializers.Serializer):
    """One row of the reports index (§12.10)."""

    session_id = serializers.UUIDField(source="session_key")
    occurred_at = serializers.DateTimeField()
    adventure_title = serializers.CharField()
    world_name = serializers.CharField()
    ended_reason = serializers.CharField()
    highlight = serializers.CharField()
    highlight_source = serializers.CharField()


class WeeklySkillSerializer(serializers.Serializer):
    """One objective's week (§18.7's `skills[]`)."""

    domain = serializers.CharField()
    objective = serializers.CharField()
    milestone = serializers.CharField()
    asks = serializers.IntegerField()
    movement = serializers.CharField(
        help_text=(
            "How this moved across the week, in words. A phrase rather than §18.7's "
            '`"+0.2"`: mastery is on the difficulty scale and its ceiling differs '
            "per objective, so the number is not interpretable without the "
            "curriculum in hand (see `apps.analytics.skills`)."
        ),
    )
    status = serializers.CharField()


class WeeklyMilestoneSerializer(serializers.Serializer):
    """A milestone step crossed during the week (§10.6, §18.7)."""

    name = serializers.CharField()
    step = serializers.CharField(help_text="The objective that completed it, in plain language.")
    reached = serializers.BooleanField()


class WeeklyReportSerializer(serializers.Serializer):
    """§18.7's weekly report, spelled as §18.7 spells it."""

    week = serializers.CharField(help_text='e.g. "2026-08-01/2026-08-07" (Monday to Sunday).')
    sessions = serializers.IntegerField()
    completionRate = serializers.FloatField(source="completion_rate")
    highlight = serializers.CharField()
    skills = WeeklySkillSerializer(many=True)
    milestone = WeeklyMilestoneSerializer(allow_null=True)
    interests_trend = serializers.ListField(child=serializers.CharField())
    recommendation = serializers.CharField()
    linesRead = serializers.IntegerField(
        source="lines_read",
        help_text=(
            "Lines the child read aloud to their companion this week. A count "
            "only: nothing has listened to the recordings yet, so there is no "
            "accuracy figure to report and none is implied."
        ),
    )


class ProgressSerializer(serializers.Serializer):
    """One objective on the insights page (§12.3).

    `mastery` and `reach` are on the wire because the brief's `/progress` shape
    names the first, and because the first is meaningless without the second — see
    `apps.analytics.skills`. **Neither is rendered.** The parent surface shows
    `status` and `summary`; a percentage would be exactly the grade §12.3 forbids.
    """

    domain = serializers.CharField()
    objective = serializers.CharField(source="code")
    milestone = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField(help_text="not_started / emerging / practising / mastered.")
    summary = serializers.CharField(help_text="The same thing in a sentence, for a person.")
    evidence_count = serializers.IntegerField()
    mastery = serializers.FloatField()
    reach = serializers.FloatField(help_text="The highest mastery this objective's asks reach.")


class MilestoneSerializer(serializers.Serializer):
    """A developmental step and everything under it (§10.6, §12.5)."""

    name = serializers.CharField()
    domain = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField(
        help_text="The least advanced objective under it — so 'mastered' means all of them.",
    )
    furthest = serializers.CharField(
        help_text="The furthest thing in this milestone the child does reliably, or empty.",
    )
    skills = ProgressSerializer(many=True)


class InterestTrendSerializer(serializers.Serializer):
    """§11.7's "this month: space, then dinosaurs, now ocean", as data."""

    now = serializers.ListField(
        child=serializers.CharField(),
        help_text="What they love this week, strongest first.",
    )
    earlier = serializers.ListField(
        child=serializers.CharField(),
        help_text="What they loved when we last looked, from the stored snapshots.",
    )
    story = serializers.CharField(
        help_text="The same thing as a sentence, or empty when there is not enough history.",
    )


class InsightsSerializer(serializers.Serializer):
    """The insights page in one read (§12.3, §12.5)."""

    milestones = MilestoneSerializer(many=True)
    interests = InterestTrendSerializer()


class MemorySerializer(serializers.Serializer):
    """One thing the companion remembers, for the parent to read (§12.6, §11.10).

    No `salience`, no `decay`, no `recall_count` and no embedding. Those are how
    retrieval ranks; showing them would invite a parent to read a number as a
    verdict on a moment in their child's life, and none of them mean that. `type`
    is sent as its own label rather than its database value for the same reason.
    """

    id = serializers.UUIDField()
    type = serializers.CharField()
    type_label = serializers.CharField()
    content = serializers.CharField()
    created_at = serializers.DateTimeField()
    adventure_id = serializers.UUIDField(allow_null=True)


class TranscriptBeatSerializer(serializers.Serializer):
    """One beat, verbatim (§12.6)."""

    seq = serializers.IntegerField()
    narration = serializers.CharField()
    emotion = serializers.CharField()
    imageUrl = serializers.CharField(allow_null=True)
    safetyStatus = serializers.CharField()
    asked = serializers.CharField()
    options = serializers.ListField(child=serializers.CharField())
    answered = serializers.CharField()


class TranscriptSerializer(serializers.Serializer):
    """One story, exactly as the child experienced it (§12.6)."""

    adventureId = serializers.UUIDField()
    title = serializers.CharField()
    world = serializers.CharField()
    place = serializers.CharField()
    status = serializers.CharField()
    startedAt = serializers.DateTimeField()
    completedAt = serializers.DateTimeField(allow_null=True)
    beats = TranscriptBeatSerializer(many=True)


class TranscriptSummarySerializer(serializers.Serializer):
    """One row of the transparency index (§12.10)."""

    adventureId = serializers.UUIDField(source="id")
    title = serializers.CharField()
    status = serializers.CharField()
    startedAt = serializers.DateTimeField(source="started_at")
    beats = serializers.IntegerField()
