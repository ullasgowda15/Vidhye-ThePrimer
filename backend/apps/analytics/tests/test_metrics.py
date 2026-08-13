"""The five §22.3 numbers (PRODUCT.md §22.1, §22.3, §5.6).

Criterion 1's second half: *the numbers are computable, and here they are for
seeded data*.

Almost everything here is built by writing events directly rather than by playing
stories, and that is deliberate: these metrics are about **calendar time** —
"came back the next day", "active in the week after" — and a test suite cannot
wait a week. `_seed` writes the events a child's first fortnight would have
produced, dated. `TestAgainstRealPlay` is the one class that plays for real, and
it is what proves the two ends of the pipeline are joined.

The assertions that matter most are the ones about the **denominators**. Every
one of these ratios has an honest reading and a flattering one, and the flattering
one is usually the accident: a retention cohort that includes children too new to
have returned, an activation rate that quietly drops the accounts that never came
back. Those have a test each.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import Any

import pytest
from django.utils import timezone

from apps.analytics import metrics
from apps.analytics.models import Event
from apps.analytics.tests.conftest import finish, play
from apps.profiles.models import Child

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def _at(days_ago: float) -> datetime.datetime:
    """A moment, `days_ago` days before now. Fractional days are allowed so a
    test can put two events on the same calendar day."""
    return NOW - datetime.timedelta(days=days_ago)


def _seed(
    child: Child,
    *,
    completed_on: Sequence[float] = (),
    started_on: Sequence[float] = (),
) -> None:
    """Write the events a child's play would have produced, dated.

    `completed_on` and `started_on` are "days ago". A completion always implies a
    start on the same day, because that is what actually happens — a test that
    seeded a completion with no start would be measuring a completion rate above
    100%, which is exactly the shape of bug these definitions exist to prevent.
    """
    for day in completed_on:
        Event.objects.create(name=Event.ADVENTURE_STARTED, child=child, ts=_at(day), props={})
        Event.objects.create(name=Event.ADVENTURE_COMPLETED, child=child, ts=_at(day), props={})
    for day in started_on:
        Event.objects.create(name=Event.ADVENTURE_STARTED, child=child, ts=_at(day), props={})


def _seed_week(child: Child, *, completions: int, weeks_ago: int = 1) -> datetime.date:
    """Put `completions` completed adventures inside one ISO week, on Mon/Tue/Wed…

    Anchored on the Monday of the target week rather than on "n days ago", so a
    test about a *weekly* metric does not depend on which weekday the suite is run.
    Returns the week start, for tests that want to assert on it.
    """
    local_today = timezone.localtime(NOW).date()
    monday = local_today - datetime.timedelta(days=local_today.weekday() + 7 * weeks_ago)

    for offset in range(completions):
        # Midday, so the local-to-UTC conversion cannot push an event over a day
        # boundary and out of its week.
        moment = timezone.make_aware(
            datetime.datetime.combine(
                monday + datetime.timedelta(days=offset), datetime.time(hour=12)
            )
        )
        Event.objects.create(name=Event.ADVENTURE_STARTED, child=child, ts=moment, props={})
        Event.objects.create(name=Event.ADVENTURE_COMPLETED, child=child, ts=moment, props={})
    return monday


class TestActivation:
    """§22.3: parent onboards **and** child completes first story."""

    def test_a_parent_who_did_both_activates(self, aarav: Child) -> None:
        parent = aarav.parent
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_at(3), props={})
        Event.objects.create(
            name=Event.ONBOARDING_COMPLETED,
            parent=parent,
            child=aarav,
            ts=_at(3),
            props={},
        )
        Event.objects.create(
            name=Event.ADVENTURE_COMPLETED,
            parent=parent,
            child=aarav,
            ts=_at(2),
            props={},
        )

        rate = metrics.activation(metrics.Window.last(7, now=NOW))
        assert (rate.numerator, rate.denominator) == (1, 1)
        assert rate.pct == 100.0

    def test_a_parent_who_onboarded_but_whose_child_never_finished_does_not(
        self, aarav: Child, nia: Child
    ) -> None:
        """The denominator is the point.

        Both parents signed up; only one child finished a story. An activation
        that anchored on *activity* rather than on signup would quietly drop the
        second account and report 100%.
        """
        for child, finished in ((aarav, True), (nia, False)):
            parent = child.parent
            Event.objects.create(
                name=Event.PARENT_SIGNED_UP,
                parent=parent,
                ts=_at(3),
                props={},
            )
            Event.objects.create(
                name=Event.ONBOARDING_COMPLETED,
                parent=parent,
                child=child,
                ts=_at(3),
                props={},
            )
            if finished:
                Event.objects.create(
                    name=Event.ADVENTURE_COMPLETED,
                    parent=parent,
                    child=child,
                    ts=_at(2),
                    props={},
                )

        rate = metrics.activation(metrics.Window.last(7, now=NOW))
        assert (rate.numerator, rate.denominator) == (1, 2)
        assert rate.pct == 50.0

    def test_activation_may_happen_after_the_window(self, aarav: Child) -> None:
        """A parent who signed up on Sunday and whose child finished on Tuesday
        activated. The *cohort* is bounded by the window; reaching the end of the
        funnel is not."""
        parent = aarav.parent
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_at(6), props={})
        Event.objects.create(
            name=Event.ONBOARDING_COMPLETED,
            parent=parent,
            ts=_at(6),
            props={},
        )
        Event.objects.create(
            name=Event.ADVENTURE_COMPLETED,
            parent=parent,
            child=aarav,
            # Yesterday, i.e. after a 3-day cohort window would have closed.
            ts=_at(1),
            props={},
        )

        rate = metrics.activation(metrics.Window(since=_at(7), until=_at(4)))
        assert (rate.numerator, rate.denominator) == (1, 1)

    def test_no_signups_reads_as_no_answer(self) -> None:
        rate = metrics.activation(metrics.Window.last(7, now=NOW))
        # Not 0% — "nobody has reached this step yet" is not "everybody failed".
        assert rate.value is None
        assert rate.pct is None


class TestDay2Return:
    """§22.3: the child returns the day after their first story."""

    def test_a_child_who_came_back_the_next_day_counts(self, aarav: Child) -> None:
        _seed(aarav, completed_on=[5], started_on=[4])

        rate = metrics.day2_return(metrics.Window.last(14, now=NOW), today=NOW.date())
        assert (rate.numerator, rate.denominator) == (1, 1)

    def test_a_child_who_came_back_two_days_later_does_not(self, aarav: Child) -> None:
        _seed(aarav, completed_on=[5], started_on=[3])

        rate = metrics.day2_return(metrics.Window.last(14, now=NOW), today=NOW.date())
        assert (rate.numerator, rate.denominator) == (0, 1)

    def test_a_child_too_new_to_judge_is_left_out_of_the_denominator(
        self, aarav: Child, nia: Child
    ) -> None:
        """The `_matured` guard, and the reason it exists.

        Without it a good week of signups would *lower* day-2 return, because
        every child who joined today would be counted as a miss for a day that
        has not happened.

        Nia's first story is anchored to **local midnight**, not to "0.2 days
        ago", for the reason the calendar test below spells out. A fractional-day
        offset only lands on *today* when the suite happens to run late enough in
        the day, and this test is about a child whose first story is today: run at
        00:33, 4.8 hours back was yesterday, which is old enough to judge, so Nia
        entered the denominator as a miss and the assertion read (1, 2). It passed
        locally and failed in CI, which is the only place this suite runs in the
        small hours (Day-14 review, P1-7).
        """
        _seed(aarav, completed_on=[5], started_on=[4])

        # Four hours ago, or local midnight if that is more recent — always today,
        # always in the past, at every hour the suite might run at.
        midnight = timezone.localtime(NOW).replace(hour=0, minute=0, second=0, microsecond=0)
        first_story = max(midnight, NOW - datetime.timedelta(hours=4))
        Event.objects.create(name=Event.ADVENTURE_STARTED, child=nia, ts=first_story, props={})
        Event.objects.create(name=Event.ADVENTURE_COMPLETED, child=nia, ts=first_story, props={})

        rate = metrics.day2_return(metrics.Window.last(14, now=NOW), today=NOW.date())
        assert (rate.numerator, rate.denominator) == (1, 1)

    def test_the_boundary_is_the_calendar_and_not_24_hours(self, aarav: Child) -> None:
        """A child who plays at 6pm Monday and 9am Tuesday came back the next day.

        A 24-hour rule would say they did not, which is neither what a parent
        means nor what §4.5's day-two payoff describes.
        """
        # Anchor the seed to local midnight so the two moments are unambiguously
        # on consecutive calendar days whatever time the suite runs at.
        midnight = timezone.localtime(NOW).replace(hour=0, minute=0, second=0, microsecond=0)
        first = midnight - datetime.timedelta(days=5) + datetime.timedelta(hours=18)
        second = midnight - datetime.timedelta(days=4) + datetime.timedelta(hours=9)

        Event.objects.create(name=Event.ADVENTURE_COMPLETED, child=aarav, ts=first, props={})
        Event.objects.create(name=Event.ADVENTURE_STARTED, child=aarav, ts=second, props={})

        rate = metrics.day2_return(metrics.Window.last(14, now=NOW), today=NOW.date())
        assert (rate.numerator, rate.denominator) == (1, 1)


class TestW1Retention:
    """§22.3's W1: active in the week after the first story."""

    def test_a_child_active_on_day_four_is_retained(self, aarav: Child) -> None:
        _seed(aarav, completed_on=[10], started_on=[6])

        rate = metrics.w1_retention(metrics.Window.last(30, now=NOW), today=NOW.date())
        assert (rate.numerator, rate.denominator) == (1, 1)

    def test_the_first_story_itself_is_not_evidence_of_returning(self, aarav: Child) -> None:
        """Day 0 is excluded. Including it would make W1 unfalsifiable — every
        child who ever completed a story would be retained by definition."""
        _seed(aarav, completed_on=[10])

        rate = metrics.w1_retention(metrics.Window.last(30, now=NOW), today=NOW.date())
        assert (rate.numerator, rate.denominator) == (0, 1)

    def test_a_child_whose_week_has_not_finished_is_not_yet_judged(self, aarav: Child) -> None:
        _seed(aarav, completed_on=[3], started_on=[2])

        rate = metrics.w1_retention(metrics.Window.last(30, now=NOW), today=NOW.date())
        assert rate.denominator == 0
        assert rate.value is None

    def test_activity_after_the_week_does_not_count(self, aarav: Child) -> None:
        _seed(aarav, completed_on=[20], started_on=[9])

        rate = metrics.w1_retention(metrics.Window.last(60, now=NOW), today=NOW.date())
        assert (rate.numerator, rate.denominator) == (0, 1)


