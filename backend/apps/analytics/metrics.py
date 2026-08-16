"""The product metrics, computed from events (PRODUCT.md §22.1, §22.3, §5.6).

The read end of the pipeline `apps.analytics.events` writes. Five numbers, and
they are the five §22.3 names:

| Metric | §22.3 definition | Early target |
|---|---|---|
| Activation | parent onboards **and** child completes first story | ≥70% |
| Day-2 return | child returns the day after their first story | ≥40% |
| W1 retention | child active in the week after their first story | ≥55% |
| Completion rate | adventures finished ÷ started | ≥80% |
| Adventures/child/week | volume of completed adventures — the North Star input | ≥3 |

Those five say how well the product is doing. Four more say *why*, and they carry
no §22.3 target because §22.3 does not name them — they are diagnostics, and a
target invented for a diagnostic is a target nobody earned:

| Metric | Question it answers |
|---|---|
| `funnel` | Which onboarding step lost them |
| `active_children` | DAU / WAU / MAU and stickiness — how many are still here |
| `retention_cohorts` | Whether retention is getting better, week-cohort by week-cohort |
| `world_popularity` | What children actually pick, and what they finish |

**Everything here reads `Event` and nothing else.** Not because the source tables
would give a wrong answer today, but because §5.6 is a pipeline: the moment these
are computed from `Adventure` and those from `Event`, the dashboard has two
sources that will disagree, and no one will know which is the metric. The
`Event` table is also the thing that survives §17.5's pruning and §13.7's
deletes, which is what lets a September completion rate stay the number it was.

**Definitions are stated, not implied.** Every §22.3 line is directional prose;
turning it into SQL requires choices, and an unstated choice is how two people
read the same dashboard differently. Each function's docstring says what it
decided and what the honest alternative was. The decisions that matter most:

* *Cohorts are anchored on the child's first story, not on the window.* "Day-2
  return" is a fact about a child's second day, so a child whose first story was
  yesterday cannot be in the denominator of a metric that needs tomorrow to
  exist. `_matured` is where that is enforced, once.
* *Calendar days, in the server's timezone.* Same honest gap the Day-13 time
  window has: `Child.locale` carries a language and not a zone, so "the next day"
  is the server's next day. Written down rather than hidden.

**This module is the product/ops half of §5.6 and must never be imported by a
parent-facing view.** `tests/test_analytics_pipeline.py::TestTheSeparationIsStructural`
asserts that, over the import graph.

*Scale note:* the queries here fan out per child in a couple of places. That is
deliberate at alpha scale — a loop over a few hundred children is readable and a
window function is not — and it is the first thing to rewrite when the numbers
get real. It is flagged where it happens.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.analytics.models import Event

if TYPE_CHECKING:
    from collections.abc import Iterable

#: The North Star's threshold (§22.1): a Delighted Weekly Active is a child who
#: completed **three or more** adventures in a week. Named because it appears in
#: two places and a literal `3` in either would be unreadable.
DWA_ADVENTURES = 3

#: How long "the week after the first story" is, in days (§22.3's W1).
W1_DAYS = 7


@dataclass(frozen=True)
class Window:
    """A half-open interval `[since, until)`.

    Half-open so consecutive windows tile without double-counting the instant they
    share — a day boundary counted twice is the classic way a funnel exceeds 100%.
    """

    since: datetime.datetime
    until: datetime.datetime

    @classmethod
    def last(cls, days: int, *, now: datetime.datetime | None = None) -> Window:
        end = now or timezone.now()
        return cls(since=end - datetime.timedelta(days=days), until=end)

    def contains(self, moment: datetime.datetime) -> bool:
        return self.since <= moment < self.until


@dataclass(frozen=True)
class Rate:
    """A metric as a fraction, with both of the numbers that made it.

    Never just a percentage. A 100% activation rate over two signups and one over
    two hundred are different facts, and a dashboard that shows only the ratio
    invites the first to be read as the second. `value` is `None` — not `0.0` —
    when the denominator is empty, because "nobody has reached this step yet" is
    not "everybody who reached it failed".
    """

    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    @property
    def pct(self) -> float | None:
        return round(100 * value, 1) if (value := self.value) is not None else None


def _localdate(moment: datetime.datetime) -> datetime.date:
    """The calendar date a moment falls on, in the server's timezone.

    One function so the timezone caveat in the module docstring has exactly one
    place to be fixed when `Child` learns to carry a zone.
    """
    return timezone.localtime(moment).date()


def _first_stories() -> dict[Any, tuple[datetime.datetime, datetime.date]]:
    """Every child's first *completed* story, as `(moment, calendar date)`.

    The anchor for three of the five metrics. Completed rather than started,
    because §22.3's activation is "child completes first story" and a child who
    began one and wandered off has not had the first-magic moment the metric is
    about.

    Both the moment and the date, because they answer different questions and
    conflating them is a real off-by-one: cohort membership is "was the first
    story inside the window", which needs the moment, and "the next day" is a
    calendar question, which needs the date. A child whose first story was at
    23:50 belongs to that evening's cohort and returns the following morning.
    """
    first: dict[Any, tuple[datetime.datetime, datetime.date]] = {}
    rows = (
        Event.objects.filter(name=Event.ADVENTURE_COMPLETED, child_id__isnull=False)
        .order_by("child_id", "ts")
        .values_list("child_id", "ts")
    )
    for child_key, ts in rows.iterator():
        if child_key not in first:
            first[child_key] = (ts, _localdate(ts))
    return first


def _play_dates() -> dict[Any, set[datetime.date]]:
    """Every calendar date each child started an adventure on.

    *Started*, not completed: "did the child come back" is a question about
    returning, and a child who came back, opened a story and was called to dinner
    came back. Retention that required completion would be measuring two things at
    once.
    """
    dates: dict[Any, set[datetime.date]] = {}
    rows = (
        Event.objects.filter(name=Event.ADVENTURE_STARTED, child_id__isnull=False)
        .annotate(day=TruncDate("ts"))
        .values_list("child_id", "day")
        .distinct()
    )
    for child_key, day in rows:
        dates.setdefault(child_key, set()).add(day)
    return dates


def _matured(
    first: dict[Any, tuple[datetime.datetime, datetime.date]],
    *,
    window: Window,
    needs_days: int,
    today: datetime.date,
) -> list[tuple[Any, datetime.date]]:
    """Children whose first story is in `window` and old enough to be judged.

    The guard that keeps every retention number honest. A child whose first story
    was this morning cannot yet have returned tomorrow; counting them as a miss
    would drag day-2 return down by exactly the number of children who joined most
    recently — so a good week of signups would *lower* the metric. They are left
    out of the denominator until the day arrives.

    Returns `(child_key, first-story date)`, because that date is what every
    caller counts days from.
    """
    return [
        (child_key, day)
        for child_key, (moment, day) in first.items()
        if window.contains(moment) and day + datetime.timedelta(days=needs_days) <= today
    ]


# ---------------------------------------------------------------------------
# §22.3 — the five metrics
# ---------------------------------------------------------------------------


def activation(window: Window) -> Rate:
    """Parents who onboarded **and** whose child completed a first story (§22.3).

    *Denominator:* accounts that signed up inside the window. Anchoring on signup
    rather than on activity is what makes this a funnel rather than a popularity
    contest — a cohort that never came back has to stay in it.

    *Numerator:* of those, the ones that reached both `onboarding.completed` and
    at least one `adventure.completed`, **at any later time**. Activation is not
    required to happen inside the window; a parent who signed up on Sunday and
    whose child finished a story on Tuesday activated.

    Both conditions are required rather than just the second, because §22.3 says
    "onboards **and**" — a child can only complete a story after onboarding, but
    checking both is what makes the funnel readable when the two numbers differ,
    and they differ the day a companion is created outside the flow.
    """
    signups = set(
        Event.objects.filter(
            name=Event.PARENT_SIGNED_UP,
            ts__gte=window.since,
            ts__lt=window.until,
            parent_id__isnull=False,
        ).values_list("parent_id", flat=True)
    )
    if not signups:
        return Rate(0, 0)

    onboarded = set(
        Event.objects.filter(name=Event.ONBOARDING_COMPLETED, parent_id__in=signups).values_list(
            "parent_id", flat=True
        )
    )
    played = set(
        Event.objects.filter(name=Event.ADVENTURE_COMPLETED, parent_id__in=signups).values_list(
            "parent_id", flat=True
        )
    )
    return Rate(len(signups & onboarded & played), len(signups))


def day2_return(window: Window, *, today: datetime.date | None = None) -> Rate:
    """Children who came back the day after their first story (§22.3).

    *Denominator:* children whose first completed story falls in the window and
    was at least a day ago (see `_matured`).

    *Numerator:* of those, the ones who started an adventure on the **next
    calendar day**. Calendar-day rather than 24-hour: a child who plays at 6pm on
    Monday and 9am on Tuesday came back the next day, and a 24-hour rule would
    say they did not. It is also the definition a parent would recognise.
    """
    today = today or _localdate(timezone.now())
    first = _first_stories()
    played = _play_dates()

    cohort = _matured(first, window=window, needs_days=1, today=today)
    returned = sum(
        1
        for child_key, day in cohort
        if (day + datetime.timedelta(days=1)) in played.get(child_key, set())
    )
    return Rate(returned, len(cohort))


def w1_retention(window: Window, *, today: datetime.date | None = None) -> Rate:
    """Children active in the week after their first story (§22.3's W1).

    *Denominator:* children whose first completed story falls in the window and
    was at least seven days ago.

    *Numerator:* of those, the ones who started an adventure on any of days 1-7
    after that first story. Day 0 is excluded on purpose — the first story itself
    is not evidence of returning, and including it would make W1 retention
    unfalsifiable.

    Anchored on the child's own first story rather than on a fixed calendar week,
    which is the other common reading. Per-child anchoring is the one that answers
    "does the second week happen", and it is the reading §4.5-§4.6's journey
    describes.
    """
    today = today or _localdate(timezone.now())
    first = _first_stories()
    played = _play_dates()

    cohort = _matured(first, window=window, needs_days=W1_DAYS, today=today)
    retained = 0
    for child_key, day in cohort:
        days = played.get(child_key, set())
        if any(day + datetime.timedelta(days=offset) in days for offset in range(1, W1_DAYS + 1)):
            retained += 1
    return Rate(retained, len(cohort))


def completion_rate(window: Window) -> Rate:
    """Adventures finished ÷ adventures started, inside the window (§22.3).

    Both counts are of events whose timestamp is in the window, which means an
    adventure started at 23:58 and finished at 00:02 counts in the denominator of
    one day and the numerator of the next. At a week's width — which is how this
    is read — the effect is a rounding error, and the alternative (join every
    completion back to its start) needs the raw rows that §17.5 prunes.

    Not every started adventure emits a completion: an abandoned one simply does
    not, which is what makes this a rate rather than a ratio of two labels.
    `ended_reason` on `session.ended` is where *why* lives (§13.6).
    """
    counts = dict(
        Event.objects.filter(
            name__in=[Event.ADVENTURE_STARTED, Event.ADVENTURE_COMPLETED],
            ts__gte=window.since,
            ts__lt=window.until,
        )
        .values_list("name")
        .annotate(n=Count("id"))
    )
    return Rate(
        counts.get(Event.ADVENTURE_COMPLETED, 0),
        counts.get(Event.ADVENTURE_STARTED, 0),
    )


def weekly_adventures(window: Window) -> list[dict[str, Any]]:
    """Adventures per child per week, and the North Star that rides on it (§22.1).

    One row per ISO week that overlaps the window, each carrying:

    * `active_children` — children who completed at least one adventure that week.
      The denominator is *active* children rather than all children, because
      "adventures per child per week" is a question about the habit of the
      children who are playing, and dividing by everyone who ever signed up turns
      it into a retention metric wearing an engagement metric's name.
    * `adventures` — completed adventures that week.
    * `per_child` — the ratio. §22.3's ≥3 target.
    * `dwa` — **Delighted Weekly Actives**: children with ≥3 completed adventures
      that week. §22.1's North Star, and the reason this function exists at all.
      It is a count and not a rate on purpose: §22.1 defines DWA as a number of
      children, and "raw time-on-app" is exactly what it refuses to be.

    Weeks start Monday (ISO), so a week is the same seven days for everybody.
    """
    rows = (
        Event.objects.filter(
            name=Event.ADVENTURE_COMPLETED,
            ts__gte=window.since,
            ts__lt=window.until,
            child_id__isnull=False,
        )
        .annotate(day=TruncDate("ts"))
        .values_list("child_id", "day")
    )

    # child -> week-start -> count. A dict rather than a `TruncWeek` aggregate
    # because Django's week truncation starts on Sunday and §22 reads in ISO
    # weeks; doing it here keeps one definition of "a week" instead of two.
    per_week: dict[datetime.date, dict[Any, int]] = {}
    for child_key, day in rows:
        start = day - datetime.timedelta(days=day.weekday())
        bucket = per_week.setdefault(start, {})
        bucket[child_key] = bucket.get(child_key, 0) + 1

    weeks: list[dict[str, Any]] = []
    for start in sorted(per_week):
        counts = per_week[start]
        adventures = sum(counts.values())
        children = len(counts)
        weeks.append(
            {
                "week_start": start,
                "active_children": children,
                "adventures": adventures,
                "per_child": adventures / children if children else None,
                "dwa": sum(1 for n in counts.values() if n >= DWA_ADVENTURES),
            }
        )
    return weeks


# ---------------------------------------------------------------------------
# Where a family falls out, how many are still here, and what they picked
# ---------------------------------------------------------------------------
#
# The four below are not in §22.3's list. They are here because §22.3's five
# numbers say *how well* the product is doing and none of them says *why*, and
# the four questions immediately downstream of a bad number all have the same
# shape: which step lost them, how many children are still playing at all, does
# the second week happen, and what did they choose. Each is computed from `Event`
# under exactly the rules the rest of this module follows.


#: The onboarding sequence, in the order the product actually makes a family walk
#: it. `parent.consent_recorded` is deliberately **not** a step: it is a
#: compliance fact that can legitimately be recorded on more than one path, and a
#: step that some families satisfy off-funnel shows up as a drop-off that is not
#: one. It is counted, like everything else, in `event_counts`.
FUNNEL_STEPS: tuple[tuple[str, str], ...] = (
    (Event.PARENT_SIGNED_UP, "Signed up"),
    (Event.CHILD_CREATED, "Added a child"),
    (Event.COMPANION_CREATED, "Made a companion"),
    (Event.ONBOARDING_COMPLETED, "Finished onboarding"),
    (Event.ADVENTURE_STARTED, "Started a first story"),
    (Event.ADVENTURE_COMPLETED, "Finished a first story"),
)


def funnel(window: Window) -> list[dict[str, Any]]:
    """The signup cohort's walk to a finished first story, step by step.

    `activation` answers "how many made it all the way"; this answers "and where
    did the rest stop", which is the only version of the question you can act on.
    One row per `FUNNEL_STEPS` entry, carrying the count, the share of the top of
    the funnel, **the share of the step immediately before**, and how many were
    lost at this step. The step-to-step column is the one to read: a funnel judged
    only against its top makes every step after a bad one look bad too.

    *Cohort:* accounts that signed up inside the window — the same denominator
    `activation` uses, and for the same reason. Reaching a later step is **not**
    required to happen inside the window, so a parent who signed up on Sunday and
    whose child finished on Tuesday is counted at every step they reached.

    *Steps are counted independently, not nested.* A row is "parents in the cohort
    who ever emitted this event", not "…and every event above it". The nested
    reading guarantees a monotonic chart, which is exactly why it is the wrong one
    here: it silently reassigns a family who skipped a step to the step they
    skipped, and the product already enforces the order (there is no companion
    without a child, no adventure without a companion), so the counts come out
    monotonic on their own. **A step that reads higher than the one above it is
    therefore not a drawing artefact — it means the step above stopped emitting**,
    and that is worth seeing rather than smoothing away.
    """
    signups = set(
        Event.objects.filter(
            name=Event.PARENT_SIGNED_UP,
            ts__gte=window.since,
            ts__lt=window.until,
            parent_id__isnull=False,
        ).values_list("parent_id", flat=True)
    )

    rows: list[dict[str, Any]] = []
    top = len(signups)
    previous: int | None = None

    for name, label in FUNNEL_STEPS:
        if name == Event.PARENT_SIGNED_UP:
            reached = top
        else:
            reached = (
                Event.objects.filter(name=name, parent_id__in=signups)
                .values("parent_id")
                .distinct()
                .count()
            )

        rows.append(
            {
                "name": name,
                "label": label,
                "reached": reached,
                # `None` rather than 0.0 wherever the denominator is empty, for the
                # reason `Rate.value` is: no-one has reached this step yet is not
                # everyone who reached it failed.
                "pct_of_top": round(100 * reached / top, 1) if top else None,
                "pct_of_previous": (round(100 * reached / previous, 1) if previous else None),
                # Floored at zero: a negative loss is the non-monotonic case above,
                # and it is reported as the step count itself rather than as a
                # nonsense number here.
                "lost_here": max(previous - reached, 0) if previous is not None else 0,
            }
        )
        previous = reached

    return rows


#: The two windows "active" is read over, in days. Twenty-eight rather than
#: thirty for the monthly one, so it is exactly four of the ISO weeks every other
#: number on the page is bucketed by — a "month" that is 30 days contains a
#: different number of weekends depending on when you ask, and weekends are when
#: this product is used.
WAU_DAYS = 7
MAU_DAYS = 28


def _distinct_active(since: datetime.datetime, until: datetime.datetime) -> int:
    """Children who started an adventure in `[since, until)`.

    *Started*, matching `_play_dates`: being active is showing up, and requiring a
    completion would make an engagement number partly a completion number.
    """
    return (
        Event.objects.filter(
            name=Event.ADVENTURE_STARTED,
            ts__gte=since,
            ts__lt=until,
            child_id__isnull=False,
        )
        .values("child_id")
        .distinct()
        .count()
    )


def active_children(window: Window) -> dict[str, Any]:
    """How many children are still playing: daily, weekly, monthly, and how sticky.

    Anchored on `window.until` rather than on `now`, so the whole page describes
    one moment and a 30-day view does not quietly mix a 30-day funnel with a
    today-shaped DAU.

    **`avg_dau` is the number to read, not `dau`.** A single day's DAU on an alpha
    with tens of children is mostly noise about which evening you looked, so the
    average over the window is reported beside it and is what `stickiness` uses.
    The average divides by **every day in the window**, including the days nobody
    played — dividing by the days that had activity would report a product used
    once a week as though its quiet days did not exist.

    **`stickiness` is `avg_dau / wau`** — of the children who played this week, the
    share who play on any given day. For a bedtime product a low number is not
    automatically bad news, which is exactly why it is reported as a ratio to be
    read rather than against a target this product has not earned yet.
    """
    until = window.until
    day = datetime.timedelta(days=1)

    rows = (
        Event.objects.filter(
            name=Event.ADVENTURE_STARTED,
            ts__gte=window.since,
            ts__lt=window.until,
            child_id__isnull=False,
        )
        .annotate(day=TruncDate("ts"))
        .values_list("day", "child_id")
        .distinct()
    )
    per_day: dict[datetime.date, set[Any]] = {}
    for played_on, child_key in rows:
        per_day.setdefault(played_on, set()).add(child_key)

    # At least one, so a sub-day window cannot divide by zero.
    days_in_window = max((window.until - window.since).days, 1)
    avg_dau = sum(len(children) for children in per_day.values()) / days_in_window

    wau = _distinct_active(until - WAU_DAYS * day, until)
    return {
        "dau": _distinct_active(until - day, until),
        "avg_dau": round(avg_dau, 2),
        "wau": wau,
        "mau": _distinct_active(until - MAU_DAYS * day, until),
        "days_in_window": days_in_window,
        "days_with_play": len(per_day),
        "stickiness": round(avg_dau / wau, 3) if wau else None,
        "wau_days": WAU_DAYS,
        "mau_days": MAU_DAYS,
    }


#: How many weeks a cohort is followed for after its first story. Four, because
#: the question this table exists to answer is "does the habit survive the first
#: month", and a fifth column would be four weeks of waiting for one more cell.
COHORT_WEEKS = 4


def retention_cohorts(
    window: Window, *, today: datetime.date | None = None
) -> list[dict[str, Any]]:
    """W1-W4 return rates by weekly cohort — the curve behind `w1_retention`.

    `w1_retention` is one number for a whole window, which cannot answer the
    question everyone actually asks of a young product: *is it getting better?* A
    cohort table can, because each row is a group of children who started in the
    same week and is never mixed with any other.

    *A cohort* is the children whose **first completed story** fell in a given ISO
    week — the same anchor `w1_retention` and `day2_return` use, so the three
    cannot disagree about who is in what.

    *A cell* is the share of that cohort who **started** an adventure during the
    week `offset` weeks later. Started, not completed, for `_play_dates`' reason.

    **An unfinished week reads `None`, never 0%.** A cohort three weeks old has no
    W4 cell yet, and printing that as zero would draw a retention cliff that is
    only a calendar. This is `_matured`'s rule applied per cell rather than per
    row, which is what makes the triangle shape of the table honest.
    """
    today = today or _localdate(timezone.now())
    first = _first_stories()
    played = _play_dates()

    cohorts: dict[datetime.date, list[Any]] = {}
    for child_key, (moment, day) in first.items():
        if not window.contains(moment):
            continue
        monday = day - datetime.timedelta(days=day.weekday())
        cohorts.setdefault(monday, []).append(child_key)

    table: list[dict[str, Any]] = []
    for monday in sorted(cohorts):
        members = cohorts[monday]
        cells: list[dict[str, Any]] = []

        for offset in range(1, COHORT_WEEKS + 1):
            start = monday + datetime.timedelta(days=7 * offset)
            # The week is judgeable only once all seven of its days are behind us.
            if start + datetime.timedelta(days=7) > today:
                cells.append({"offset": offset, "returned": None, "pct": None})
                continue

            returned = sum(
                1
                for child_key in members
                if any(
                    start + datetime.timedelta(days=within) in played.get(child_key, set())
                    for within in range(7)
                )
            )
            cells.append(
                {
                    "offset": offset,
                    "returned": returned,
                    "pct": round(100 * returned / len(members), 1) if members else None,
                }
            )

        table.append({"week_start": monday, "size": len(members), "cells": cells})

    return table


def world_popularity(window: Window, *, limit: int = 12) -> list[dict[str, Any]]:
    """Which worlds children choose, and which ones they finish (§4, §22.3).

    The one "what are our users like" number that is free: `adventure.started`
    already carries `world`, so this is a group-by and no new instrumentation.

    Two columns, and the second is the interesting one. *Started* is what the
    catalog and the personalizer are steering children towards; *completion rate*
    is whether the world held them once they got there. A world that is picked
    often and finished rarely is a content problem wearing a popularity number.

    `children` is distinct children rather than adventures, because one child
    playing the same world nine times is a fact about that child and not about the
    world.

    **Adventures with no world recorded get their own row rather than being
    dropped**, so the column sums to the same total `completion_rate` divides. A
    completion carries the world off its stored plan and a start carries it off
    the personalizer, so a plan that lost the key lands in that row and reads as a
    start with no completion — visible, rather than quietly deflating one world.
    """
    started_rows = Event.objects.filter(
        name=Event.ADVENTURE_STARTED,
        ts__gte=window.since,
        ts__lt=window.until,
    ).values_list("props__world", "child_id")

    started: dict[str, int] = {}
    children: dict[str, set[Any]] = {}
    for world, child_key in started_rows:
        key = str(world or "")
        started[key] = started.get(key, 0) + 1
        if child_key is not None:
            children.setdefault(key, set()).add(child_key)

    completed: dict[str, int] = {}
    completed_rows = Event.objects.filter(
        name=Event.ADVENTURE_COMPLETED,
        ts__gte=window.since,
        ts__lt=window.until,
    ).values_list("props__world", flat=True)
    for world in completed_rows:
        key = str(world or "")
        completed[key] = completed.get(key, 0) + 1

    rows = [
        {
            "world": world,
            "started": count,
            "completed": completed.get(world, 0),
            "children": len(children.get(world, set())),
            "completion": Rate(completed.get(world, 0), count),
        }
        for world, count in started.items()
    ]
    # Most-played first, then by name so two equally-played worlds do not swap
    # places between refreshes.
    rows.sort(key=lambda row: (-row["started"], row["world"]))
    return rows[:limit]


# ---------------------------------------------------------------------------
# Wellbeing guardrails (§22.5) — watched, never maximised
# ---------------------------------------------------------------------------


#: The session length §22.5 asks us to flag. Not a target and not a cap — the cap
#: is the parent's own `session_cap_min` (§13.6) and it is enforced elsewhere.
#: This is the number that says "a child sat for over half an hour", which is the
#: shape of thing §22.5 wants a human to *notice*, whether it came from a
#: generous parental limit or from the cap failing to fire.
OVER_LONG_SESSION_MIN = 30


def _session_length_distribution(ended: Any) -> dict[str, Any]:
    """§22.5's first guardrail: the session-length *distribution*.

    A distribution rather than a mean, and the reason is the same one that makes
    `max_sessions_per_child_day` a maximum: the pattern §22.5 wants flagged
    belongs to one child on one evening, and an average over a healthy cohort is
    precisely the statistic that hides it. So this reports the median, the p90 and
    a count over `OVER_LONG_SESSION_MIN` — the last of which is the one that gets
    read, because it is a number of *children's evenings* and not a summary.

    Read off `session.ended`'s own `duration_s`, so this stays inside the module's
    one rule: every §22 number comes from `Event` and from nothing else. Sessions
    with no duration recorded (a sitting closed by a path that never stamped one)
    are excluded rather than counted as zero — a missing measurement is not a
    short session, and averaging it in as one would make the guardrail read safer
    the more often the stamp failed.
    """
    durations = sorted(
        int(value)
        for value in ended.values_list("props__duration_s", flat=True)
        if isinstance(value, int | float) and value >= 0
    )
    total = len(durations)
    if not total:
        # Same keys as the populated case. A dict whose shape depends on whether
        # there was data is a `KeyError` waiting for a quiet week.
        return {
            "measured": 0,
            "median_min": None,
            "p90_min": None,
            "over_long": 0,
            "over_long_threshold_min": OVER_LONG_SESSION_MIN,
        }

    def minutes(index: int) -> float:
        return round(durations[min(index, total - 1)] / 60, 1)

    return {
        # Reported next to the numbers it produced: a distribution over three
        # sittings and one over three hundred read identically without it.
        "measured": total,
        "median_min": minutes(total // 2),
        "p90_min": minutes(int(total * 0.9)),
        "over_long": sum(1 for value in durations if value >= OVER_LONG_SESSION_MIN * 60),
        "over_long_threshold_min": OVER_LONG_SESSION_MIN,
    }


def guardrails(window: Window) -> dict[str, Any]:
    """§22.5's three: how long and how often children sat, and safety incidents.

    Guardrails are *watched and capped*, never maximised, and they sit next to the
    growth numbers on the same page for that reason: §22.5 gives them a veto, and
    a veto exercised on a different screen is not a veto.

    `sessions_per_child_day` is the compulsive-pattern flag. It is a maximum and
    not a mean, because the pattern §22.5 wants flagged belongs to one child, and
    a mean over a healthy cohort hides it.
    """
    ended = Event.objects.filter(
        name=Event.SESSION_ENDED, ts__gte=window.since, ts__lt=window.until
    )
    reasons = dict(ended.values_list("props__ended_reason").annotate(n=Count("id")))

    per_child_day: dict[tuple[Any, datetime.date], int] = {}
    for child_key, ts in ended.values_list("child_id", "ts"):
        if child_key is None:
            continue
        key = (child_key, _localdate(ts))
        per_child_day[key] = per_child_day.get(key, 0) + 1

    incidents = Event.objects.filter(
        name=Event.SAFETY_INCIDENT, ts__gte=window.since, ts__lt=window.until
    )

    return {
        "sessions": ended.count(),
        # `cap` here is the wellbeing control working, not a failure: §13.6's warm
        # close landed a story at the parent's limit.
        "ended_reasons": {str(reason): count for reason, count in reasons.items()},
        "session_minutes": _session_length_distribution(ended),
        "max_sessions_per_child_day": max(per_child_day.values(), default=0),
        "safety_incidents": incidents.count(),
        # Must trend to ~0 (§22.5). Any serious one is a stop-the-line event, so it
        # is counted separately rather than folded into the total.
        "serious_safety_incidents": incidents.filter(
            props__severity__in=["high", "critical"]
        ).count(),
    }


# ---------------------------------------------------------------------------
# The whole page, in one call
# ---------------------------------------------------------------------------


def snapshot(window: Window, *, today: datetime.date | None = None) -> dict[str, Any]:
    """Every §22.3 number for one window — what the internal dashboard renders.

    Assembled here rather than in the view so the same numbers are available to
    `manage.py metrics` without a second implementation, and so a reviewer has one
    place to check that the dashboard is showing what this module computes.
    """
    return {
        "window": window,
        "activation": activation(window),
        "day2_return": day2_return(window, today=today),
        "w1_retention": w1_retention(window, today=today),
        "completion_rate": completion_rate(window),
        "weeks": weekly_adventures(window),
        "funnel": funnel(window),
        "active": active_children(window),
        "cohorts": retention_cohorts(window, today=today),
        "worlds": world_popularity(window),
        "guardrails": guardrails(window),
        "events": event_counts(window),
    }


def event_counts(window: Window) -> list[tuple[str, int]]:
    """Every event name and how many landed — the pipeline's own health check.

    On the dashboard because it is the fastest way to answer "is a service still
    emitting". A metric that has quietly fallen to zero and a service that has
    quietly stopped emitting look identical in every number above and different
    here.
    """
    rows = (
        Event.objects.filter(ts__gte=window.since, ts__lt=window.until)
        .values_list("name")
        .annotate(n=Count("id"))
        .order_by("name")
    )
    return [(name, count) for name, count in rows]


def names_emitted() -> Iterable[str]:
    """Every event name that has ever been written. Used by the boundary test and
    by the dashboard's "not seen yet" list."""
    return Event.objects.values_list("name", flat=True).distinct()
