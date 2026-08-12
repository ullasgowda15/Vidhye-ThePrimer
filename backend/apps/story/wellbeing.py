"""Anti-over-engagement, enforced (PRODUCT.md §13.6, §12.7).

> *"Hard rules, enforced in product: session length cap and daily time window
> (parent-set); a warm 'time to go' ending that the companion itself delivers
> positively; no child-directed notifications, streaks-as-pressure, FOMO, or dark
> patterns."*

This module is the enforcement half of that sentence. It answers two questions
and nothing else:

    winding_down(adventure) -> is this sitting at its cap?
    window_open(child)      -> may a story start right now?

**Why it is a module and not a branch in the orchestrator.** Because §13.6 is the
one part of the product designed to work *against* our own engagement metric, and
a rule like that has to be somewhere a reviewer can read it whole. Scattered
across the three call sites that need it, "does the cap actually end the story"
becomes a question nobody can answer without tracing a request.

**The cap ends the story warmly; it never cuts it off.** That distinction is the
whole ethical content of the feature and it is why this file contains no way to
stop a stream. When the cap is reached, `winding_down` becomes true, and the
*only* thing that happens is that `orchestrator.is_final_seq` starts saying yes:
the beat the child is about to be told becomes the last one, generated under
`FINAL_BEAT_RULE` plus `CAP_CLOSE_RULE`, so the companion lands the story, says
goodbye by name and plants tomorrow's hook. The child answers nothing they were
mid-way through and loses nothing they were holding. A hard cutoff at the cap
would be a *worse* wellbeing outcome than no cap at all — a child pulled out of an
unresolved story is a child who has to be soothed, and the parent learns not to
use the control.

**The cap is measured on the sitting, not on the story, and a sitting is not one
`PlaySession` row.** This is the part that would have quietly not worked.
`orchestrator.create_adventure` opens a session per adventure, and the warm close
offers the child "Again!" — so a cap read off `PlaySession.started_at` alone
restarts from zero every time a child taps for another story, which is precisely
the child it exists to protect. `sitting_started_at` therefore walks backwards
over the child's recent sessions and joins any two separated by less than
`SITTING_GAP_MIN`: three stories in a row after school is one sitting, and coming
back after dinner is a new one. A three-minute gap is a child choosing to carry
on; an hour is a child who went and did something else, which is the outcome §9.5
wants and must not be penalised.

**The window is a door, not a shutter.** It is checked when a story would
*start*. A story already under way when the window closes is allowed to reach its
warm close, for the same reason the cap does not cut: nothing about this product
takes something away from a child mid-sentence. That is a deliberate leniency of
at most one adventure and it is stated here rather than discovered.

**Nothing in this file notifies anybody, and nothing in it counts anything.**
There is no streak, no "you have N minutes left", no comeback nudge, no reward for
returning. Those are the things §13.6 forbids and the reason the forbidding is
credible is that there is nowhere in the codebase they exist to be turned on.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from django.utils import timezone

from apps.profiles.models import Child
from apps.profiles.settings import ChildSettings, settings_for
from apps.story.models import Adventure, PlaySession

logger = logging.getLogger(__name__)

#: Why a story may not start right now. `None` means it may.
CLOSED_TIME_WINDOW = "time_window"

#: Why the current story is being landed early. Written onto the adventure's plan
#: so the transparency log and the session report can say "this ended at the cap"
#: rather than leaving a parent to infer it from a short story.
CLOSED_SESSION_CAP = "session_cap"


@dataclass(frozen=True, slots=True)
class Gate:
    """Whether the child may begin a story, and what to say if not.

    `message` is written for the **child's** screen and is the companion's own
    voice — warm, specific, never a rule quoted back at a four-year-old. It is
    deliberately part of the refusal rather than left to the client: "the door is
    shut" and "here is how a companion says the door is shut" must not be two
    decisions that can drift apart, and a client that invented its own wording
    would eventually invent a disappointed one.
    """

    open: bool
    reason: str = ""
    message: str = ""


#: What the companion says when the day's window has closed. Second person, warm,
#: and it *points at the real world* (§9.5) rather than at coming back to the app —
#: which is the difference between an ending and a hook.
WINDOW_CLOSED_LINE = (
    "{companion} is having a rest just now. Let's go and find something to do — "
    "and we'll have our story next time."
)


def companion_name(child: Child) -> str:
    """The companion's name, or a warm stand-in.

    Never raises and never says "no companion": a child with none cannot reach a
    story at all (`story.views.CompanionRequired`), so this only fires in a test or
    a half-built account, and even there a child must not be shown a gap.
    """
    companion = getattr(child, "companion", None)
    return getattr(companion, "name", None) or "Your friend"


def window_open(child: Child, *, controls: ChildSettings | None = None) -> bool:
    """Is the child inside their daily time window (§13.6)?

    True whenever no window is set, which is the default. See
    `profiles.settings.TimeWindow` for the wrapping case (19:00 to 07:00) and for the
    timezone caveat, which is real and is written down rather than hidden.
    """
    controls = controls or settings_for(child)
    return controls.time_window.is_open()


def gate_for(child: Child) -> Gate:
    """May this child start a story right now, and what does the companion say?

    Only the window closes this door. The **cap does not**, and that is worth
    being explicit about: a cap is a bound on one sitting, so the honest response
    to reaching it is to land the story the child is in — which `winding_down`
    does — and not to refuse them the app. A cap that also locked the door would
    turn a wellbeing control into a punishment, and a parent who wants a hard daily
    limit has the time window for exactly that.
    """
    controls = settings_for(child)
    if not controls.time_window.is_open():
        logger.info(
            "wellbeing.window_closed child=%s window=%s",
            child.pk,
            controls.time_window.as_text(),
        )
        return Gate(
            open=False,
            reason=CLOSED_TIME_WINDOW,
            message=WINDOW_CLOSED_LINE.format(companion=companion_name(child)),
        )
    return Gate(open=True)


#: Two `PlaySession` rows less than this far apart are the same sitting.
#:
#: Fifteen minutes, because the gap it has to straddle is "the child tapped Again"
#: — seconds — and the gap it must *not* straddle is "the child went and did
#: something else and came back", which is the outcome §9.5 actively wants. A
#: number small enough to reset on a genuine break and large enough that a nappy
#: change, a drink of water or a slow reconnect does not hand the child a fresh
#: cap.
SITTING_GAP_MIN = 15

#: How many earlier sessions the walk-back will look at. A bound rather than a
#: guess: at one story per five minutes this covers over an hour and a half of
#: continuous play, and it keeps a pathological row set from turning one beat into
#: an unbounded query. Reaching it is logged.
SITTING_LOOKBACK = 24


def sitting_started_at(child: Child, session: PlaySession) -> datetime.datetime:
    """When this child actually sat down — across joined sessions (§13.6).

    Walks backwards from `session` over the child's earlier sessions, absorbing
    each one whose end is less than `SITTING_GAP_MIN` before the next one's start.
    Stops at the first real gap.

    One indexed query (`PlaySession` has `(child, -started_at)`), evaluated once
    per beat and memoised for the rest of that request by `winding_down`.

    A previous session with no `ended_at` — a child who closed the tab without the
    unload firing — is measured from its `started_at`, which errs toward joining.
    That is the safe direction here: the cost of joining two sittings that were
    really separate is a shorter story, and the cost of the opposite is a cap that
    does not fire.
    """
    start = session.started_at
    earlier = list(
        PlaySession.objects.filter(child=child, started_at__lt=start)
        .order_by("-started_at")
        .values_list("started_at", "ended_at")[:SITTING_LOOKBACK]
    )
    for started, ended in earlier:
        gap_min = ((start - (ended or started)).total_seconds()) / 60.0
        if gap_min > SITTING_GAP_MIN:
            return start
        start = started

    if len(earlier) == SITTING_LOOKBACK:
        logger.warning(
            "wellbeing.sitting_lookback_exhausted child=%s sessions=%d",
            child.pk,
            SITTING_LOOKBACK,
        )
    return start


def session_minutes(session: PlaySession | None) -> float:
    """How long **one session row** has been running, in minutes.

    Measured from `started_at` to now for a live sitting, and to `ended_at` for a
    finished one — so this is meaningful in a report as well as in the loop.
    Negative clock skew floors at zero rather than reading as a sitting that has
    not begun.

    Not what the cap is measured against; see `sitting_minutes`.
    """
    if session is None:
        return 0.0
    end = session.ended_at or timezone.now()
    return max(0.0, (end - session.started_at).total_seconds() / 60.0)


def sitting_minutes(child: Child, session: PlaySession | None) -> float:
    """How long the child has been playing in this sitting, across joined sessions."""
    if session is None:
        return 0.0
    return max(0.0, (timezone.now() - sitting_started_at(child, session)).total_seconds() / 60.0)


def cap_reached(child: Child, session: PlaySession | None) -> bool:
    """Has this sitting run past the parent's cap (§13.6)?

    False when there is no session row. A sitting we cannot measure is not a
    sitting we may cut short on a guess — and it is already logged as a gap by
    `orchestrator._close_out_session`, which loses three pipelines to the same
    null.
    """
    if session is None:
        return False
    return sitting_minutes(child, session) >= settings_for(child).session_cap_min


def winding_down(adventure: Adventure) -> bool:
    """Should the beat about to be told be this adventure's warm close?

    True once the sitting has reached the cap.

    **Memoised on the adventure object, which means memoised per request.** One
    stream open delivers one beat (`story.streaming`), and `is_final_seq` is asked
    four times while that beat is built — by the prompt, by the choice
    normaliser, by the fallback and by the wire payload. Those four must give the
    same answer or the child gets a goodbye with two choice cards under it, and
    asking the clock four times is how they would eventually differ. Adventure rows
    are re-fetched per request, so the *next* beat re-decides from scratch: a
    parent who raises the cap while their child is playing has raised it (§12.7),
    which is what "honored immediately" has to mean here.

    Never true for an adventure that has already finished — there is nothing left
    to land — and never true before the first beat exists. That second exclusion is
    the one worth arguing for: a child who taps the star one minute after the cap
    has passed would otherwise get a single beat that is entirely a goodbye, which
    reads as the app refusing them. They get one short, complete story instead,
    which is a smaller wellbeing cost than the confusion.
    """
    cached = getattr(adventure, "_winding_down", None)
    if cached is not None:
        return cached

    decided = (
        adventure.status == Adventure.Status.IN_PROGRESS
        and adventure.beats.exists()
        and cap_reached(adventure.child, adventure.session)
    )
    if decided:
        logger.info(
            "wellbeing.cap_reached adventure=%s child=%s cap_min=%d",
            adventure.pk,
            adventure.child_id,
            settings_for(adventure.child).session_cap_min,
        )
    # `setattr` rather than an attribute assignment, and not to dodge mypy: the
    # read on the way in is already a `getattr` with a default, because this is a
    # cache stashed on a model instance that has no such field. Writing one half
    # dynamically and the other half statically is what made mypy flag the pair as
    # an error rather than as the deliberate memo it is. Both halves now say the
    # same thing about what this attribute is.
    setattr(adventure, "_winding_down", decided)  # noqa: B010
    return decided


def ended_reason_for(adventure: Adventure, reason: str) -> str:
    """The sitting's `ended_reason`, upgraded to `cap` when the cap is why (§13.6).

    Only ever upgrades a *completed* close. A child who left mid-story left, and
    relabelling that as "reached the cap" would tell a parent their control worked
    when what actually happened is that a four-year-old wandered off — the report
    is meant to be honest about the difference (§12.1).

    Read from the clock rather than taken from the client, because the client that
    sends `end` is the child's tablet and it has no idea what the cap is.
    """
    if reason != PlaySession.EndedReason.COMPLETED:
        return reason
    if cap_reached(adventure.child, adventure.session):
        return PlaySession.EndedReason.CAP
    return reason


def close_reason(adventure: Adventure) -> str:
    """`CLOSED_SESSION_CAP` when the cap is what is ending this story, else `""`.

    Used to stamp the adventure and to pick the extra prompt rule, so "why did
    today's story end where it did" is a fact on the row rather than an inference
    from its length (§12.1's honesty requirement, and §13.6's own claim that the
    cap is visible to the parent).
    """
    return CLOSED_SESSION_CAP if winding_down(adventure) else ""