class TestCompletionRate:
    """§22.3: adventures finished ÷ started."""

    def test_three_of_four(self, aarav: Child) -> None:
        _seed(aarav, completed_on=[3, 2, 1], started_on=[1])

        rate = metrics.completion_rate(metrics.Window.last(7, now=NOW))
        assert (rate.numerator, rate.denominator) == (3, 4)
        assert rate.pct == 75.0

    def test_it_cannot_exceed_one_hundred_percent_for_a_normal_week(self, aarav: Child) -> None:
        """Every completion in `_seed` implies its own start, which is what
        actually happens — the guard here is against a future emission that
        completes something it never started."""
        _seed(aarav, completed_on=[3, 2, 1])

        rate = metrics.completion_rate(metrics.Window.last(7, now=NOW))
        assert rate.value == 1.0


class TestWeeklyAdventuresAndTheNorthStar:
    """§22.1: DWA is children who complete ≥3 adventures in a week.

    Every test here places its completions inside **one deliberate ISO week**
    (`_last_week`) rather than "one, two and three days ago". The first draft did
    the latter and failed on five weekdays out of seven — three adventures on
    Saturday, Sunday and Monday are three adventures across two weeks, and the
    child is not a DWA in either. That is not a quirk of the implementation, it is
    what a weekly metric means, and a test that only passed midweek would have
    been testing the calendar.
    """

    def test_a_child_with_three_adventures_in_a_week_is_delighted(self, aarav: Child) -> None:
        _seed_week(aarav, completions=3)

        weeks = metrics.weekly_adventures(metrics.Window.last(21, now=NOW))
        assert sum(week["dwa"] for week in weeks) == 1
        assert sum(week["adventures"] for week in weeks) == 3

    def test_a_child_with_two_is_not(self, aarav: Child) -> None:
        _seed_week(aarav, completions=2)

        weeks = metrics.weekly_adventures(metrics.Window.last(21, now=NOW))
        assert sum(week["dwa"] for week in weeks) == 0
        assert sum(week["adventures"] for week in weeks) == 2

    def test_three_adventures_split_across_two_weeks_are_not_a_dwa(self, aarav: Child) -> None:
        """The property the metric is *for*.

        DWA fuses "came back" with "habit" (§22.1), and a habit is a thing that
        happens inside a week. Two adventures one week and one the next is a child
        who is playing, and it is not the number §22.1 counts.
        """
        _seed_week(aarav, completions=2, weeks_ago=2)
        _seed_week(aarav, completions=1, weeks_ago=1)

        weeks = metrics.weekly_adventures(metrics.Window.last(28, now=NOW))
        assert sum(week["adventures"] for week in weeks) == 3
        assert sum(week["dwa"] for week in weeks) == 0

    def test_per_child_divides_by_active_children_not_by_everyone(
        self, aarav: Child, nia: Child
    ) -> None:
        """ "Adventures per child per week" is about the children who are playing.

        Dividing by everyone who ever signed up turns it into a retention metric
        wearing an engagement metric's name — and would make the number fall every
        time a new family joined.
        """
        _seed_week(aarav, completions=2)
        # `nia` exists and did not play.

        weeks = metrics.weekly_adventures(metrics.Window.last(21, now=NOW))
        assert len(weeks) == 1
        assert weeks[0]["active_children"] == 1
        assert weeks[0]["per_child"] == pytest.approx(2.0)

    def test_weeks_start_on_monday(self, aarav: Child) -> None:
        """ISO weeks, so a week is the same seven days for everybody. Django's own
        `TruncWeek` starts on Sunday, which is why `weekly_adventures` buckets in
        Python."""
        _seed(aarav, completed_on=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10])

        weeks = metrics.weekly_adventures(metrics.Window.last(30, now=NOW))
        assert all(week["week_start"].weekday() == 0 for week in weeks)


