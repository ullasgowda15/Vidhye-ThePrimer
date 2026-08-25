"""The behavioural detail behind §22's headline numbers (PRODUCT.md §5.6, §22).

`metrics.py` answers *how is the product doing* — the five §22.3 numbers and the
four diagnostics that explain them. This module answers the next question, the one
you ask once those numbers stop being a surprise: **what are families actually
doing, and when.**

Ten readings, each aimed at a decision:

| Reading | The question | What it changes |
|---|---|---|
| `clock` | When do they play? When do they sign up? | When to ship, when to email |
| `daily` | What did each day look like? | Whether a number is a trend or one good Saturday |
| `accounts` | Who signed up, when, and what happened next? | Who to call |
| `activation_speed` | How long from signup to first story? | Where onboarding drags |
| `attention` | Where does the time go? | Which world earns its generation budget |
| `storyflow` | How far into a story do they get? | Which beat loses them |
| `interaction` | How do children answer? | Whether the choices are readable to a four-year-old |
| `audience` | Who are these children? | Who we are actually building for |
| `content_health` | What is the machine serving? | Whether quality is holding as volume grows |
| `reading` | Who read a line aloud, and which lines? | Whether Phase 1 is producing a corpus |

**Everything here reads `Event`, for the reason `metrics` does** (§5.6): one
pipeline, one source, so two sections of one dashboard cannot disagree. There are
two deliberate exceptions, each argued on its own function: `accounts` follows
`Event.parent` to read an account's email, and `reading` reads
`reading.ReadingAttempt` because a reading attempt emits no event at all.

**On the privacy line.** §13.8 is data minimisation on *child* data, and this
module holds that line exactly where the rest of the app does: no child is named
here, no child is a row here, and nothing here carries a word a child said or a
word said to them. Every child-derived figure is an aggregate across families.
What `accounts` does show is the *account holder* — the adult who signed up, which
is the customer record, not the child's behavioural record. That distinction is
the whole of §5.6's boundary and it is worth keeping sharp: knowing that
`priya@example.com` signed up on Tuesday at 21:40 and has not come back is running
a business; knowing what her daughter chose at beat four is not, and is not here.

**This module is the product/ops half of §5.6 and must never be imported by a
parent-facing view**, asserted over the import graph by
`tests/test_analytics_pipeline.py::TestTheSeparationIsStructural`.

*Scale note:* like `metrics`, several readings pull a window of events into Python
and fold them there. That is deliberate at alpha scale — the folds are legible and
the equivalent SQL is not — and it is the first thing to rewrite when a window
stops fitting comfortably in memory. Every query is bounded by the window and by
`values_list`, so none of them drags a `props` blob further than it has to go.
"""

from __future__ import annotations

import datetime
import zoneinfo
from typing import TYPE_CHECKING, Any

from django.db import models

from apps.analytics.metrics import Rate, Window
from apps.analytics.models import Event
from apps.reading.models import ReadingAttempt

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# ---------------------------------------------------------------------------
# Time, in the timezone the families are actually in
# ---------------------------------------------------------------------------

#: The server stores and serves UTC (`TIME_ZONE = "UTC"`, `settings/base.py`), and
#: for every metric in `metrics.py` that is the right call: a calendar day has to
#: be one fixed thing or two windows tile wrong.
#:
#: **It is the wrong call for a clock.** "When do children play" is the one
#: question on this dashboard whose answer is *destroyed* by the wrong zone — a
#: bedtime product used in India peaks at 16:00 UTC, which reads as an
#: after-lunch product and is a completely false picture of the same families.
#:
#: **So there is one zone, and it is the families'.** This offered a picker of
#: seven and defaulted to UTC, which meant the honest thing on the page — every
#: hour reading — was wrong by five and a half hours until somebody remembered to
#: change a dropdown, and was quoted from screenshots that did not record which
#: way it had been set. A default nobody should ever accept is a bug with a
#: workaround, not a feature.
#:
#: The zone changes *only* which local hour and calendar day a moment is filed
#: under. Every rate, funnel, cohort and retention figure is computed on the
#: server's own UTC days and cannot move because of anything here — that split is
#: what makes those numbers quotable, and it is unchanged.
ZONES: tuple[str, ...] = ("Asia/Kolkata",)

DEFAULT_ZONE = "Asia/Kolkata"

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

#: The four parts of a day, in the words a parent would use. Named because "the
#: evening share" is a sentence somebody will say out loud about this product, and
#: it should mean one arithmetic thing when they do.
DAYPARTS: tuple[tuple[str, int, int], ...] = (
    ("Night", 0, 6),
    ("Morning", 6, 12),
    ("Afternoon", 12, 17),
    ("Evening", 17, 24),
)


def resolve_zone(name: str | None) -> zoneinfo.ZoneInfo:
    """A zone from `?tz=`, refused unless it is one we offer.

    Clamped to `ZONES` rather than passed to `ZoneInfo` directly, for the reason
    `internal.days_from` clamps its window: an arbitrary string from a query
    parameter reaching a system tzdata lookup is a needless edge, and the list is
    the set of answers anybody actually wants.
    """
    if name in ZONES:
        return zoneinfo.ZoneInfo(name)
    return zoneinfo.ZoneInfo(DEFAULT_ZONE)


def _local(moment: datetime.datetime, zone: zoneinfo.ZoneInfo) -> datetime.datetime:
    return moment.astimezone(zone)


def _events(window: Window, *names: str) -> Any:
    """Every event of these names inside the window."""
    return Event.objects.filter(name__in=names, ts__gte=window.since, ts__lt=window.until)


