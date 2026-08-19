"""The behavioural readings behind the dashboard (`apps.analytics.insights`).

Built by writing events directly, for the reason `test_metrics.py` gives: these
readings are about **clock time** — "played at 20:00", "signed up on Tuesday",
"came back nine hours later" — and a suite cannot wait for an evening. `_at`
places an event at a chosen moment; every test then asserts on a number whose
correct value can be worked out by hand from what was written.

The assertions that matter most are the ones about **what the number refuses to
say**: a percentile over an empty list, a bucket that has to stay separate from a
zero, a drop-off measured against the previous step rather than the top. Those are
the readings a dashboard gets quietly wrong, and each has a test here.

The last class renders both real templates against a real context. A dashboard
that computes perfectly and raises `TemplateSyntaxError` on the one box that has
it is not a dashboard, and nothing else in this suite would notice.
"""

from __future__ import annotations

import datetime
import zoneinfo
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils import timezone

from apps.analytics import insights, internal
from apps.analytics.metrics import Window
from apps.analytics.models import Event
from apps.analytics.tests.conftest import finish, play
from apps.profiles.models import Child

pytestmark = pytest.mark.django_db

NOW = timezone.now()
UTC = zoneinfo.ZoneInfo("UTC")
KOLKATA = zoneinfo.ZoneInfo("Asia/Kolkata")


def _window(days: int = 30) -> Window:
    return Window.last(days, now=NOW)


def _at(**delta: float) -> datetime.datetime:
    return NOW - datetime.timedelta(**delta)


def _on(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    """A precise UTC moment. The clock readings are all about the hour, so a test
    about them has to name one rather than take whatever `now` happens to be."""
    return datetime.datetime.combine(day, datetime.time(hour, minute), tzinfo=UTC)


def _event(name: str, *, ts: datetime.datetime, child: Child | None = None, **props: Any) -> Event:
    return Event.objects.create(
        name=name,
        child=child,
        parent=getattr(child, "parent", None),
        ts=ts,
        props=props,
    )


@pytest.fixture
def parent(db: Any) -> Any:
    return get_user_model().objects.create_user(
        email="insights-parent@example.com", password="storybook-primer-42"
    )


@pytest.fixture
def kid(parent: Any) -> Child:
    return Child.objects.create(
        parent=parent,
        first_name="Ravi",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
    )


# ---------------------------------------------------------------------------
class TestTheClock:
    """When families are here — the reading the old dashboard could not make."""

    def test_hours_are_counted_in_the_zone_that_was_asked_for(self, kid: Child) -> None:
        """The same event is a different hour in two zones, and that is the point.

        18:30 UTC is 00:00 the next day in Kolkata. A dashboard that ignored the
        zone would report an Indian family's bedtime story as a lunchtime one.
        """
        yesterday = timezone.localtime(NOW).date() - datetime.timedelta(days=1)
        _event(Event.ADVENTURE_STARTED, ts=_on(yesterday, 18, 30), child=kid)

        in_utc = insights.clock(_window(), zone=UTC)
        in_kolkata = insights.clock(_window(), zone=KOLKATA)

        assert in_utc["hours"][18]["plays"] == 1
        assert in_utc["busiest_hour"] == "18:00"
        # +05:30 pushes it past midnight into the next day's midnight hour.
        assert in_kolkata["hours"][0]["plays"] == 1
        assert in_kolkata["busiest_hour"] == "00:00"

    def test_the_grid_crosses_weekday_with_hour(self, kid: Child) -> None:
        """The 7x24 grid is the only reading that can tell two habits apart."""
        monday = timezone.localtime(NOW).date()
        monday -= datetime.timedelta(days=monday.weekday())
        monday -= datetime.timedelta(days=7)  # safely inside a 30-day window

        for _ in range(3):
            _event(Event.ADVENTURE_STARTED, ts=_on(monday, 19), child=kid)

        reading = insights.clock(_window(), zone=UTC)
        row = reading["grid"][0]
        assert row["day"] == "Mon"
        assert row["cells"][19]["count"] == 3
        assert row["cells"][19]["level"] == insights.LEVELS  # the peak takes the top shade
        assert reading["grid_max"] == 3

    def test_an_empty_hour_is_level_zero_not_the_palest_shade(self, kid: Child) -> None:
        """An hour nobody played and an hour one child played must not look alike."""
        day = timezone.localtime(NOW).date() - datetime.timedelta(days=1)
        for _ in range(10):
            _event(Event.ADVENTURE_STARTED, ts=_on(day, 20), child=kid)
        _event(Event.ADVENTURE_STARTED, ts=_on(day, 7), child=kid)

        cells = {
            cell["hour"]: cell
            for row in insights.clock(_window(), zone=UTC)["grid"]
            for cell in row["cells"]
            if cell["count"]
        }
        assert cells[7]["level"] >= 1
        assert cells[20]["level"] == insights.LEVELS
        empty = [
            cell
            for row in insights.clock(_window(), zone=UTC)["grid"]
            for cell in row["cells"]
            if not cell["count"]
        ]
        assert all(cell["level"] == 0 for cell in empty)

    def test_signups_are_kept_on_their_own_curve(self, parent: Any, kid: Child) -> None:
        """Two different behaviours by two different people, never one series."""
        day = timezone.localtime(NOW).date() - datetime.timedelta(days=1)
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_on(day, 13), props={})
        _event(Event.ADVENTURE_STARTED, ts=_on(day, 20), child=kid)

        reading = insights.clock(_window(), zone=UTC)
        assert reading["hours"][13]["signups"] == 1
        assert reading["hours"][13]["plays"] == 0
        assert reading["hours"][20]["plays"] == 1
        assert reading["hours"][20]["signups"] == 0

    def test_an_empty_window_says_nothing_rather_than_zero_oclock(self) -> None:
        reading = insights.clock(_window(), zone=UTC)
        assert reading["busiest_hour"] is None
        assert reading["busiest_weekday"] is None
        assert reading["weekend_share"].pct is None