class TestGuardrails:
    """§22.5 — watched and capped, never maximised."""

    def test_ended_reasons_are_reported_as_they_are(self, aarav: Child) -> None:
        for reason in ("completed", "completed", "cap", "exit"):
            Event.objects.create(
                name=Event.SESSION_ENDED,
                child=aarav,
                ts=_at(1),
                props={"ended_reason": reason},
            )

        rails = metrics.guardrails(metrics.Window.last(7, now=NOW))
        assert rails["ended_reasons"] == {"completed": 2, "cap": 1, "exit": 1}
        assert rails["sessions"] == 4

    def test_the_compulsive_pattern_flag_is_a_maximum_not_a_mean(
        self, aarav: Child, nia: Child
    ) -> None:
        """The pattern §22.5 wants flagged belongs to *one* child. A mean over a
        healthy cohort hides it."""
        for _ in range(6):
            Event.objects.create(
                name=Event.SESSION_ENDED,
                child=aarav,
                ts=_at(1),
                props={"ended_reason": "exit"},
            )
        Event.objects.create(
            name=Event.SESSION_ENDED,
            child=nia,
            ts=_at(1),
            props={"ended_reason": "completed"},
        )

        rails = metrics.guardrails(metrics.Window.last(7, now=NOW))
        assert rails["max_sessions_per_child_day"] == 6

    def test_serious_incidents_are_counted_apart(self, aarav: Child) -> None:
        for severity in ("low", "medium", "high", "critical"):
            Event.objects.create(
                name=Event.SAFETY_INCIDENT,
                child=aarav,
                ts=_at(1),
                props={"severity": severity},
            )

        rails = metrics.guardrails(metrics.Window.last(7, now=NOW))
        assert rails["safety_incidents"] == 4
        # Any serious incident is a stop-the-line event, so it must not be
        # averaged into a total that looks fine.
        assert rails["serious_safety_incidents"] == 2