def _number(value: Any) -> float | None:
    """A `props` value as a number, or `None` if it is not one.

    `props` is JSON written by eleven call sites, and a reading that assumed a
    field was an int would fail on the day one of them passes a string — loudly on
    a dashboard, which is the worst place. Every numeric fold goes through here.
    `bool` is excluded on purpose: `True` is an `int` in Python, and a boolean flag
    averaged into a latency would be silent nonsense.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _percentile(sorted_values: Sequence[float], fraction: float) -> float | None:
    """The value at a fraction through an already-sorted list.

    Nearest-rank, not interpolated. These are latencies and durations measured in
    whole milliseconds and seconds; an interpolated p90 invents a measurement that
    was never taken, and at alpha sample sizes the difference is noise dressed as
    precision.
    """
    if not sorted_values:
        return None
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    """Median, p90, max and the count they came from.

    The count is not optional. A p90 over seven measurements is a fact about seven
    measurements, and a dashboard that prints the percentile without the `n`
    invites it to be quoted as though it were a fact about the product.
    """
    ordered = sorted(values)
    return {
        "measured": len(ordered),
        "median": _percentile(ordered, 0.5),
        "p90": _percentile(ordered, 0.9),
        "max": ordered[-1] if ordered else None,
    }


def _histogram(values: Iterable[float], edges: Sequence[float], unit: str) -> list[dict[str, Any]]:
    """A distribution as labelled buckets, with the share in each.

    Explicit edges rather than equal-width bins, because every distribution in this
    module is long-tailed — one child sat for two hours — and equal-width bins over
    a range set by an outlier produce twenty empty buckets and one full one, which
    is a picture of the outlier and not of the product.

    The last bucket is open-ended (`30+`), so the outlier lands somewhere true
    instead of stretching the axis.
    """
    ordered = sorted(values)
    total = len(ordered)
    buckets: list[dict[str, Any]] = []
    for index, low in enumerate(edges):
        high = edges[index + 1] if index + 1 < len(edges) else None
        count = sum(1 for value in ordered if value >= low and (high is None or value < high))
        if high is None:
            label = f"{_trim(low)}{unit}+"
        elif index == 0 and low == 0:
            label = f"<{_trim(high)}{unit}"
        else:
            label = f"{_trim(low)}{EN_DASH}{_trim(high)}{unit}"
        buckets.append({"label": label, "count": count, "share": Rate(count, total)})
    return buckets


#: The range separator in a bucket label ("5-10min"). A plain hyphen: the source
#: of this project is held to unambiguous ASCII, and a typographic en dash in a
#: label is not worth an exception to that.
EN_DASH = "-"


def _trim(value: float) -> str:
    """`5` not `5.0`, so a bucket label reads like a number a person wrote."""
    return str(int(value)) if float(value).is_integer() else str(value)


#: How many shades the heatmap's ramp has. Five, because past about seven the
#: adjacent classes of a sequential ramp stop being tellable apart and the grid
#: becomes decoration — and because a cell has to carry its own count in a tooltip
#: anyway, the shade only has to say "more than that one".
LEVELS = 5


def _level(count: int, peak: int) -> int:
    """A count as a shade index, `0` for nothing at all.

    Zero is its own level and is drawn as bare surface, never as the palest shade.
    An hour when nobody played and an hour when one child played are different
    facts about a bedtime product, and a ramp that starts at "pale" makes the empty
    grid look like a quiet one.
    """
    if count <= 0 or peak <= 0:
        return 0
    return min(LEVELS, -(-count * LEVELS // peak))


def _level_floor(level: int, peak: int) -> int:
    """The smallest count that reaches a shade — the heatmap legend's tick."""
    if peak <= 0:
        return 0
    return max(1, -(-(level - 1) * peak // LEVELS) + 1)


def _share_rows(
    counter: dict[str, int], *, limit: int | None = None, blank: str = "(not recorded)"
) -> list[dict[str, Any]]:
    """A counter as sorted rows with a share of the total.

    Sorted by count then by label, so two equal categories do not swap places
    between refreshes — a table that reorders itself on reload is one nobody trusts
    to have been read correctly the first time.
    """
    total = sum(counter.values())
    rows: list[dict[str, Any]] = [
        {"label": key or blank, "count": count, "share": Rate(count, total)}
        for key, count in counter.items()
    ]
    rows.sort(key=lambda row: (-int(row["count"]), str(row["label"])))
    return rows[:limit] if limit else rows


# ---------------------------------------------------------------------------
# 1 · The clock — when families are actually here
# ---------------------------------------------------------------------------
def clock(window: Window, *, zone: zoneinfo.ZoneInfo) -> dict[str, Any]:
    """When children play and when parents sign up, by local hour and weekday.

    **Play is `adventure.started`, not `session.ended`.** The question is when a
    family *reaches for* the product; a sitting that ended at 20:40 because the cap
    fired says more about the cap than about the household. Starting is the intent.

    Three shapes of the same fact, because they answer three different questions:

    * `hours` — the day's profile. This is the one that says "bedtime product" or
      "weekend product" out loud, and it is what a release window and a push
      notification schedule are chosen against.
    * `weekdays` — whether this is a school-night habit or a Saturday treat. The
      difference decides whether a weekly retention number is a real habit or an
      artefact of two good days.
    * `grid` — the two crossed, 7x24. A product used at 19:00 on weekdays and 10:00
      at weekends has an identical `hours` profile to one used at both times every
      day, and only the grid tells them apart.

    Signups are folded in on the same clock because they are a different behaviour
    by a different person: the parent researches at lunchtime and the child plays
    at bedtime is a real and common pattern, and seeing the two curves apart is how
    you notice you are running one product for two audiences.
    """
    plays = [
        _local(ts, zone)
        for ts in _events(window, Event.ADVENTURE_STARTED).values_list("ts", flat=True)
    ]
    signups = [
        _local(ts, zone)
        for ts in _events(window, Event.PARENT_SIGNED_UP).values_list("ts", flat=True)
    ]

    play_hours = [0] * 24
    signup_hours = [0] * 24
    play_weekdays = [0] * 7
    signup_weekdays = [0] * 7
    grid = [[0] * 24 for _ in range(7)]

    for moment in plays:
        play_hours[moment.hour] += 1
        play_weekdays[moment.weekday()] += 1
        grid[moment.weekday()][moment.hour] += 1
    for moment in signups:
        signup_hours[moment.hour] += 1
        signup_weekdays[moment.weekday()] += 1

    busiest = max(range(24), key=lambda hour: play_hours[hour]) if plays else None
    busiest_day = max(range(7), key=lambda day: play_weekdays[day]) if plays else None
    grid_peak = max((max(row) for row in grid), default=0)

    return {
        "zone": str(zone),
        "zones": ZONES,
        "plays": len(plays),
        "signups": len(signups),
        "hours": [
            {
                "hour": hour,
                "label": f"{hour:02d}",
                "plays": play_hours[hour],
                "signups": signup_hours[hour],
            }
            for hour in range(24)
        ],
        "peak_plays": max(play_hours) if plays else 0,
        "peak_signups": max(signup_hours) if signups else 0,
        "weekdays": [
            {
                "day": WEEKDAY_NAMES[day],
                "weekend": day >= 5,
                "plays": play_weekdays[day],
                "signups": signup_weekdays[day],
            }
            for day in range(7)
        ],
        "peak_weekday_plays": max(play_weekdays) if plays else 0,
        "grid": [
            {
                "day": WEEKDAY_NAMES[day],
                "weekend": day >= 5,
                "cells": [
                    {"hour": hour, "count": count, "level": _level(count, grid_peak)}
                    for hour, count in enumerate(grid[day])
                ],
            }
            for day in range(7)
        ],
        "grid_max": grid_peak,
        "grid_legend": [
            {"level": level, "from": _level_floor(level, grid_peak)}
            for level in range(1, LEVELS + 1)
        ],
        "busiest_hour": f"{busiest:02d}:00" if busiest is not None else None,
        "busiest_weekday": WEEKDAY_NAMES[busiest_day] if busiest_day is not None else None,
        "dayparts": [
            {
                "label": label,
                "window": f"{low:02d}-{high:02d}",
                "plays": sum(play_hours[low:high]),
                "share": Rate(sum(play_hours[low:high]), len(plays)),
            }
            for label, low, high in DAYPARTS
        ],
        "weekend_share": Rate(sum(play_weekdays[5:]), len(plays)),
    }


# ---------------------------------------------------------------------------
# 2 · The daily series — the shape of the window
# ---------------------------------------------------------------------------
def daily(window: Window, *, zone: zoneinfo.ZoneInfo) -> dict[str, Any]:
    """One row per calendar day in the window, with a day of zero written as zero.

    **Every day in the window appears, including the empty ones.** A series built
    by grouping events would skip the quiet days entirely, and a chart drawn from
    it would join Friday to Monday with a straight line and hide the weekend — the
    single most misleading thing a time series can do to a product used at
    weekends.

    "Children" is distinct children who started something, so a series of days can
    be read against the DAU figure above it rather than as a different quantity
    with a similar name. Minutes come off `session.ended`'s own `duration_s`, which
    is why a day can show adventures and no minutes: sittings are stamped when they
    close, and a story still running at midnight belongs to the day it ends on.
    """
    start = _local(window.since, zone).date()
    finish = _local(window.until, zone).date()
    days: list[datetime.date] = []
    cursor = start
    while cursor <= finish:
        days.append(cursor)
        cursor += datetime.timedelta(days=1)

    signups: dict[datetime.date, int] = {}
    started: dict[datetime.date, int] = {}
    completed: dict[datetime.date, int] = {}
    sittings: dict[datetime.date, int] = {}
    seconds: dict[datetime.date, float] = {}
    children: dict[datetime.date, set[Any]] = {}

    rows = _events(
        window,
        Event.PARENT_SIGNED_UP,
        Event.ADVENTURE_STARTED,
        Event.ADVENTURE_COMPLETED,
        Event.SESSION_ENDED,
    ).values_list("name", "ts", "child_id", "props")

    for name, ts, child_key, props in rows:
        day = _local(ts, zone).date()
        if name == Event.PARENT_SIGNED_UP:
            signups[day] = signups.get(day, 0) + 1
        elif name == Event.ADVENTURE_STARTED:
            started[day] = started.get(day, 0) + 1
            if child_key is not None:
                children.setdefault(day, set()).add(child_key)
        elif name == Event.ADVENTURE_COMPLETED:
            completed[day] = completed.get(day, 0) + 1
        elif name == Event.SESSION_ENDED:
            sittings[day] = sittings.get(day, 0) + 1
            duration = _number((props or {}).get("duration_s"))
            if duration is not None and duration >= 0:
                seconds[day] = seconds.get(day, 0.0) + duration

    # **Both ends of the window are partial, not just today.** The window is
    # rolling — `now - N days` to `now` — so the first local day is only covered
    # from the hour the window opens, exactly as the last is only covered up to
    # now. The page warned about one and not the other, which made the opening bar
    # of every chart read as a quiet day that never happened. Flagged rather than
    # dropped: the events in it are real and belong in the totals.
    series = [
        {
            "date": day,
            "weekend": day.weekday() >= 5,
            "partial": day in (start, finish),
            "signups": signups.get(day, 0),
            "children": len(children.get(day, set())),
            "started": started.get(day, 0),
            "completed": completed.get(day, 0),
            "sittings": sittings.get(day, 0),
            "minutes": round(seconds.get(day, 0.0) / 60),
        }
        for day in days
    ]
    return {
        "series": series,
        "zone": str(zone),
        # The scales the charts are drawn against, computed here rather than in the
        # template: Django's template language cannot take a maximum, and a chart
        # that rescales per bar is not a chart.
        "max_children": max((row["children"] for row in series), default=0),
        "max_started": max((row["started"] for row in series), default=0),
        "max_signups": max((row["signups"] for row in series), default=0),
        "max_minutes": max((row["minutes"] for row in series), default=0),
        "total_signups": sum(row["signups"] for row in series),
        "total_started": sum(row["started"] for row in series),
        "total_minutes": sum(row["minutes"] for row in series),
    }


# ---------------------------------------------------------------------------
# 3 · The accounts — who signed up, and what happened next
# ---------------------------------------------------------------------------

#: How many accounts the roster shows. A cap rather than a page, because this is a
#: dashboard and not a CRM: past a couple of hundred rows the question stops being
#: "who signed up" and starts being "export it", which is `manage.py drain_events`.
ROSTER_LIMIT = 250


def accounts(
    window: Window, *, zone: zoneinfo.ZoneInfo, limit: int = ROSTER_LIMIT
) -> dict[str, Any]:
    """Every account that signed up in this window, and how far each one got.

    **This is the one reading on the page that names anybody**, and it names the
    *account holder* — an adult who gave us their email to buy a product. That is a
    customer record and reading it is running the business: "who signed up on
    Tuesday and never came back" is a list of people to call, and it cannot be
    answered by an aggregate. Everything about the *child* stays counted, never
    named and never itemised — a family's row says three children and eleven
    adventures, and nothing about which worlds, which choices, or which evening
    (§13.8, §5.6).

    The spine is `Event`, like everything else here; the email is read across
    `Event.parent`, which is the one place this module leaves the event log. That
    is a deliberate, single, documented crossing rather than a habit: it is why
    `PARENT_SIGNED_UP` is queried with `select_related` and why no other reading in
    this file touches a `User`.

    **`stage` is where the family stopped, not what they failed.** A family that
    signed up an hour ago is `signed up`, and that is a true and neutral statement
    about an hour-old account; the page prints the account's age beside it, so a
    fresh row is never read as a stalled one.
    """
    signups = list(
        _events(window, Event.PARENT_SIGNED_UP)
        .select_related("parent")
        .order_by("-ts")[:limit]
        .values_list("parent_id", "ts", "parent__email", "parent__is_active")
    )
    parent_keys = [key for key, _, _, _ in signups if key is not None]
    if not parent_keys:
        return {"rows": [], "shown": 0, "total": 0, "stages": [], "zone": str(zone)}

    total = _events(window, Event.PARENT_SIGNED_UP).count()

    # One pass over every later event belonging to these accounts. Not restricted
    # to the window: the question this table answers is "what happened *next*", and
    # a family who signed up on the last day of the window and played on the first
    # day after it is precisely the row worth seeing.
    follow = Event.objects.filter(parent_id__in=parent_keys, ts__gte=window.since).values_list(
        "parent_id", "name", "ts", "child_id", "props"
    )

    consent: dict[Any, datetime.datetime] = {}
    children: dict[Any, set[Any]] = {}
    companions: dict[Any, int] = {}
    onboarded: dict[Any, datetime.datetime] = {}
    completions: dict[Any, int] = {}
    starts: dict[Any, int] = {}
    sittings: dict[Any, int] = {}
    seconds: dict[Any, float] = {}
    last_seen: dict[Any, datetime.datetime] = {}
    incidents: dict[Any, int] = {}

    for parent_key, name, ts, child_key, props in follow:
        if name != Event.PARENT_SIGNED_UP:
            # The last sign of life, whatever it was. Signup itself excluded, so a
            # dormant account reads as dormant rather than as active on the day it
            # was created.
            if ts > last_seen.get(parent_key, ts - datetime.timedelta(seconds=1)):
                last_seen[parent_key] = ts
        if name == Event.PARENT_CONSENT_RECORDED:
            consent.setdefault(parent_key, ts)
        elif name == Event.CHILD_CREATED and child_key is not None:
            children.setdefault(parent_key, set()).add(child_key)
        elif name == Event.COMPANION_CREATED:
            companions[parent_key] = companions.get(parent_key, 0) + 1
        elif name == Event.ONBOARDING_COMPLETED:
            onboarded.setdefault(parent_key, ts)
        elif name == Event.ADVENTURE_STARTED:
            starts[parent_key] = starts.get(parent_key, 0) + 1
        elif name == Event.ADVENTURE_COMPLETED:
            completions[parent_key] = completions.get(parent_key, 0) + 1
        elif name == Event.SESSION_ENDED:
            sittings[parent_key] = sittings.get(parent_key, 0) + 1
            duration = _number((props or {}).get("duration_s"))
            if duration is not None and duration >= 0:
                seconds[parent_key] = seconds.get(parent_key, 0.0) + duration
        elif name == Event.SAFETY_INCIDENT:
            incidents[parent_key] = incidents.get(parent_key, 0) + 1

    now = window.until
    rows: list[dict[str, Any]] = []
    stage_counts: dict[str, int] = {}

    for parent_key, ts, email, is_active in signups:
        if parent_key in completions:
            stage = "playing"
        elif parent_key in starts:
            stage = "first story"
        elif parent_key in onboarded or parent_key in companions:
            stage = "companion made"
        elif parent_key in children:
            stage = "child added"
        elif parent_key in consent:
            stage = "consented"
        else:
            stage = "signed up"
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

        seen = last_seen.get(parent_key)
        rows.append(
            {
                "email": email or "(account removed)",
                "active": bool(is_active),
                "signed_up_at": _local(ts, zone),
                "age_h": round((now - ts).total_seconds() / 3600, 1),
                "stage": stage,
                "children": len(children.get(parent_key, set())),
                "started": starts.get(parent_key, 0),
                "completed": completions.get(parent_key, 0),
                "sittings": sittings.get(parent_key, 0),
                "minutes": round(seconds.get(parent_key, 0.0) / 60),
                "incidents": incidents.get(parent_key, 0),
                "last_seen_at": _local(seen, zone) if seen else None,
                # Hours of silence, which is the column you read to find the family
                # about to churn. `None` for an account that has done nothing at
                # all — a dash, never a zero.
                "quiet_h": round((now - seen).total_seconds() / 3600, 1) if seen else None,
            }
        )

    return {
        "rows": rows,
        "shown": len(rows),
        "total": total,
        "zone": str(zone),
        "stages": _share_rows(stage_counts),
    }


# ---------------------------------------------------------------------------
# 4 · Activation speed — how long the first story takes to arrive
# ---------------------------------------------------------------------------

#: Hours from signup to first completed story, bucketed. The first edge is a
#: quarter of an hour because §4.2's onboarding is designed to be walked in one
#: sitting, and "did they get there in the same sitting" is the question the whole
#: flow was built to make answerable.
ACTIVATION_EDGES = (0.0, 0.25, 1.0, 6.0, 24.0, 72.0, 168.0)


def activation_speed(window: Window) -> dict[str, Any]:
    """Signup → first completed story, as a distribution rather than an average.

    A mean here is meaningless and would be quoted anyway: the distribution is
    bimodal by construction — families who finish onboarding in one sitting, and
    families who come back days later — and the mean of those two groups describes
    neither of them.

    **Only accounts that got there are measured.** A family who signed up yesterday
    and has not played is not a slow activation, they are an unfinished one, and
    folding them in as a large number would make this reading worse every time a
    good week of signups arrived. How many are in that state is reported next to it
    as `waiting`, which is the honest denominator.

    Both halves matter and they are different columns: `to_first_start` is how long
    until a child was *put in front of* a story (that is onboarding's job), and
    `to_first_completion` is how long until one finished (that is the story's). A
    gap between the two says the setup is fine and the first story is not.
    """
    signed_up: dict[Any, datetime.datetime] = {}
    for parent_key, ts in _events(window, Event.PARENT_SIGNED_UP).values_list("parent_id", "ts"):
        if parent_key is not None:
            signed_up.setdefault(parent_key, ts)

    if not signed_up:
        return {
            "to_first_start": _distribution([]),
            "to_first_completion": _distribution([]),
            "buckets": _histogram([], ACTIVATION_EDGES, "h"),
            "activated": 0,
            "waiting": 0,
            "signups": 0,
        }

    first_start: dict[Any, datetime.datetime] = {}
    first_done: dict[Any, datetime.datetime] = {}
    for parent_key, name, ts in Event.objects.filter(
        parent_id__in=list(signed_up), ts__gte=window.since
    ).values_list("parent_id", "name", "ts"):
        if name == Event.ADVENTURE_STARTED:
            if ts < first_start.get(parent_key, ts + datetime.timedelta(seconds=1)):
                first_start[parent_key] = ts
        elif name == Event.ADVENTURE_COMPLETED:
            if ts < first_done.get(parent_key, ts + datetime.timedelta(seconds=1)):
                first_done[parent_key] = ts

    def hours(reached: dict[Any, datetime.datetime]) -> list[float]:
        return [
            (moment - signed_up[key]).total_seconds() / 3600
            for key, moment in reached.items()
            if key in signed_up and moment >= signed_up[key]
        ]

    completion_hours = hours(first_done)
    return {
        "to_first_start": _distribution(hours(first_start)),
        "to_first_completion": _distribution(completion_hours),
        "buckets": _histogram(completion_hours, ACTIVATION_EDGES, "h"),
        "activated": len(completion_hours),
        "waiting": len(signed_up) - len(completion_hours),
        "signups": len(signed_up),
    }


# ---------------------------------------------------------------------------
# 5 · Attention — where the time actually goes
# ---------------------------------------------------------------------------

#: Sitting-length buckets, in minutes. The edges are the ones a parent would
#: recognise: under five minutes is a glance, 20 to 30 is a long bedtime story, and
#: past 30 is `metrics.OVER_LONG_SESSION_MIN`, the §22.5 threshold, so this
#: histogram and that guardrail cut the day at the same place and cannot disagree.
SITTING_EDGES = (0.0, 5.0, 10.0, 20.0, 30.0)

#: Beats per sitting. `1` is its own bucket because a one-beat sitting is a
#: different event from a short one — it is somebody who opened the app and left,
#: and averaging it into "2-3" hides the only bucket that is unambiguously bad.
BEAT_EDGES = (0.0, 1.0, 2.0, 4.0, 8.0, 12.0)


def attention(window: Window) -> dict[str, Any]:
    """How long sittings run, how much story fits in them, and which worlds hold them.

    The headline the founder asked for — *where do they spend their time* — is the
    `worlds` table, and it is deliberately measured in **minutes** rather than in
    starts. A world that is picked constantly and abandoned in ninety seconds and a
    world that is picked rarely and held for twenty minutes look identical on a
    popularity chart and opposite here, and the second one is the product working.

    `minutes_per_beat` is the pacing number. It is the closest thing available to
    "is this too slow", and it is a median over sittings rather than a global
    ratio, so one very long sitting cannot set it.

    Worlds are joined to sittings through the adventure's own event rather than
    through the session, because a sitting can span two adventures and its minutes
    genuinely belong to both; a sitting with more than one world is attributed to
    the world it *started* in, and how often that happens is reported as
    `multi_world_sittings` so the attribution is never silently assumed to be clean.
    """
    ended = list(_events(window, Event.SESSION_ENDED).values_list("session_key", "props"))
    minutes: list[float] = []
    beats: list[float] = []
    per_beat: list[float] = []
    reasons: dict[str, int] = {}
    completed_sittings = 0

    for _, props in ended:
        payload = props or {}
        duration = _number(payload.get("duration_s"))
        beat_count = _number(payload.get("beats"))
        reason = str(payload.get("ended_reason") or "")
        reasons[reason] = reasons.get(reason, 0) + 1
        if payload.get("completed") is True:
            completed_sittings += 1
        if duration is not None and duration >= 0:
            minutes.append(duration / 60)
            if beat_count and beat_count > 0:
                per_beat.append(duration / 60 / beat_count)
        if beat_count is not None and beat_count >= 0:
            beats.append(beat_count)

    # Which world each sitting began in.
    sitting_world: dict[Any, str] = {}
    multi = 0
    for session_key, world in _events(window, Event.ADVENTURE_STARTED).values_list(
        "session_key", "props__world"
    ):
        if session_key is None:
            continue
        key = str(world or "")
        if session_key in sitting_world:
            if sitting_world[session_key] != key:
                multi += 1
            continue
        sitting_world[session_key] = key

    world_minutes: dict[str, float] = {}
    world_sittings: dict[str, int] = {}
    for session_key, props in ended:
        world = sitting_world.get(session_key)
        if world is None:
            continue
        duration = _number((props or {}).get("duration_s"))
        world_sittings[world] = world_sittings.get(world, 0) + 1
        if duration is not None and duration >= 0:
            world_minutes[world] = world_minutes.get(world, 0.0) + duration / 60

    total_minutes = round(sum(world_minutes.values()))
    worlds: list[dict[str, Any]] = [
        {
            "world": world or "(not recorded)",
            "sittings": count,
            "minutes": round(world_minutes.get(world, 0.0)),
            "avg_min": round(world_minutes.get(world, 0.0) / count, 1) if count else None,
            "share": Rate(round(world_minutes.get(world, 0.0)), total_minutes),
        }
        for world, count in world_sittings.items()
    ]
    worlds.sort(key=lambda row: (-int(row["minutes"]), str(row["world"])))

    return {
        "sittings": len(ended),
        "minutes_total": round(sum(minutes)),
        "sitting_minutes": _distribution(minutes),
        "sitting_buckets": _histogram(minutes, SITTING_EDGES, "min"),
        "beats_per_sitting": _distribution(beats),
        "beat_buckets": _histogram(beats, BEAT_EDGES, " beats"),
        "minutes_per_beat": _distribution(per_beat),
        "ended_reasons": _share_rows(reasons),
        "completed_sittings": Rate(completed_sittings, len(ended)),
        "worlds": worlds[:12],
        "world_minutes_max": max((int(row["minutes"]) for row in worlds), default=0),
        "multi_world_sittings": multi,
    }


# ---------------------------------------------------------------------------
# 6 · Storyflow — how far in they get, and where they stop
# ---------------------------------------------------------------------------

#: How many beats the reach curve is drawn over. Twelve because `estimated_beats`
#: sits in that range for every arc the planner currently produces; anything beyond
#: it is counted as `beyond` rather than stretching the chart.
REACH_BEATS = 12


def storyflow(window: Window) -> dict[str, Any]:
    """The reach curve: of the adventures that saw beat 1, how many saw beat *n*.

    **This is the most actionable chart on the page** and it is the one the old
    dashboard had no way to draw. A completion rate says 62% and leaves you
    nowhere; a reach curve says they leave at beat three, and beat three is a thing
    somebody can go and read.

    Counted over *distinct adventures* reaching each sequence number, not over beats
    delivered, so an adventure that retried a beat is one adventure at that depth
    and not two. The denominator is adventures that reached beat 1 — an adventure
    that never got a first beat is a generation failure, not a drop-off, and it is
    counted separately as `no_first_beat` so the two cannot be confused.

    `kept` is the share of the *previous* step held at this one, which is the column
    that names the beat. Measured against the top, every beat after a bad one looks
    bad too — the same trap `metrics.funnel` documents, and it costs more here
    because the steps are so close together.
    """
    depth: dict[str, int] = {}
    for adventure_key, seq in _events(window, Event.BEAT_DELIVERED).values_list(
        "props__adventure_id", "props__seq"
    ):
        number = _number(seq)
        if adventure_key is None or number is None:
            continue
        key = str(adventure_key)
        depth[key] = max(depth.get(key, 0), int(number))

    started = _events(window, Event.ADVENTURE_STARTED).count()
    reached_one = sum(1 for value in depth.values() if value >= 1)

    rows: list[dict[str, Any]] = []
    previous = reached_one
    for seq in range(1, REACH_BEATS + 1):
        reached = sum(1 for value in depth.values() if value >= seq)
        rows.append(
            {
                "seq": seq,
                "reached": reached,
                "of_first": Rate(reached, reached_one),
                "kept": Rate(reached, previous),
                "lost": max(previous - reached, 0),
            }
        )
        previous = reached

    # The biggest single step-to-step loss, so the page can say it in words instead
    # of asking somebody to scan a column. Beat 1 is excluded as a candidate: its
    # "loss" is measured against itself and is always zero, and including it would
    # make the sentence read "most children stop at beat 1" on an empty window.
    candidates = [row for row in rows if row["seq"] > 1 and row["lost"]]
    return {
        "rows": rows,
        "adventures": len(depth),
        "started": started,
        "reached_one": reached_one,
        # Adventures the log says began but for which no beat was ever delivered.
        # A generation failure, and a number that should be zero.
        "no_first_beat": max(started - reached_one, 0),
        "deepest": max(depth.values(), default=0),
        "beyond": sum(1 for value in depth.values() if value > REACH_BEATS),
        "depth": _distribution([float(value) for value in depth.values()]),
        "worst_step": max(candidates, key=lambda row: row["lost"]) if candidates else None,
    }


# ---------------------------------------------------------------------------
# 7 · Interaction — how children actually answer
# ---------------------------------------------------------------------------

#: Decision-latency buckets, in seconds. Under two seconds is a tap that did not
#: involve reading; over thirty is a child who has left the room. Both edges are
#: the interesting ones — the middle is just "they thought about it".
LATENCY_EDGES = (0.0, 2.0, 5.0, 10.0, 30.0)

#: **Whether the option's on-screen position is recorded. It is, since
#: `option_index` was added to `choice.made`.**
#:
#: The constant stays because the *history* is the useful part. `chosen_key` was
#: described at its emit site for a long time as "a positional key (`a`/`b`/`c`)".
#: It never was — it is a slug of the option's label (`red_leaf`) — and a reading
#: built on that description answered a different question while looking correct.
#: `option_index` is the real position, and this flag is what the page and the
#: terminal report consult rather than assuming.
#:
#: **Nothing before the field landed has a position.** Events written earlier
#: carry no `option_index`, and the reading counts them as `unpositioned` rather
#: than as position 0 — otherwise the day the field shipped would look like a
#: cliff of first-card taps. Read the position bars against `positioned` and not
#: against `choices`.
POSITION_IS_INSTRUMENTED = True

#: How many positions the bias table lists. Six: beats offer two or three options
#: today, and a seventh row would be an empty row on every real beat.
POSITION_ROWS = 6

#: How many distinct options the picked-options table lists before folding the
#: rest into one row. Twelve: enough to see a dominant option, short enough that a
#: long tail of one-offs does not become the table.
OPTION_ROWS = 12


def _position_rows(
    taken: dict[int, int], chance: dict[int, float], positioned: int
) -> list[dict[str, Any]]:
    """One row per on-screen position: taken, expected by chance, and the gap.

    `expected` is the number of times this position *would* have been chosen if
    every child picked at random — the sum of `1 / option_count` over the choices
    that offered it. That is the only fair yardstick when beats offer different
    numbers of options: measured against a flat third, a week heavy in two-option
    beats reads as a first-card bias that is really just arithmetic.

    `lift` is taken ÷ expected. `1.0` is chance; `1.4` on position 0 means the top
    card is taken 40% more often than the words alone would explain, which is the
    number to act on. It is `None` when a position was never offered, because a
    ratio to zero is not a large lift — it is not a lift.

    Rows run to the highest position ever *offered*, not the highest ever taken, so
    a third option that nobody ever picks shows as a row of zeros. That row is the
    finding: an option children never take is worth reading.
    """
    highest = max([*taken, *chance], default=-1)
    rows: list[dict[str, Any]] = []
    for position in range(min(highest + 1, POSITION_ROWS)):
        count = taken.get(position, 0)
        expected = chance.get(position, 0.0)
        rows.append(
            {
                "position": position,
                # "1st" reads better than "0" to anyone who did not write the code,
                # and the raw index is kept beside it for anyone who did.
                "label": _ordinal(position + 1),
                "count": count,
                "share": Rate(count, positioned),
                "expected": round(expected, 1),
                "expected_pct": round(100 * expected / positioned, 1) if positioned else None,
                "lift": round(count / expected, 2) if expected else None,
            }
        )
    return rows


def _ordinal(number: int) -> str:
    """`1st`, `2nd`, `3rd`, `4th`. Only ever called with 1..6, so no teens edge."""
    return f"{number}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(number, 'th') }"


def interaction(window: Window) -> dict[str, Any]:
    """Decision latency, retries, help taken, and where on the screen they tapped.

    **`positions` is the one to read first, and it is a design check rather than a
    metric.** `option_index` is where the chosen option sat, so if position 0 wins
    far more often than chance, the choices are not being read — a four-year-old is
    tapping the top card. That is a layout and copy problem, it is invisible in
    every other number on this dashboard, and it caps what all of them mean: a
    learning signal collected from a reflex is measuring the reflex.

    **Chance is not "one over the number of rows".** Beats offer two or three
    options and the mix moves, so the fair share for position 0 is the average of
    `1 / option_count` over the choices that actually offered a position — computed
    here as `expected`, per position, rather than assumed. Comparing a 3-option
    world against a flat 33% would manufacture a bias on any week the two-option
    beats were common.

    `unpositioned` counts choices written before `option_index` existed, plus the
    real cases with no position at all — an unrecognised key, or a beat offering
    only "carry on". They are excluded from the bias arithmetic rather than
    counted as position 0, which would read as a first-card habit that is really
    just history.

    `top_option_share` is kept alongside as a different signal: one *option* taking
    a very large share — a generic `continue` — says children are advancing rather
    than choosing, which is about the writing and not about the layout.

    `assisted` is how often the companion helped, and `retried` how often the child
    needed another go. Both are split by whether the beat was a Learning Engine ask
    (§10.5), because help on a *learning* beat is the system working as designed
    and help on an ordinary story branch is a child who could not tell what was
    being asked — the same number meaning two opposite things, which is why it is
    never reported as one.

    Latency under two seconds is reported as `instant` rather than being averaged
    in: it is not a fast decision, it is a tap before the words landed, and its
    *share* is the signal.
    """
    rows = list(
        _events(window, Event.CHOICE_MADE).values_list(
            "props__chosen_key",
            "props__latency_ms",
            "props__retries",
            "props__assisted",
            "props__learning_ask",
            "props__option_index",
            "props__option_count",
        )
    )
    seconds: list[float] = []
    options: dict[str, int] = {}
    taken: dict[int, int] = {}
    # The fair share each position would take if children chose at random, summed
    # over the choices that offered one. Accumulated rather than assumed — see the
    # docstring: a flat 1/len(positions) would invent a bias whenever the mix of
    # two- and three-option beats moved.
    chance: dict[int, float] = {}
    positioned = 0
    retried = 0
    retried_learning = 0
    assisted = 0
    assisted_learning = 0
    learning = 0
    instant = 0

    for key, latency_ms, retries, was_assisted, learning_ask, index, offered in rows:
        label = str(key or "")
        options[label] = options.get(label, 0) + 1

        slot = _number(index)
        offered_count = _number(offered)
        if slot is not None and slot >= 0 and offered_count and offered_count > 0:
            positioned += 1
            taken[int(slot)] = taken.get(int(slot), 0) + 1
            for position in range(int(offered_count)):
                chance[position] = chance.get(position, 0.0) + 1 / offered_count

        is_learning = learning_ask is True
        learning += 1 if is_learning else 0
        value = _number(latency_ms)
        if value is not None and value >= 0:
            seconds.append(value / 1000)
            instant += 1 if value < 2000 else 0
        count = _number(retries)
        if count and count > 0:
            retried += 1
            retried_learning += 1 if is_learning else 0
        if was_assisted is True:
            assisted += 1
            assisted_learning += 1 if is_learning else 0

    total = len(rows)
    ordinary = total - learning
    return {
        "choices": total,
        "latency_s": _distribution(seconds),
        "latency_buckets": _histogram(seconds, LATENCY_EDGES, "s"),
        "instant": Rate(instant, len(seconds)),
        # Where they tapped, against what chance would have given each position.
        "positions": _position_rows(taken, chance, positioned),
        "positioned": positioned,
        # Choices with no position: written before `option_index` existed, or a
        # beat that offered no options to have a position in. Excluded from the
        # bias arithmetic, reported so the exclusion is visible.
        "unpositioned": total - positioned,
        "position_instrumented": POSITION_IS_INSTRUMENTED,
        # The options actually taken. Capped, with the tail counted separately, so
        # a long list of one-off slugs cannot become the table.
        "options": _share_rows(options, limit=OPTION_ROWS),
        "distinct_options": len(options),
        "other_options": max(len(options) - OPTION_ROWS, 0),
        "top_option_share": Rate(max(options.values()), total) if options else Rate(0, 0),
        "retried": Rate(retried, total),
        "assisted": Rate(assisted, total),
        "learning_share": Rate(learning, total),
        "assisted_on_learning": Rate(assisted_learning, learning),
        "assisted_on_story": Rate(assisted - assisted_learning, ordinary),
        "retried_on_learning": Rate(retried_learning, learning),
        "retried_on_story": Rate(retried - retried_learning, ordinary),
    }


# ---------------------------------------------------------------------------
# 8 · Audience — who these families are
# ---------------------------------------------------------------------------
def audience(window: Window) -> dict[str, Any]:
    """The children as a population, and the settings their parents chose.

    Everything here is a count over `child.created` and `companion.created` props,
    which carry exactly the shape §13.8 allows: an age *band* rather than a
    birthdate, the *number* of interests rather than the interests. That is enough
    to answer "who are we building for" and not enough to describe anybody.

    **The parental settings are the most under-read numbers in the product.**
    `session_cap_min` is the parent's own answer to "how long should my child be on
    this", and `intensity_ceiling` is their answer to "how brave is my child" — a
    direct statement of what the family wants, given at setup, and the distribution
    of them says more about the market than any engagement chart here. If most
    parents pick fifteen minutes, a twenty-minute story arc is the wrong product.

    `seeded` is the share of parents who filled in interests at all: a parent who
    wrote nothing is a personalizer running on defaults, and their child's
    experience is a different product from the one the seeded families got.
    """
    created = list(
        _events(window, Event.CHILD_CREATED).values_list(
            "props__age_band",
            "props__interests",
            "props__fears",
            "props__avoid_topics",
            "props__intensity_ceiling",
            "props__session_cap_min",
        )
    )
    bands: dict[str, int] = {}
    caps: dict[str, int] = {}
    ceilings: dict[str, int] = {}
    interest_counts: list[float] = []
    seeded = 0
    with_fears = 0
    with_avoid = 0

    for band, interests, fears, avoid, ceiling, cap in created:
        bands[str(band or "")] = bands.get(str(band or ""), 0) + 1
        cap_label = "" if cap is None else f"{cap} min"
        caps[cap_label] = caps.get(cap_label, 0) + 1
        ceiling_label = "" if ceiling is None else str(ceiling)
        ceilings[ceiling_label] = ceilings.get(ceiling_label, 0) + 1
        count = _number(interests)
        if count is not None:
            interest_counts.append(count)
            seeded += 1 if count > 0 else 0
        with_fears += 1 if (_number(fears) or 0) > 0 else 0
        with_avoid += 1 if (_number(avoid) or 0) > 0 else 0

    archetypes: dict[str, int] = {}
    companions = _events(window, Event.COMPANION_CREATED)
    for value in companions.values_list("props__archetype", flat=True):
        archetypes[str(value or "")] = archetypes.get(str(value or ""), 0) + 1

    consent_methods: dict[str, int] = {}
    consent_types: dict[str, int] = {}
    for method, kind in _events(window, Event.PARENT_CONSENT_RECORDED).values_list(
        "props__method", "props__consent_type"
    ):
        consent_methods[str(method or "")] = consent_methods.get(str(method or ""), 0) + 1
        consent_types[str(kind or "")] = consent_types.get(str(kind or ""), 0) + 1

    return {
        "children": len(created),
        "age_bands": _share_rows(bands),
        "session_caps": _share_rows(caps, blank="(default)"),
        "intensity_ceilings": _share_rows(ceilings, blank="(default)"),
        "archetypes": _share_rows(archetypes),
        "consent_methods": _share_rows(consent_methods),
        "consent_types": _share_rows(consent_types),
        "interests_per_child": _distribution(interest_counts),
        "seeded": Rate(seeded, len(created)),
        "with_fears": Rate(with_fears, len(created)),
        "with_avoid_topics": Rate(with_avoid, len(created)),
    }


# ---------------------------------------------------------------------------
# 9 · Content health — what the machine is serving
# ---------------------------------------------------------------------------
def content_health(window: Window) -> dict[str, Any]:
    """The safety layer's output mix, the learning load, and how hard the stories are.

    **`safety` is the number that must be watched as volume grows.** Every beat
    carries the status the safety layer gave it, so this is the proportion of a
    child's evening that came from the pre-vetted fallback pool rather than from a
    generation that passed — and a rising fallback share is the product quietly
    degrading while every engagement number on this page still looks fine. It sits
    here rather than in §22.5's guardrails because it is not an incident: it is the
    system working, and the question is how often it has to.

    `interactions` is what the child was asked to *do* — a mix that has collapsed to
    one interaction type is a story engine that has stopped varying, which children
    notice long before a metric does.

    `difficulty` and `estimated_beats` come off `adventure.started`, so they say
    what the planner *intended*; `storyflow` says what actually happened. Read
    together they answer whether the arcs are too long, which is a planner setting
    and a one-line change.
    """
    statuses: dict[str, int] = {}
    interactions: dict[str, int] = {}
    learning_asks = 0
    beats = 0
    for status, kind, ask in _events(window, Event.BEAT_DELIVERED).values_list(
        "props__safety_status", "props__interaction", "props__learning_ask"
    ):
        beats += 1
        statuses[str(status or "")] = statuses.get(str(status or ""), 0) + 1
        interactions[str(kind or "")] = interactions.get(str(kind or ""), 0) + 1
        learning_asks += 1 if ask is True else 0

    difficulties: list[float] = []
    estimated: list[float] = []
    objectives: dict[str, int] = {}
    returning = 0
    starts = 0
    for difficulty, planned, objective, is_returning in _events(
        window, Event.ADVENTURE_STARTED
    ).values_list(
        "props__difficulty", "props__estimated_beats", "props__objective", "props__returning_place"
    ):
        starts += 1
        value = _number(difficulty)
        if value is not None:
            difficulties.append(value)
        beats_planned = _number(planned)
        if beats_planned is not None:
            estimated.append(beats_planned)
        objectives[str(objective or "")] = objectives.get(str(objective or ""), 0) + 1
        returning += 1 if is_returning is True else 0

    incidents: dict[str, int] = {}
    severities: dict[str, int] = {}
    for category, severity in _events(window, Event.SAFETY_INCIDENT).values_list(
        "props__category", "props__severity"
    ):
        incidents[str(category or "")] = incidents.get(str(category or ""), 0) + 1
        severities[str(severity or "")] = severities.get(str(severity or ""), 0) + 1

    return {
        "beats": beats,
        "safety": _share_rows(statuses),
        "interactions": _share_rows(interactions),
        "learning_asks": Rate(learning_asks, beats),
        "difficulty": _distribution(difficulties),
        "estimated_beats": _distribution(estimated),
        "objectives": _share_rows(objectives, limit=12, blank="(none set)"),
        "returning_places": Rate(returning, starts),
        "incident_categories": _share_rows(incidents),
        "incident_severities": _share_rows(severities),
    }


# ---------------------------------------------------------------------------
# 10 · The data dictionary — what we collect, in plain words
# ---------------------------------------------------------------------------

#: Every event the product records, what it carries, and what it pays for.
#:
#: **This exists because a dashboard is only trustworthy if you know what is
#: behind it**, and the honest answer here is short enough to print: eleven
#: events, and none of them carries a word a child said or a word said to them.
#: Somebody reading a number on this page should be able to see, on the same page,
#: exactly which fact produced it and exactly what we chose not to keep.
#:
#: It is a literal rather than something derived from the code, because the third
#: column is a *judgement* — "what this makes answerable" is not in the schema. It
#: is checked against `Event.NAMES` by `catalogue`, so an event added without a
#: line here shows up as an unexplained row rather than silently vanishing.
CATALOGUE: tuple[tuple[str, str, str, str], ...] = (
    (
        Event.PARENT_SIGNED_UP,
        "An account was created. The time, and the account.",
        "No name, no source, no marketing tag.",
        "Signups, the roster, every funnel denominator, the signup clock.",
    ),
    (
        Event.PARENT_CONSENT_RECORDED,
        "Consent was given: its type and the method.",
        "Not the consent record itself — that is the audit trail, kept apart.",
        "The consent mix, and step two of the funnel.",
    ),
    (
        Event.CHILD_CREATED,
        "A child profile was added: age band, how many interests, fears and "
        "avoid-topics were filled in, the intensity ceiling and session cap.",
        "No name, no birthdate, and none of the interests or fears themselves — " "counts only.",
        "Who these families are, the parental settings, funnel step three.",
    ),
    (
        Event.COMPANION_CREATED,
        "A companion was made, and its archetype.",
        "Not its name, not its look.",
        "Archetype popularity.",
    ),
    (
        Event.ONBOARDING_COMPLETED,
        "The parent finished setup. Once per account.",
        "Nothing else.",
        "Activation's first half.",
    ),
    (
        Event.ADVENTURE_STARTED,
        "A story began: which world, how long the planned arc is, the objective, "
        "the difficulty, and whether the place is one they have been before.",
        "Not the title — a title is a sentence about this child's story.",
        "The play clock, the daily series, worlds, what the planner intended.",
    ),
    (
        Event.BEAT_DELIVERED,
        "One turn of the story went out: its number in the sequence, the safety "
        "status it was given, what the child was asked to do, and whether it was "
        "a learning ask.",
        "Nothing about what the beat says.",
        "The reach curve, the safety mix, the interaction mix.",
    ),
    (
        Event.CHOICE_MADE,
        "The child answered: which option they took, where it sat on screen and "
        "how many were offered, how long they took, how many retries, and whether "
        "the companion helped.",
        "Not the words on the option, and never what the child typed or said. The "
        "key is a slug of the label; the position is just a number.",
        "Decision time, position bias, help and retry rates.",
    ),
    (
        Event.ADVENTURE_COMPLETED,
        "A story finished: how many beats, which world, which objective.",
        "Not the ending.",
        "Completion rate, cohorts, DWA, activation.",
    ),
    (
        Event.SESSION_ENDED,
        "A sitting closed: how long, how many beats, why it ended, and whether a "
        "story finished in it.",
        "Nothing about what happened inside it.",
        "Time played, sitting length, pacing, the wellbeing guardrails.",
    ),
    (
        Event.SAFETY_INCIDENT,
        "The safety layer acted: severity, category, what it did, whether it " "escalated.",
        "Not the text that triggered it.",
        "The incident counts, and the stop-the-line number.",
    ),
)


def reading(window: Window) -> dict[str, Any]:
    """What came of asking children to read a line aloud.

    **The only reading in this module that does not come from the event log.**
    Everything else here folds `Event` rows; a reading attempt is a row in
    `reading.ReadingAttempt` and there is no event for it. That is a deliberate
    crossing, like `accounts` reaching through `Event.parent` for an email, and it
    is made for the same reason: the fact is not in the log and the question is
    worth answering. Whether reading should also emit an event is a Phase 2
    decision — it wants the score alongside it.

    **Nothing here is a grade, because nothing has listened.** `spoke` is whether
    a device sent us audio, which is a fact about a microphone and a child's
    willingness, not about how well they read. There is no accuracy figure in the
    schema to report and none is implied — Phase 2 builds the first one.

    `invited` counts rows, so it is "asked and answered", not "offered": a child
    who saw the invitation and tapped past it never reaches the endpoint and is
    not here. The gap between this and the eligible population is not knowable
    from these rows and is not guessed at.
    """
    rows = list(
        ReadingAttempt.objects.filter(
            created_at__gte=window.since, created_at__lt=window.until
        ).values_list(
            "child_id", "passage_id", "passage_text", "spoke", "duration_ms", "audio_kept"
        )
    )

    invited = len(rows)
    spoke = [row for row in rows if row[3]]
    children = {row[0] for row in rows}
    durations = [float(row[4]) for row in spoke if row[4] is not None]

    # Which sentences actually get read, so a line nobody will attempt is
    # findable. Keyed by id, labelled by the text the child saw.
    by_line: dict[str, dict[str, Any]] = {}
    for _child, passage_id, text, said, _ms, _kept in rows:
        line = by_line.setdefault(
            passage_id, {"passage_id": passage_id, "text": text, "invited": 0, "spoke": 0}
        )
        line["invited"] += 1
        if said:
            line["spoke"] += 1

    lines = sorted(by_line.values(), key=lambda row: (-row["invited"], row["passage_id"]))
    for line in lines:
        line["share"] = Rate(line["spoke"], line["invited"])

    return {
        "invited": invited,
        "spoke": len(spoke),
        "quiet": invited - len(spoke),
        "children": len(children),
        "kept": sum(1 for row in rows if row[5]),
        "spoke_share": Rate(len(spoke), invited),
        "seconds": _distribution([value / 1000 for value in durations]),
        "lines": lines,
        "max_invited": max((line["invited"] for line in lines), default=0),
    }


def catalogue(window: Window) -> list[dict[str, Any]]:
    """The data dictionary, with each event's live count in this window.

    The count is what turns documentation into a diagnostic. A row that reads
    "powers the reach curve" next to a count of zero is the fastest way to tell a
    metric that has genuinely fallen quiet from an emit site that was never wired —
    and those two look identical everywhere else on the dashboard.

    Any name in `Event.NAMES` with no line in `CATALOGUE` is appended with its
    description missing rather than dropped, so adding an event and forgetting to
    document it is visible on the page instead of invisible.
    """
    counts = dict(
        _events(window, *sorted(Event.NAMES))
        .values_list("name")
        .annotate(total=models.Count("id"))
        .values_list("name", "total")
    )
    rows = [
        {
            "name": name,
            "carries": carries,
            "withheld": withheld,
            "powers": powers,
            "count": counts.get(name, 0),
        }
        for name, carries, withheld, powers in CATALOGUE
    ]
    documented = {name for name, _, _, _ in CATALOGUE}
    rows += [
        {
            "name": name,
            "carries": "(not yet described here)",
            "withheld": "",
            "powers": "",
            "count": counts.get(name, 0),
        }
        for name in sorted(Event.NAMES - documented)
    ]
    return rows


# ---------------------------------------------------------------------------
# The whole reading, in one call
# ---------------------------------------------------------------------------
def snapshot(window: Window, *, zone: zoneinfo.ZoneInfo) -> dict[str, Any]:
    """Every reading in this module, for one window and one clock.

    One entry point for the same reason `metrics.snapshot` is one: the dashboard
    asks for "the insights" and gets a fixed set, so a section cannot be added to
    the page and silently left out of `manage.py metrics`, or the other way round.
    """
    return {
        "clock": clock(window, zone=zone),
        "daily": daily(window, zone=zone),
        "accounts": accounts(window, zone=zone),
        "activation_speed": activation_speed(window),
        "attention": attention(window),
        "storyflow": storyflow(window),
        "interaction": interaction(window),
        "audience": audience(window),
        "content": content_health(window),
        "reading": reading(window),
        "catalogue": catalogue(window),
    }
