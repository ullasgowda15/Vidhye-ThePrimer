"""How long a sitting is credited with (`apps.story.duration`).

The bug these exist for shipped and was found on the production dashboard: one
sitting of 2,931 minutes beside four of eight to sixteen. Nothing was wrong with
the arithmetic — the row really had been open for two days. What was wrong was
calling that playing.

The direction matters more than the precision. A sitting must never be credited
with *more* time than a child was there for, because this number is a §22.5
wellbeing guardrail before it is a metric, and because it is shown to a parent.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.utils import timezone

from apps.story import duration
from apps.story.models import Adventure, Beat, PlaySession

pytestmark = pytest.mark.django_db


def _session(child: Any, *, started_ago: datetime.timedelta) -> PlaySession:
    session = PlaySession.objects.create(child=child)
    # `started_at` is `auto_now_add`, so it has to be moved after the fact.
    PlaySession.objects.filter(pk=session.pk).update(started_at=timezone.now() - started_ago)
    session.refresh_from_db()
    return session


def _beat_at(session: PlaySession, *, ago: datetime.timedelta) -> Beat:
    adventure = Adventure.objects.create(child=session.child, session=session)
    beat = Beat.objects.create(adventure=adventure, seq=1)
    Beat.objects.filter(pk=beat.pk).update(created_at=timezone.now() - ago)
    beat.refresh_from_db()
    return beat


class TestASittingClosedWhileTheChildIsThere:
    def test_it_keeps_its_real_length(self, child: Any) -> None:
        # Started twelve minutes ago, last beat one minute ago, closing now. The
        # close is inside the grace, so wall clock is the honest answer and the
        # new rule must not shorten it.
        session = _session(child, started_ago=datetime.timedelta(minutes=12))
        _beat_at(session, ago=datetime.timedelta(minutes=1))

        seconds = duration.played_seconds(session, ended_at=timezone.now())

        assert 11 * 60 <= seconds <= 12 * 60 + 5

    def test_the_tail_after_the_last_beat_still_counts(self, child: Any) -> None:
        # A beat is stamped when it is *delivered*; the child then listens, thinks
        # and taps, and none of that writes a row. Ending the clock at the last
        # beat would undercount every ordinary sitting by that tail.
        session = _session(child, started_ago=datetime.timedelta(minutes=10))
        _beat_at(session, ago=datetime.timedelta(minutes=3))

        seconds = duration.played_seconds(session, ended_at=timezone.now())

        assert seconds >= 9 * 60


class TestASittingNobodyClosed:
    def test_two_days_open_is_not_two_days_of_playing(self, child: Any) -> None:
        """The production row, reproduced.

        Started two days ago, last beat two days ago, finalised now by the cap
        check. Wall clock says 2,880 minutes; the child was there for about ten.
        """
        session = _session(child, started_ago=datetime.timedelta(days=2))
        _beat_at(session, ago=datetime.timedelta(days=2) - datetime.timedelta(minutes=10))

        seconds = duration.played_seconds(session, ended_at=timezone.now())

        assert seconds < 20 * 60, "an abandoned sitting must not be credited as playing"
        assert seconds >= 10 * 60, "the part the child was actually there for still counts"

    def test_it_never_credits_more_than_the_wall_clock(self, child: Any) -> None:
        # The grace must not run past the close: a sitting ended one minute after
        # its only beat is one minute long, not six.
        session = _session(child, started_ago=datetime.timedelta(minutes=6))
        _beat_at(session, ago=datetime.timedelta(minutes=5))
        ended = timezone.now()

        seconds = duration.played_seconds(session, ended_at=ended)

        assert seconds <= int((ended - session.started_at).total_seconds())


class TestASittingWithNothingInIt:
    def test_an_empty_sitting_is_zero_not_the_whole_gap(self, child: Any) -> None:
        # A child opened the app and never got a story. The row may sit open for
        # days; none of it is playing.
        session = _session(child, started_ago=datetime.timedelta(days=1))

        assert duration.played_seconds(session, ended_at=timezone.now()) == 0

    def test_an_open_sitting_has_no_duration_yet(self, child: Any) -> None:
        session = _session(child, started_ago=datetime.timedelta(minutes=5))
        _beat_at(session, ago=datetime.timedelta(minutes=1))

        assert duration.played_seconds(session) == 0
