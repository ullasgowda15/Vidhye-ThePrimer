"""How long a child actually played, as opposed to how long the row was open.

A sitting's duration used to be wall clock — `ended_at - started_at` — and that
is only the same thing when the sitting is closed at the moment the child stops.
It often is not. A child who opens a story and wanders off leaves the session row
open; it is finalised whenever the app next runs the cap check, which may be two
days later. Production had exactly that: one sitting of **2,931 minutes** beside
four of eight to sixteen, and it was not a long sitting, it was a Sunday
afternoon plus two nights of nothing.

That single row moved five readings at once — total minutes played, the p90
sitting, minutes-per-world, the §22.5 "over 30 min" wellbeing guardrail, and the
duration on the parent's own session report, which would have told a parent their
five-year-old played for two days.

**So duration is measured to the last thing the child did, not to the moment the
row was closed.** `IDLE_GRACE` is what keeps that honest in the ordinary
direction: the last beat is created when it is *delivered*, and a child then
listens to it, thinks, and taps — none of which writes a row. Ending the clock at
the last beat would undercount every normal sitting by that tail. So the rule is:

    duration = min(ended_at, last_activity + IDLE_GRACE) - started_at

A sitting closed while the child is still there keeps its real length, because
`ended_at` is already inside the grace. A sitting closed days later is cut back
to the last moment there is any evidence a child was present. Nothing here can
report *more* time than wall clock, which is the direction that matters: this
number is a wellbeing guardrail before it is a metric, and it must never overstate
how long a child was on a screen.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from django.db.models import Max

if TYPE_CHECKING:
    from apps.story.models import PlaySession

#: How long after the last recorded event a child is still credited as playing.
#:
#: Covers the tail a sitting genuinely has and no database row marks: hearing the
#: final beat, looking at the picture, saying goodbye. Five minutes is longer than
#: that tail and far shorter than the gap that makes a sitting abandoned, so it
#: separates the two cases without needing to know which one it is looking at.
IDLE_GRACE = datetime.timedelta(minutes=5)


def last_activity_at(session: PlaySession) -> datetime.datetime | None:
    """When this sitting last shows any sign of a child, or `None`.

    Beats and choices both count, and the later of the two wins. A choice is the
    stronger signal — it is the child acting — but a sitting can end on a beat
    nobody answered, and that beat was still delivered to somebody.

    Two queries rather than a join: they are indexed lookups on the adventure FK
    and this runs once when a sitting closes, not per beat.
    """
    from apps.story.models import Beat, Choice

    last_beat = Beat.objects.filter(adventure__session=session).aggregate(at=Max("created_at"))[
        "at"
    ]
    last_choice = Choice.objects.filter(adventure__session=session).aggregate(at=Max("created_at"))[
        "at"
    ]

    stamps = [stamp for stamp in (last_beat, last_choice) if stamp is not None]
    return max(stamps) if stamps else None


def played_seconds(session: PlaySession, *, ended_at: datetime.datetime | None = None) -> int:
    """Seconds this sitting should be credited with. Never negative.

    `ended_at` is an argument so the caller can pass the moment it is *about* to
    stamp, before saving. Falls back to the row's own value.

    With no beat and no choice there is nothing to measure — a child opened the
    app and did not get a story — and that is `0` rather than the wall clock,
    which for an abandoned empty session is the whole gap.
    """
    close = ended_at or session.ended_at
    if close is None:
        return 0

    last = last_activity_at(session)
    if last is None:
        return 0

    effective = min(close, last + IDLE_GRACE)
    return max(0, int((effective - session.started_at).total_seconds()))