# ---------------------------------------------------------------------------
class TestTheDailySeries:
    def test_every_day_in_the_window_appears_including_the_empty_ones(self, kid: Child) -> None:
        """A quiet day is a zero, never a gap.

        A series that skipped it would join Friday to Monday with a straight line
        and hide the weekend — the worst thing a chart can do to this product.
        """
        _event(Event.ADVENTURE_STARTED, ts=_at(days=2), child=kid)
        reading = insights.daily(_window(7), zone=UTC)

        # Seven days of window spans eight calendar dates at most, and at least
        # eight when the window's ends fall on different days.
        assert len(reading["series"]) >= 8
        assert sum(row["started"] for row in reading["series"]) == 1
        assert any(row["started"] == 0 for row in reading["series"])
        dates = [row["date"] for row in reading["series"]]
        assert dates == sorted(dates)

    def test_minutes_come_off_the_sitting_that_closed(self, kid: Child) -> None:
        _event(Event.SESSION_ENDED, ts=_at(days=1), child=kid, duration_s=900, beats=6)
        reading = insights.daily(_window(7), zone=UTC)
        assert reading["total_minutes"] == 15
        assert reading["max_minutes"] == 15


# ---------------------------------------------------------------------------
class TestTheRoster:
    def test_it_names_the_account_and_counts_the_children(self, parent: Any, kid: Child) -> None:
        """The customer is named; the child is counted and never named."""
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_at(days=3), props={})
        _event(Event.CHILD_CREATED, ts=_at(days=3), child=kid, age_band="5-6")
        _event(Event.ADVENTURE_STARTED, ts=_at(days=2), child=kid)
        _event(Event.ADVENTURE_COMPLETED, ts=_at(days=2), child=kid)
        _event(Event.SESSION_ENDED, ts=_at(days=2), child=kid, duration_s=600, beats=5)

        reading = insights.accounts(_window(), zone=UTC)
        [row] = reading["rows"]

        assert row["email"] == "insights-parent@example.com"
        assert row["children"] == 1
        assert row["completed"] == 1
        assert row["minutes"] == 10
        assert row["stage"] == "playing"
        # The child's name appears nowhere in the reading.
        assert kid.first_name not in repr(reading)

    def test_stage_is_where_they_stopped(self, parent: Any) -> None:
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_at(hours=2), props={})
        reading = insights.accounts(_window(), zone=UTC)
        [row] = reading["rows"]
        assert row["stage"] == "signed up"
        # An account that has done nothing has no quiet time — a dash, not a zero.
        assert row["quiet_h"] is None
        assert row["age_h"] == pytest.approx(2.0, abs=0.1)

    def test_follow_up_is_not_clipped_by_the_end_of_the_window(
        self, parent: Any, kid: Child
    ) -> None:
        """A family who signed up on the last day and played after it still counts.

        The window decides who is in the table, not what they are credited with.
        """
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_at(hours=1), props={})
        # After `window.until`, which is `NOW`.
        _event(
            Event.ADVENTURE_STARTED,
            ts=NOW + datetime.timedelta(minutes=30),
            child=kid,
        )
        [row] = insights.accounts(_window(), zone=UTC)["rows"]
        assert row["started"] == 1
        assert row["stage"] == "first story"