class TestAgainstRealPlay:
    """The two ends of the pipeline, joined.

    Everything above writes events by hand so that calendar-time metrics are
    testable at all. This class plays real stories through the real orchestrator
    and asserts the numbers come out — which is what stops the seeded tests from
    being a well-tested fiction.
    """

    def test_two_played_adventures_produce_a_completion_rate_and_a_week(
        self, aarav: Child, fake_ai: Any
    ) -> None:
        from apps.story.models import PlaySession

        finish(play(aarav))
        finish(play(aarav), reason=PlaySession.EndedReason.EXIT)

        window = metrics.Window.last(7, now=timezone.now())
        rate = metrics.completion_rate(window)
        assert (rate.numerator, rate.denominator) == (1, 2)
        assert rate.pct == 50.0

        weeks = metrics.weekly_adventures(window)
        assert sum(week["adventures"] for week in weeks) == 1
        assert sum(week["active_children"] for week in weeks) == 1

    def test_the_snapshot_the_dashboard_renders_computes_end_to_end(
        self, aarav: Child, fake_ai: Any
    ) -> None:
        finish(play(aarav))

        snapshot = metrics.snapshot(metrics.Window.last(7, now=timezone.now()))
        assert set(snapshot) == {
            "window",
            "activation",
            "day2_return",
            "w1_retention",
            "completion_rate",
            "weeks",
            "guardrails",
            "events",
        }
        assert dict(snapshot["events"])[Event.ADVENTURE_COMPLETED] == 1