# ---------------------------------------------------------------------------
class TestActivationSpeed:
    def test_only_accounts_that_arrived_are_measured(self, parent: Any, kid: Child) -> None:
        """A family who has not played yet is waiting, not slow."""
        other = get_user_model().objects.create_user(
            email="insights-waiting@example.com", password="storybook-primer-42"
        )
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_at(hours=5), props={})
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=other, ts=_at(hours=5), props={})
        _event(Event.ADVENTURE_COMPLETED, ts=_at(hours=3), child=kid)

        reading = insights.activation_speed(_window())
        assert reading["signups"] == 2
        assert reading["activated"] == 1
        assert reading["waiting"] == 1
        assert reading["to_first_completion"]["median"] == pytest.approx(2.0, abs=0.1)

    def test_an_empty_window_returns_the_same_shape(self) -> None:
        """A dict whose keys depend on whether there was data is a KeyError waiting."""
        reading = insights.activation_speed(_window())
        assert reading["to_first_completion"]["median"] is None
        assert reading["buckets"]
        assert reading["activated"] == 0


# ---------------------------------------------------------------------------
class TestAttention:
    def test_worlds_are_ranked_by_minutes_and_not_by_picks(self, kid: Child) -> None:
        """The whole reason this reading exists.

        A world picked four times for a minute each and one picked once for twenty
        are identical on a popularity chart and opposite here.
        """
        for index in range(4):
            session = f"0000000{index}-0000-0000-0000-00000000000{index}"
            Event.objects.create(
                name=Event.ADVENTURE_STARTED,
                child=kid,
                ts=_at(days=1),
                session_key=session,
                props={"world": "glitter"},
            )
            Event.objects.create(
                name=Event.SESSION_ENDED,
                child=kid,
                ts=_at(days=1),
                session_key=session,
                props={"duration_s": 60, "beats": 1},
            )
        deep = "aaaaaaaa-0000-0000-0000-000000000000"
        Event.objects.create(
            name=Event.ADVENTURE_STARTED,
            child=kid,
            ts=_at(days=1),
            session_key=deep,
            props={"world": "tidepool"},
        )
        Event.objects.create(
            name=Event.SESSION_ENDED,
            child=kid,
            ts=_at(days=1),
            session_key=deep,
            props={"duration_s": 1200, "beats": 10},
        )

        worlds = insights.attention(_window())["worlds"]
        assert [row["world"] for row in worlds] == ["tidepool", "glitter"]
        assert worlds[0]["minutes"] == 20
        assert worlds[1]["minutes"] == 4
        # Popularity says the opposite: glitter was picked four times to one.
        assert worlds[1]["sittings"] == 4

    def test_a_missing_duration_is_not_a_zero_length_sitting(self, kid: Child) -> None:
        _event(Event.SESSION_ENDED, ts=_at(days=1), child=kid, beats=3)
        _event(Event.SESSION_ENDED, ts=_at(days=1), child=kid, duration_s=600, beats=4)
        reading = insights.attention(_window())
        assert reading["sittings"] == 2
        # Only one had a measurable length; the other is absent, not averaged in.
        assert reading["sitting_minutes"]["measured"] == 1
        assert reading["sitting_minutes"]["median"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
class TestStoryflow:
    def test_the_reach_curve_counts_adventures_not_beats(self, kid: Child) -> None:
        """An adventure that retried a beat is one adventure at that depth."""
        for seq in (1, 2, 2, 3):
            _event(Event.BEAT_DELIVERED, ts=_at(days=1), child=kid, adventure_id="adv-1", seq=seq)
        for seq in (1, 2):
            _event(Event.BEAT_DELIVERED, ts=_at(days=1), child=kid, adventure_id="adv-2", seq=seq)

        reading = insights.storyflow(_window())
        rows = {row["seq"]: row for row in reading["rows"]}
        assert rows[1]["reached"] == 2
        assert rows[2]["reached"] == 2
        assert rows[3]["reached"] == 1
        assert reading["adventures"] == 2
        assert reading["deepest"] == 3

    def test_kept_is_measured_against_the_previous_step(self, kid: Child) -> None:
        """Against the top, every beat after a bad one looks bad too."""
        for adventure in range(10):
            for seq in (1, 2):
                _event(
                    Event.BEAT_DELIVERED,
                    ts=_at(days=1),
                    child=kid,
                    adventure_id=f"adv-{adventure}",
                    seq=seq,
                )
        # Half of them go one beat further.
        for adventure in range(5):
            _event(
                Event.BEAT_DELIVERED,
                ts=_at(days=1),
                child=kid,
                adventure_id=f"adv-{adventure}",
                seq=3,
            )

        rows = {row["seq"]: row for row in insights.storyflow(_window())["rows"]}
        assert rows[3]["kept"].pct == pytest.approx(50.0)
        assert rows[3]["of_first"].pct == pytest.approx(50.0)
        # Beat 4 held nobody, but it did not lose them either — beat 3 did.
        assert rows[4]["reached"] == 0
        assert rows[3]["lost"] == 5

    def test_an_adventure_with_no_first_beat_is_a_failure_not_a_dropoff(self, kid: Child) -> None:
        _event(Event.ADVENTURE_STARTED, ts=_at(days=1), child=kid)
        _event(Event.ADVENTURE_STARTED, ts=_at(days=1), child=kid)
        _event(Event.BEAT_DELIVERED, ts=_at(days=1), child=kid, adventure_id="adv-1", seq=1)

        reading = insights.storyflow(_window())
        assert reading["started"] == 2
        assert reading["reached_one"] == 1
        assert reading["no_first_beat"] == 1

    def test_worst_step_names_the_beat_they_failed_to_reach(self, kid: Child) -> None:
        """One adventure that saw only beat 1 dropped *between* beat 1 and beat 2.

        The page reads this as "between beat 1 and beat 2", not "at beat 1" — the
        beat to go and read is the last one they saw, and the step is what names it.
        """
        _event(Event.BEAT_DELIVERED, ts=_at(days=1), child=kid, adventure_id="adv-1", seq=1)
        step = insights.storyflow(_window())["worst_step"]
        assert step is not None
        assert step["seq"] == 2
        assert step["lost"] == 1

    def test_worst_step_never_accuses_beat_one_itself(self) -> None:
        """Beat 1's "loss" is measured against itself and is always zero.

        On an empty window there is no step to name at all, rather than a sentence
        reading "most children stop at beat 1".
        """
        assert insights.storyflow(_window())["worst_step"] is None


# ---------------------------------------------------------------------------
class TestInteraction:
    def test_the_options_taken_are_reported_with_a_folded_tail(self, kid: Child) -> None:
        """Which options children take, without a long tail of one-offs taking over."""
        for _ in range(8):
            _event(
                Event.CHOICE_MADE,
                ts=_at(days=1),
                child=kid,
                chosen_key="continue",
                latency_ms=800,
                retries=0,
                assisted=False,
                learning_ask=False,
            )
        for key in ("red_leaf", "look_at_willow"):
            _event(
                Event.CHOICE_MADE,
                ts=_at(days=1),
                child=kid,
                chosen_key=key,
                latency_ms=6000,
                retries=0,
                assisted=False,
                learning_ask=False,
            )

        reading = insights.interaction(_window())
        top = reading["options"][0]
        assert top["label"] == "continue"
        assert top["share"].pct == pytest.approx(80.0)
        assert reading["distinct_options"] == 3
        assert reading["top_option_share"].pct == pytest.approx(80.0)
        # Eight of ten were tapped before the words landed.
        assert reading["instant"].pct == pytest.approx(80.0)

    def test_position_bias_is_measured_against_computed_chance(self, kid: Child) -> None:
        """The design check: is the first card taken more often than chance?

        Deliberately a *mixed* set of beats — six offering three options, four
        offering two — because that is the case a flat "one over the number of
        rows" yardstick gets wrong. Chance for the first card here is
        6 x 1/3 + 4 x 1/2 = 4.0 of 10, i.e. 40%, not 33%.
        """
        # Every one of the ten choices takes the first card.
        for _ in range(6):
            _event(
                Event.CHOICE_MADE,
                ts=_at(days=1),
                child=kid,
                chosen_key="red_leaf",
                option_index=0,
                option_count=3,
                latency_ms=3000,
                retries=0,
                assisted=False,
                learning_ask=False,
            )
        for _ in range(4):
            _event(
                Event.CHOICE_MADE,
                ts=_at(days=1),
                child=kid,
                chosen_key="continue",
                option_index=0,
                option_count=2,
                latency_ms=3000,
                retries=0,
                assisted=False,
                learning_ask=False,
            )

        reading = insights.interaction(_window())
        assert reading["positioned"] == 10
        assert reading["unpositioned"] == 0

        first, second, third = reading["positions"]
        assert first["label"] == "1st"
        assert first["count"] == 10
        assert first["expected"] == pytest.approx(4.0)
        assert first["expected_pct"] == pytest.approx(40.0)
        # Taken 2.5x as often as chance alone would give it.
        assert first["lift"] == pytest.approx(2.5)

        # A position that was offered and never taken is a row of zeros, not a
        # missing row: an option children never pick is the finding.
        assert second["count"] == 0
        assert second["lift"] == pytest.approx(0.0)
        # The third card was only ever offered by the three-option beats.
        assert third["expected"] == pytest.approx(2.0)

    def test_choices_with_no_position_are_excluded_not_counted_as_the_first_card(
        self, kid: Child
    ) -> None:
        """Events written before `option_index` existed must not read as bias.

        Counting a missing position as 0 would draw a first-card habit out of
        nothing but history, on the exact week the field shipped.
        """
        _event(
            Event.CHOICE_MADE,
            ts=_at(days=1),
            child=kid,
            chosen_key="red_leaf",
            option_index=1,
            option_count=2,
            latency_ms=3000,
            retries=0,
            assisted=False,
            learning_ask=False,
        )
        for _ in range(5):  # the old shape: no position at all
            _event(
                Event.CHOICE_MADE,
                ts=_at(days=1),
                child=kid,
                chosen_key="continue",
                latency_ms=3000,
                retries=0,
                assisted=False,
                learning_ask=False,
            )

        reading = insights.interaction(_window())
        assert reading["choices"] == 6
        assert reading["positioned"] == 1
        assert reading["unpositioned"] == 5
        # The one positioned choice took the *second* card, so the first card must
        # not have been credited with the five that carry no position.
        assert reading["positions"][0]["count"] == 0
        assert reading["positions"][1]["count"] == 1

    def test_the_position_field_is_recorded_and_the_flag_says_so(self, kid: Child) -> None:
        """`POSITION_IS_INSTRUMENTED` is what the page consults, so it is asserted."""
        assert insights.POSITION_IS_INSTRUMENTED is True
        assert insights.interaction(_window())["position_instrumented"] is True

    def test_help_is_split_by_whether_the_beat_was_a_learning_ask(self, kid: Child) -> None:
        """The same number means two opposite things, so it is never one number."""
        _event(
            Event.CHOICE_MADE,
            ts=_at(days=1),
            child=kid,
            chosen_key="a",
            latency_ms=3000,
            retries=0,
            assisted=True,
            learning_ask=True,
        )
        _event(
            Event.CHOICE_MADE,
            ts=_at(days=1),
            child=kid,
            chosen_key="b",
            latency_ms=3000,
            retries=1,
            assisted=False,
            learning_ask=False,
        )

        reading = insights.interaction(_window())
        assert reading["assisted_on_learning"].pct == pytest.approx(100.0)
        assert reading["assisted_on_story"].pct == pytest.approx(0.0)
        assert reading["retried_on_story"].pct == pytest.approx(100.0)
        assert reading["learning_share"].pct == pytest.approx(50.0)


# ---------------------------------------------------------------------------
class TestThePositionIsActuallyEmitted:
    """The reading above is only as true as the emit site, so play for real.

    Every other test in this class family writes events by hand, which proves the
    arithmetic and proves nothing about whether `apps.story.orchestrator` puts
    `option_index` on the wire. That gap is exactly how the original mistake
    survived: the field was *described* correctly in a comment and was never
    checked. This plays a story through the real orchestrator and reads the event
    it wrote.
    """

    def test_a_real_choice_records_where_the_option_sat(self, aarav: Child, fake_ai: Any) -> None:
        adventure = play(aarav)
        finish(adventure)

        choices = list(Event.objects.filter(name=Event.CHOICE_MADE, child=aarav).order_by("ts"))
        assert choices, "playing an adventure should record at least one choice"

        positioned = [event for event in choices if event.props.get("option_index") is not None]
        assert positioned, (
            "no choice carried an option_index. `record_choice` must put the "
            "chosen option's position on the event, or the position-bias check on "
            "/admax is measuring nothing."
        )

        for event in positioned:
            index = event.props["option_index"]
            offered = event.props["option_count"]
            assert isinstance(index, int) and not isinstance(index, bool)
            assert isinstance(offered, int) and not isinstance(offered, bool)
            # The position has to be inside the set of options it indexes, or the
            # chance arithmetic is dividing by the wrong denominator.
            assert 0 <= index < offered, f"option_index {index} outside 0..{offered - 1}"

    def test_the_reading_picks_it_up_end_to_end(self, aarav: Child, fake_ai: Any) -> None:
        """From a played story to the number the page renders, with no hand-written
        events in between."""
        finish(play(aarav))

        reading = insights.interaction(Window.last(1))
        assert reading["positioned"] > 0
        assert sum(row["count"] for row in reading["positions"]) == reading["positioned"]
        # Every row's chance share is a real fraction of the positioned choices.
        assert all(row["expected"] <= reading["positioned"] + 1e-9 for row in reading["positions"])


# ---------------------------------------------------------------------------
class TestAudienceAndContent:
    def test_parental_settings_are_read_off_the_props(self, kid: Child) -> None:
        _event(
            Event.CHILD_CREATED,
            ts=_at(days=1),
            child=kid,
            age_band="5-6",
            interests=3,
            fears=1,
            avoid_topics=0,
            intensity_ceiling=1,
            session_cap_min=15,
        )
        reading = insights.audience(_window())
        assert reading["age_bands"][0]["label"] == "5-6"
        assert reading["session_caps"][0]["label"] == "15 min"
        assert reading["seeded"].pct == pytest.approx(100.0)
        assert reading["with_avoid_topics"].pct == pytest.approx(0.0)

    def test_the_safety_mix_is_a_share_of_every_beat(self, kid: Child) -> None:
        for _ in range(9):
            _event(
                Event.BEAT_DELIVERED,
                ts=_at(days=1),
                child=kid,
                adventure_id="adv-1",
                seq=1,
                safety_status="pass",
                interaction="choice",
                learning_ask=False,
            )
        _event(
            Event.BEAT_DELIVERED,
            ts=_at(days=1),
            child=kid,
            adventure_id="adv-1",
            seq=2,
            safety_status="fallback",
            interaction="choice",
            learning_ask=True,
        )

        reading = insights.content_health(_window())
        assert reading["beats"] == 10
        assert reading["safety"][0]["label"] == "pass"
        assert reading["safety"][1]["label"] == "fallback"
        assert reading["safety"][1]["share"].pct == pytest.approx(10.0)
        assert reading["learning_asks"].pct == pytest.approx(10.0)


# ---------------------------------------------------------------------------
class TestTheCatalogue:
    """The data dictionary is the page's own explanation of itself."""

    def test_every_event_the_product_can_emit_has_a_row(self) -> None:
        rows = insights.catalogue(_window())
        assert {row["name"] for row in rows} == set(Event.NAMES)

    def test_every_event_is_described(self) -> None:
        """An event added without a line in `CATALOGUE` must be visible, not silent."""
        described = {name for name, _, _, _ in insights.CATALOGUE}
        assert described == set(Event.NAMES), (
            "Every event needs a line in insights.CATALOGUE saying what it carries, "
            "what it withholds and what it powers - that table is what makes the "
            "dashboard's numbers readable by somebody who did not build it."
        )

    def test_counts_come_from_the_window(self, kid: Child) -> None:
        _event(Event.ADVENTURE_STARTED, ts=_at(days=1), child=kid)
        counts = {row["name"]: row["count"] for row in insights.catalogue(_window())}
        assert counts[Event.ADVENTURE_STARTED] == 1
        assert counts[Event.SAFETY_INCIDENT] == 0


# ---------------------------------------------------------------------------
class TestTheDashboardRenders:
    """Both templates, against a real context, with data and without.

    A reading that computes perfectly and raises `TemplateSyntaxError` on the box
    that has it is not a dashboard, and no other test in this suite would notice.
    The empty case is tested too, because every "no data yet" branch on the page is
    a branch nothing else exercises — and the first person to open this page will
    be looking at exactly that.
    """

    def _render(self, template: str) -> str:
        context = internal.dashboard_context(7, zone=KOLKATA)
        context["operator"] = None
        return render_to_string(template, context)

    @pytest.mark.parametrize("template", ["analytics/admax.html", "analytics/metrics.html"])
    def test_it_renders_when_nothing_has_happened(self, db: Any, template: str) -> None:
        html = self._render(template)
        assert "The Primer" in html
        assert "Asia/Kolkata" in html

    @pytest.mark.parametrize("template", ["analytics/admax.html", "analytics/metrics.html"])
    def test_it_renders_with_data(self, parent: Any, kid: Child, template: str) -> None:
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_at(days=2), props={})
        _event(Event.CHILD_CREATED, ts=_at(days=2), child=kid, age_band="5-6", interests=2)
        _event(Event.COMPANION_CREATED, ts=_at(days=2), child=kid, archetype="dragon")
        _event(Event.ADVENTURE_STARTED, ts=_at(days=1), child=kid, world="tidepool", difficulty=0.4)
        _event(
            Event.BEAT_DELIVERED,
            ts=_at(days=1),
            child=kid,
            adventure_id="adv-1",
            seq=1,
            safety_status="pass",
            interaction="choice",
            learning_ask=False,
        )
        _event(
            Event.CHOICE_MADE,
            ts=_at(days=1),
            child=kid,
            chosen_key="a",
            latency_ms=4200,
            retries=0,
            assisted=False,
            learning_ask=False,
        )
        _event(Event.ADVENTURE_COMPLETED, ts=_at(days=1), child=kid, world="tidepool", beats=1)
        _event(Event.SESSION_ENDED, ts=_at(days=1), child=kid, duration_s=540, beats=1)

        html = self._render(template)
        assert "insights-parent@example.com" in html  # the roster names the account
        assert "tidepool" in html
        assert kid.first_name not in html  # and never the child

    @pytest.mark.parametrize("template", ["analytics/admax.html", "analytics/metrics.html"])
    def test_no_template_syntax_survives_into_the_page(self, db: Any, template: str) -> None:
        """Anything that still looks like template source is source that did not run.

        `{#` is in this list because of a real bug it caught: Django's short comment
        form is **single-line only**, and a multi-line `{# … #}` renders its own text
        onto the page as a paragraph. Nothing else here would have noticed — the page
        rendered, every number was right, and there was an explanatory comment sitting
        in the middle of the controls.
        """
        html = self._render(template)
        for token in ("{%", "{{", "{#"):
            assert token not in html, f"unrendered template syntax {token!r} in {template}"


# ---------------------------------------------------------------------------
class TestTheTerminalReportStaysInStep:
    """`manage.py metrics` and the page are one report rendered twice."""

    def test_it_prints_the_behavioural_sections(self, parent: Any, kid: Child) -> None:
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_at(days=1), props={})
        _event(Event.ADVENTURE_STARTED, ts=_at(days=1), child=kid, world="tidepool")
        _event(Event.SESSION_ENDED, ts=_at(days=1), child=kid, duration_s=600, beats=4)

        text = internal.as_text(7, now=NOW, tz="Asia/Kolkata")
        assert "When families are here (clock: Asia/Kolkata)" in text
        assert "Where the time goes" in text
        assert "How far into a story they get" in text
        assert "tidepool" in text

    def test_it_stays_ascii_for_the_windows_console(self, kid: Child) -> None:
        """The `metrics` command's own comment promises this; here it is asserted."""
        _event(Event.ADVENTURE_STARTED, ts=_at(days=1), child=kid, world="tidepool")
        _event(Event.SESSION_ENDED, ts=_at(days=1), child=kid, duration_s=600, beats=4)

        text = internal.as_text(7, now=NOW)
        offenders = sorted({char for char in text if ord(char) > 127})
        assert not offenders, f"non-ASCII in the terminal report: {offenders}"

    def test_the_roster_is_not_printed_to_a_terminal(self, parent: Any) -> None:
        """The one table that names people stays on the page behind two factors."""
        Event.objects.create(name=Event.PARENT_SIGNED_UP, parent=parent, ts=_at(days=1), props={})
        text = internal.as_text(7, now=NOW)
        assert "insights-parent@example.com" not in text
