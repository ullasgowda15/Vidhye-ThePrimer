"""Session and weekly reports (PRODUCT.md §12.1, §12.2, §12.4, §13.9).

    a finished sitting  ->  a short, true, specific recap
    a week of those     ->  a digest, a milestone if one was reached, a nudge

**Almost none of this is generated.** Where the child went, the choice they made,
the objective that was exercised and how it went are read off `story.Beat`,
`story.Choice` and `learning.LearningProgress` and rendered by the functions
below. One sentence per report — the human highlight — comes from a model, and
even that is checked against the session before it is kept
(`apps.analytics.prompts`). §12.1's requirement is *honest and specific*, and the
cheapest way to be honest is to not ask anybody to make anything up.

**Nothing here recomputes what another module owns.** The mastery estimate is
`apps.learning`'s (read live, translated for a parent by
`apps.analytics.skills`); the interest ranking is `apps.memory.interest`'s; the
milestone rollup is the catalog's. This module joins them and writes English.

**The weekly report is computed on read**, from the stored session reports plus
the live skill model and interest graph. See `analytics.models` on why there is no
weekly table.

**Movement over a week is measured, not modelled.** `SessionReport.skills` records
what the skill model said at the end of each sitting, so "this moved on this week"
is the difference between two things we wrote down at the time — not a re-run of
§10.5's arithmetic against today's catalog. When there is no earlier snapshot to
compare against, the report says "first practised this week" rather than inventing
a baseline, which is the same rule §13.9 applies to everything else here.

The baseline is looked up **per objective** (`_baseline_index`) and not per report.
That distinction is load-bearing rather than fussy: §10.3 interleaves domains, so
the last report before a week is usually about something else entirely, and reading
it as the baseline for everything makes "first practised this week" and "a step
reached this week" into claims we make about skills the child has had for months.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.analytics import prompts, skills
from apps.analytics.models import HighlightSource, SessionReport
from apps.learning import engine
from apps.learning.demands import COMPARE, COUNT, demand_for
from apps.learning.models import LearningProgress, Objective
from apps.memory import interest
from apps.memory import prompts as memory_prompts
from apps.profiles.models import Child
from apps.reading.models import ReadingAttempt
from apps.story.models import Adventure, Beat, Choice, PlaySession

logger = logging.getLogger(__name__)

#: How many top interests a report snapshots and a weekly digest names. The same
#: handful `interest.TOP_INTERESTS` gives the planner: past the top few, a list of
#: "what your child loves" stops being a window and becomes a word cloud.
TOP_INTERESTS = 5

#: How much mastery has to move inside a week before the report says it moved.
#: Below this it is arithmetic noise from one more observation, and a parent told
#: "moved on" every week regardless would learn to ignore the word.
MOVEMENT_EPSILON = 0.02

#: How many reports the session list returns. Two months of daily play, which is
#: further back than any parent scrolls in one sitting and is bounded.
REPORT_PAGE = 60

#: The longest "what happened next" may run in a session report. One sentence of
#: the story's own narration; the rare very long one is trimmed at a word
#: boundary, and the whole beat is a click away in the transparency log (§12.6).
MAX_CONSEQUENCE_CHARS = 160

#: How far back the interest trend looks. §11.7's own example is a month
#: ("this month: space, then dinosaurs, now ocean") and the graph's half-life is
#: 30 days, so two months is one full fade plus the phase before it — long enough
#: to show a change of heart, short enough that last spring is not still being
#: reported as news.
TREND_WINDOW_DAYS = 60

#: How many phases the trend sentence names. Three is the shape of §11.7's own
#: example and is where the sentence stops being readable.
TREND_PHASES = 3

#: How many past reports the weekly digest will read through looking for a
#: baseline snapshot of the objectives practised this week.
#:
#: The walk stops as soon as every one of them has been found, so this bound is
#: only reached by a child who practised something this week and *never* before —
#: in which case "first practised this week" is the true answer anyway and the
#: scan has cost one indexed query. Sized well past a year of daily play so the
#: cap is a guard against an unbounded read rather than a limit on memory.
BASELINE_SCAN = 400


# ---------------------------------------------------------------------------
# One sitting (§12.1)
# ---------------------------------------------------------------------------
def generate_session_report(session: PlaySession) -> SessionReport:
    """Write the report for one finished sitting. Idempotent.

    Returns the existing row if this sitting already has one, so a redelivered
    Celery task, a second `end`, and the read-path fallback in
    `report_for_session` all converge on one report rather than three. The gate is
    `session_key` and not the FK, because the FK is allowed to go null (§17.5).

    Never raises for content reasons. A sitting with no adventures, no choices and
    no objective still gets a report — a short and honest one — because "your child
    opened it and closed it again" is a true thing a parent may want to see, and a
    missing report looks like a broken product rather than a quiet afternoon.
    """
    existing = SessionReport.objects.filter(session_key=session.pk).first()
    if existing is not None:
        logger.info("analytics.report_noop session=%s reason=already", session.pk)
        return existing

    child = session.child
    adventure = session.adventures.order_by("started_at").first()
    beats = list(_beats_of(adventure))
    choices = _choices_by_beat(adventure)

    evidence = engine.evidence_from(adventure) if adventure is not None else []
    objective = engine.objective_by_code(adventure.primary_objective_id if adventure else None)

    highlight, source = _highlight(child, session, beats, choices)

    report = SessionReport(
        child=child,
        session=session,
        session_key=session.pk,
        occurred_at=session.started_at,
        duration_s=session.duration_s or 0,
        ended_reason=session.ended_reason or "",
        beats=len(beats),
        adventure_title=(adventure.title if adventure else ""),
        world_name=str((adventure.plan or {}).get("world_name") or "") if adventure else "",
        place_name=str((adventure.plan or {}).get("place") or "") if adventure else "",
        adventure=adventure,
        adventure_key=(adventure.pk if adventure else None),
        key_choice=_key_choice(beats, choices),
        objective_code=(objective.code if objective else ""),
        milestone_name=(objective.milestone.name if objective else ""),
        how_it_went=_how_it_went(objective, evidence),
        highlight=highlight,
        highlight_source=source,
        skills=_skill_snapshot(child, evidence),
        interests=[
            {"topic": topic.name, "weight": round(topic.weight, 3)}
            for topic in interest.top_interests(child, limit=TOP_INTERESTS)
        ],
    )

    try:
        with transaction.atomic():
            report.save()
    except IntegrityError:
        # Two workers wrote at once. The unique constraint on `session_key` is the
        # real gate; the check at the top of this function is only the cheap one.
        logger.info("analytics.report_raced session=%s", session.pk)
        return SessionReport.objects.get(session_key=session.pk)

    logger.info(
        "analytics.report session=%s child=%s beats=%d objective=%s highlight=%s",
        session.pk,
        child.pk,
        len(beats),
        report.objective_code or "-",
        source,
    )
    return report


def report_for_session(child: Child, session_key: Any) -> SessionReport | None:
    """This sitting's report, generating it now if the worker never did.

    The dashboard must not have a hole in it because a broker was down when a child
    finished a story. Generating on read is a few queries and, at worst, one model
    call — on a request a parent made deliberately, not on a child's path.

    Returns None when the sitting does not exist or is not this child's; the caller
    turns that into a 404. A sitting still in progress is *not* reported on: a
    report is a recap, and there is nothing yet to recap.
    """
    report = SessionReport.objects.filter(child=child, session_key=session_key).first()
    if report is not None:
        return report

    session = PlaySession.objects.filter(pk=session_key, child=child).first()
    if session is None or session.ended_at is None:
        return None
    return generate_session_report(session)


def _beats_of(adventure: Adventure | None) -> list[Beat]:
    if adventure is None:
        return []
    return list(Beat.objects.filter(adventure=adventure).order_by("seq"))


def _choices_by_beat(adventure: Adventure | None) -> dict[Any, Choice]:
    if adventure is None:
        return {}
    return {choice.beat_id: choice for choice in Choice.objects.filter(adventure=adventure)}


# ---------------------------------------------------------------------------
# Where they went, and what they chose (§12.1)
# ---------------------------------------------------------------------------
def _key_choice(beats: list[Beat], choices: dict[Any, Choice]) -> str:
    """The branch that mattered, and what the story did about it.

    "The branch that mattered" is the last beat that offered the child a real
    choice — two or more cards — rather than the first. A five-beat arc opens with
    a decision that sets the direction and closes with one the story has been
    building toward, and the second is the one a parent recognises when their child
    tells them about it.

    **What came of it is the next beat's own opening line, not
    `Choice.consequence_summary`.** That column looks like the right one and is
    not: `orchestrator.consequence_summary` writes it *for the next generation*,
    so it is in machine register and it carries `Beat.next_hint` — our private
    instruction to the writer. Rendered on a parent's page it reads
    *"At beat 3, Aarav chose … The beat's own steer for what follows: if the child
    counts -> cross the river"*, which is the seams of the product showing through
    the one surface that is supposed to be a recap. The next beat's narration is
    what actually happened next, in the story's words, and the parent can read the
    rest of it verbatim one click away (§12.6).

    Empty when nothing branched. A sitting where the child only tapped to carry on
    had no key choice, and saying so by omission is better than promoting a
    "continue" tap into a decision.
    """
    for index in range(len(beats) - 1, -1, -1):
        beat = beats[index]
        if len(beat.options) < 2:
            continue
        choice = choices.get(beat.pk)
        if choice is None:
            continue
        option = beat.option(choice.chosen_key)
        label = str((option or {}).get("label") or "").strip()
        if not label:
            continue
        after = beats[index + 1] if index + 1 < len(beats) else None
        consequence = _first_sentence(after.narration) if after is not None else ""
        if consequence:
            return f'Chose "{label}". What happened next: {consequence}'
        return f'Chose "{label}".'
    return ""


def _first_sentence(text: str) -> str:
    """The opening sentence of a beat's narration, flattened and bounded.

    Verbatim as far as it goes — this is story the child was actually told, and
    the transparency log shows the whole of it — but one sentence, because the
    report is a recap and a parent who wants the prose has a link to it.
    """
    flat = " ".join(str(text or "").split())
    if not flat:
        return ""
    stop = re.search(r"[.!?]", flat)
    sentence = flat[: stop.end()] if stop else flat
    if len(sentence) <= MAX_CONSEQUENCE_CHARS:
        return sentence
    return sentence[:MAX_CONSEQUENCE_CHARS].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------------
# The objective, and how it went (§12.1, §10.5)
# ---------------------------------------------------------------------------
def _ask_phrase(item: engine.Evidence, objective: Objective) -> str:
    """What the child was actually asked, reconstructed from the beat's own tag.

    `demands.demand_for` is pure and the beat stores the difficulty it was stamped
    with, so this is the ask that was made rather than a description of one. That
    matters more than it looks: after the Day-10 fix the stamped difficulty is the
    *delivered* one, so "asked to count 3 things" is what the picture held even
    when the selector had asked for a harder version than `count_to_3` can offer.
    """
    demand = demand_for(objective.code, objective.domain, item.difficulty)
    if demand.kind == COUNT:
        return f"counting {demand.quantity} things"
    if demand.kind == COMPARE:
        return f"saying which was more, {demand.quantity} or {demand.smaller}"
    return "finding what a word described"


def _outcome_phrase(item: engine.Evidence) -> str:
    """How it went, in words with no failure state in them (§8.5, §12.3).

    A child who did not get there is not marked wrong — there are no wrong answers
    in this product — and is not quietly rounded up to a success either, because a
    parent who later watches their child count would notice. "Still new" is the
    true middle: we asked, they had a go, it is not in hand yet.
    """
    if not item.correct:
        return "had a go — still new"
    if item.assisted:
        return "got there together"
    if item.retries:
        return "got there on the second go"
    if item.easy:
        return "straight away, on their own"
    return "on their own"


def _how_it_went(objective: Objective | None, evidence: list[engine.Evidence]) -> str:
    """The objective's line in the report (§12.1).

    Names the ask and the outcome for each time it came up, in order, so a parent
    can see the within-session shape §10.4 produces: five things with a hand, then
    three on their own, is a different afternoon from three then five and reads as
    one.
    """
    if objective is None:
        return ""
    if not evidence:
        return (
            f"{objective.milestone.name.lower().capitalize()} was what today's story was "
            "quietly built around, but it never came up — the sitting ended first."
        )
    parts = [f"{_ask_phrase(item, objective)} — {_outcome_phrase(item)}" for item in evidence]
    return "; ".join(parts).capitalize() + "."


def _skill_snapshot(child: Child, evidence: list[engine.Evidence]) -> list[dict[str, Any]]:
    """What was asked, what happened, and what we believed afterwards.

    The counts are facts about this sitting and are true forever. `mastery` and
    `status` are what the skill model said when this report was written — a
    snapshot, kept so a weekly report can measure movement against something we
    actually recorded rather than against a re-derivation (see the module
    docstring). Nothing reads these two as current: the dashboard's live status
    comes from `skills.skill_views`.
    """
    if not evidence:
        return []

    grouped: dict[str, list[engine.Evidence]] = {}
    for item in evidence:
        grouped.setdefault(item.objective_code, []).append(item)

    objectives = {
        objective.code: objective
        for objective in Objective.objects.filter(code__in=grouped).select_related("milestone")
    }
    rows = {
        row.objective.code: row
        for row in LearningProgress.objects.filter(
            child=child, objective__code__in=grouped
        ).select_related("objective")
    }

    snapshot: list[dict[str, Any]] = []
    for code in sorted(grouped):
        objective = objectives.get(code)
        if objective is None:
            # The catalog changed under a stored beat. Day 10 logs and skips this
            # in the mastery update; the report does the same rather than invent a
            # milestone name for a code nobody can look up any more.
            logger.warning("analytics.unknown_objective child=%s code=%s", child.pk, code)
            continue
        items = grouped[code]
        row = rows.get(code)
        reach = skills.reach_for(objective.code, objective.domain)
        snapshot.append(
            {
                "code": code,
                "domain": objective.domain,
                "milestone": objective.milestone.name,
                "asks": len(items),
                "unaided": sum(1 for item in items if item.correct and not item.struggled),
                "assisted": sum(1 for item in items if item.assisted),
                "retried": sum(1 for item in items if item.retries),
                "mastery": round(row.mastery, 3) if row else 0.0,
                "status": skills.status_for(row, reach=reach) if row else skills.NOT_STARTED,
            }
        )
    return snapshot


# ---------------------------------------------------------------------------
# The highlight (§12.1, §13.9)
# ---------------------------------------------------------------------------
def _highlight(
    child: Child,
    session: PlaySession,
    beats: list[Beat],
    choices: dict[Any, Choice],
) -> tuple[str, str]:
    """The one specific sentence, and where it came from.

    Three outcomes, in order of preference and each one honest:

    * a model sentence that survived `prompts.is_grounded`;
    * a sentence assembled from the child's own contribution, when the model was
      unreachable or wrote something we could not point at;
    * a plain statement that the sitting was a quiet one, when the child did not
      contribute anything to point at.

    The third is not a failure. §12.1 forbids inflation, and most sittings of a
    four-year-old are ordinary; a product that produced a delighted sentence about
    every one of them would be producing noise, and a parent would learn to skip
    the line that matters.

    **A moment has to be the child's.** The specifics a sentence may be grounded
    on are what the child said, answered and chose — not the setting we wrote or
    the characters we invented. Scenery used to be in this set, on the reasoning
    that a sentence naming Bramble should not be rejected for it; but a sentence
    that names *both* Bramble and something the child did already passes on the
    child's half, so the only sentence scenery ever admitted was one about our own
    prose — "Aarav loved the mossy riverbank at dawn", stored as `model` and
    published under a line saying it was checked against what happened. That is
    the sitting the `quiet` branch is for.
    """
    contributions = _child_contributions(beats, choices)
    fallback, fallback_source = _plain_highlight(child, contributions)

    specifics = prompts.specifics_from(*(text for _, text in contributions))
    if not specifics:
        return fallback, fallback_source

    try:
        # An empty log on purpose: `session_digest` falls through to the persisted
        # beats and choices, which is what we want here. STM is discarded at
        # consolidation (§11.2), and this report may be generated afterwards — or
        # regenerated days later on the read path — so a digest built from Redis
        # would be complete on the happy path and empty on every other one.
        digest = memory_prompts.session_digest(session, log=[])
        sentence = prompts.highlight_for(child, digest, specifics)
    except prompts.HighlightUnavailable as exc:
        logger.info("analytics.highlight_fallback session=%s reason=%s", session.pk, exc)
        return fallback, fallback_source
    return sentence, HighlightSource.MODEL


#: The three ways a child puts something of their own into a sitting, strongest
#: first — a word they volunteered, an answer they worked out, a card they took.
#: The same ordering `memory.interest` grades reinforcement by, and for the same
#: reason: a word a four-year-old invents is theirs, and a card they took is
#: partly ours.
SAID = "said"
ANSWERED = "answered"
CHOSE = "chose"


def _child_contributions(beats: list[Beat], choices: dict[Any, Choice]) -> list[tuple[str, str]]:
    """Everything in this sitting that came from the child, strongest first.

    Returned as `(kind, text)` rather than as bare strings so the plain highlight
    can say the true thing about each kind. A counting answer is not a card the
    child "chose", and a report that said *Aarav chose "3" today* would be both
    clumsy and slightly false about what happened — which is a small version of
    exactly the failure §13.9 is about.
    """
    said: list[tuple[str, str]] = []
    answered: list[tuple[str, str]] = []
    chose: list[tuple[str, str]] = []
    for beat in beats:
        choice = choices.get(beat.pk)
        if choice is None:
            continue
        if isinstance(choice.value, str) and choice.value.strip():
            said.append((SAID, choice.value.strip()))
        elif choice.value is not None:
            answered.append((ANSWERED, str(choice.value)))
        option = beat.option(choice.chosen_key) or {}
        if len(beat.options) >= 2 and str(option.get("label") or "").strip():
            chose.append((CHOSE, str(option["label"]).strip()))
    return [*said, *answered, *chose]


def _plain_highlight(child: Child, contributions: list[tuple[str, str]]) -> tuple[str, str]:
    """A highlight with no model in it, for when the generated one cannot be had.

    Built from the strongest thing the child put in, and phrased as what that
    actually was. It is plainer than a generated sentence and it is *true*, which
    is the right way round for this fallback: a parent reading it learns something
    real about their afternoon, and nothing here can be about a moment that did
    not happen.
    """
    name = child.first_name
    if contributions:
        kind, text = contributions[0]
        if kind == SAID:
            return f'{name} came up with "{text}" today.', HighlightSource.DATA
        if kind == ANSWERED:
            return f"{name} worked out the answer was {text} today.", HighlightSource.DATA
        return f'{name} chose "{text}" today.', HighlightSource.DATA
    return (
        f"{name} tapped through today's story without adding much of their own — "
        "a quiet session, which is a perfectly good kind.",
        HighlightSource.QUIET,
    )


# ---------------------------------------------------------------------------
# A week of them (§12.2, §18.7)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class WeeklyReport:
    """§18.7's shape, as a value the serializer renders.

    Two deliberate departures from the example in §18.7, both in the direction of
    honesty and both additive rather than breaking (§18.8):

    * **`movement` is a phrase, not a delta.** The example shows `"+0.2"`. That
      number is on the difficulty scale, its ceiling differs per objective (see
      `analytics.skills`), and 0.2 of it means something different for `count_to_3`
      than for `count_to_10` — so a parent reading "+0.2" is reading something that
      cannot be interpreted without the curriculum in front of them. The field is
      still a string; it now says "moved on this week".
    * **No `new_words` entry.** The example shows one for vocabulary. We do not
      record which words a child met in a story, so the only way to fill it would
      be to guess, and §13.9 is what this whole surface is about. When word
      exposure is tracked, the field arrives with real data behind it.
    """

    week: str
    start: datetime.date
    end: datetime.date
    sessions: int
    completion_rate: float
    highlight: str
    skills: list[dict[str, Any]] = field(default_factory=list)
    milestone: dict[str, Any] | None = None
    interests_trend: list[str] = field(default_factory=list)
    recommendation: str = ""
    #: Lines the child read aloud to their companion this week
    #: (Docs/Build/Reading-Integration.md §8).
    #:
    #: A count and nothing else, deliberately. Phase 1 does not listen to the
    #: audio — every threshold in the detector was tuned against an adult
    #: imitating a child — so "read six lines" is the whole of what is known, and
    #: any adjective next to it would be invented. Accuracy arrives in Phase 2
    #: with a detector that has heard real children.
    lines_read: int = 0


def week_bounds(day: datetime.date) -> tuple[datetime.date, datetime.date]:
    """The Monday-to-Sunday week containing `day`.

    ISO weeks, because §18.7's `"2026-08-01/2026-08-07"` is a seven-day span and a
    week has to start somewhere the same way every time — a rolling "last 7 days"
    would make two reports of the same week disagree depending on when they were
    asked for.
    """
    start = day - datetime.timedelta(days=day.weekday())
    return start, start + datetime.timedelta(days=6)


def generate_weekly_report(child: Child, week: datetime.date | None = None) -> WeeklyReport:
    """The week's digest (§12.2, §18.7). Computed, not stored.

    `week` is any day inside the week wanted; it defaults to today. Everything is
    read from the session reports written at the time plus the live skill model and
    interest graph — see the module docstring on why movement is measured against
    stored snapshots rather than recomputed.
    """
    start, end = week_bounds(week or timezone.localdate())
    reports = list(
        SessionReport.objects.filter(
            child=child, occurred_at__date__gte=start, occurred_at__date__lte=end
        ).order_by("occurred_at")
    )

    touched = _touched(reports)
    before = _baseline_index(child, start, set(touched))

    completed = sum(1 for report in reports if report.ended_reason == "completed")
    live = {view.code: view for view in skills.skill_views(child)}

    return WeeklyReport(
        week=f"{start.isoformat()}/{end.isoformat()}",
        start=start,
        end=end,
        sessions=len(reports),
        completion_rate=round(completed / len(reports), 2) if reports else 0.0,
        highlight=_week_highlight(reports),
        skills=_week_skills(touched, before, live),
        milestone=_week_milestone(touched, before),
        interests_trend=[
            topic.name for topic in interest.top_interests(child, limit=TOP_INTERESTS)
        ],
        recommendation=_recommendation(child, live),
        lines_read=ReadingAttempt.objects.filter(
            child=child,
            created_at__date__gte=start,
            created_at__date__lte=end,
            spoke=True,
        ).count(),
    )


def _week_highlight(reports: list[SessionReport]) -> str:
    """One sentence for the week: the best real moment in it.

    "Best" is `HighlightSource.MODEL` over `DATA` over `QUIET`, then most recent —
    which is to say, the most specific thing we are confident actually happened. A
    week of quiet sessions gets a quiet sentence, because that is what the week
    was.
    """
    if not reports:
        return ""
    # Keyed on `str` rather than on `HighlightSource`, because the column hands
    # back a plain string and that is what is looked up. It *worked* keyed on the
    # enum — `TextChoices` subclasses `str`, so the hashes match — but a reader had
    # to know that to be sure, and if it ever stopped being true every report
    # would silently rank 0 and the "best highlight" would degrade to whichever
    # was most recent. Same values, no cleverness required.
    rank: dict[str, int] = {
        HighlightSource.MODEL.value: 2,
        HighlightSource.DATA.value: 1,
        HighlightSource.QUIET.value: 0,
    }
    best = max(
        reports, key=lambda report: (rank.get(report.highlight_source, 0), report.occurred_at)
    )
    return best.highlight


def _touched(reports: list[SessionReport]) -> dict[str, dict[str, Any]]:
    """What this week practised: objective code -> the last snapshot, and the asks.

    Only objectives the child actually practised this week appear — the full
    curriculum is what the Insights page is for, and a weekly digest listing
    everything they did *not* do would bury the two things they did.
    """
    touched: dict[str, dict[str, Any]] = {}
    for report in reports:
        for entry in report.skills or []:
            code = str(entry.get("code") or "")
            if not code:
                continue
            seen = touched.setdefault(code, {"last": entry, "asks": 0})
            seen["last"] = entry
            seen["asks"] += int(entry.get("asks") or 0)
    return touched


def _baseline_index(
    child: Child, start: datetime.date, codes: set[str]
) -> dict[str, dict[str, Any]]:
    """The last snapshot written for each of these objectives *before* this week.

    **Per objective, and that is the whole point.** The obvious implementation —
    take the most recent report before the week and read it — is wrong here, and
    wrong in a way that produces confident false claims rather than missing ones.
    §10.3's selector interleaves domains, so consecutive sittings deliberately
    practise *different* objectives; the last report before any given week is
    therefore usually about something else, and an objective missing from it would
    be read as an objective with no history. `_movement` would announce a skill
    practised for months as "first practised this week", and `_week_milestone`
    would compare `MASTERED` against a baseline of nothing and report a step as
    crossed *this week* — every week, for as long as the child kept touching it.

    Walked newest-first and stopped as soon as every code is accounted for, so the
    common case reads one or two rows. An objective that genuinely has no earlier
    snapshot is simply absent, which is what lets "first practised this week" mean
    something when it is said.
    """
    if not codes:
        return {}

    wanted = set(codes)
    index: dict[str, dict[str, Any]] = {}
    snapshots = (
        SessionReport.objects.filter(child=child, occurred_at__date__lt=start)
        .order_by("-occurred_at")
        .values_list("skills", flat=True)[:BASELINE_SCAN]
    )
    for snapshot in snapshots:
        for entry in snapshot or []:
            code = str(entry.get("code") or "")
            if code in wanted:
                index[code] = entry
                wanted.discard(code)
        if not wanted:
            break
    return index


def _week_skills(
    touched: dict[str, dict[str, Any]],
    before: dict[str, dict[str, Any]],
    live: dict[str, skills.SkillView],
) -> list[dict[str, Any]]:
    """Skills touched this week, with the movement we recorded for each (§12.2)."""
    rows: list[dict[str, Any]] = []
    for code in sorted(touched):
        seen = touched[code]
        entry = seen["last"]
        view = live.get(code)
        earlier = before.get(code)
        rows.append(
            {
                "domain": str(entry.get("domain") or (view.domain if view else "")),
                "objective": code,
                "milestone": str(entry.get("milestone") or (view.milestone if view else "")),
                "asks": seen["asks"],
                "movement": _movement(earlier, entry),
                "status": view.status if view else str(entry.get("status") or skills.NOT_STARTED),
            }
        )
    return rows


def _movement(earlier: dict[str, Any] | None, latest: dict[str, Any]) -> str:
    """How this objective moved across the week, in a phrase (§12.2).

    Measured between two snapshots we wrote at the time. With no earlier snapshot
    there is nothing to measure and the report says so — "first practised this
    week" is information; a "+0.0" against an invented baseline is not.
    """
    if earlier is None:
        return "first practised this week"

    was = float(earlier.get("mastery") or 0.0)
    now = float(latest.get("mastery") or 0.0)
    if str(earlier.get("status")) != str(latest.get("status")):
        if str(latest.get("status")) == skills.MASTERED:
            return "does this reliably now"
        if str(latest.get("status")) == skills.PRACTISING:
            return "moved from just starting to practising"
    if now - was > MOVEMENT_EPSILON:
        return "moved on this week"
    if was - now > MOVEMENT_EPSILON:
        return "found it harder this week than last"
    return "steady this week"


def _week_milestone(
    touched: dict[str, dict[str, Any]],
    before: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """A milestone step reached this week, or None (§10.6, §18.7).

    "Reached this week" means an objective that was not mastered in the last
    snapshot before this week and is mastered in the last snapshot inside it. That
    is a crossing we recorded on two specific days, which is the only version of
    this claim that is checkable — "it is mastered now and they played this week"
    would report the same milestone every week forever.

    The baseline has to be looked up **per objective** for that sentence to be
    true; see `_baseline_index` for why reading a single earlier report instead
    turns this function into exactly the every-week claim it exists to avoid.

    None is the ordinary answer. Milestones are months apart at this age, and a
    weekly digest that produced one every week would be describing something else.
    """
    crossed = [
        code
        for code, seen in touched.items()
        if str(seen["last"].get("status")) == skills.MASTERED
        and str((before.get(code) or {}).get("status") or skills.NOT_STARTED) != skills.MASTERED
    ]
    if not crossed:
        return None

    objectives = {
        objective.code: objective
        for objective in Objective.objects.filter(code__in=crossed).select_related("milestone")
    }
    # The furthest one, if a week somehow crossed two: a parent hears about
    # counting to five, not about counting to three on the way past it.
    prerequisites = {
        code: list(prereqs)
        for code, prereqs in Objective.objects.values_list("code", "prerequisites")
    }
    code = max(crossed, key=lambda item: (skills.chain_depth(item, prerequisites), item))
    objective = objectives.get(code)
    if objective is None:
        return None
    return {
        "name": objective.milestone.name,
        "step": objective.description.rstrip(".").lower(),
        "reached": True,
    }


# ---------------------------------------------------------------------------
# What they love, and what they loved (§11.7, §12.3, §12.5)
# ---------------------------------------------------------------------------
def interest_trend(child: Child, *, now: datetime.datetime | None = None) -> dict[str, Any]:
    """§11.7's "this month: space, then dinosaurs, now ocean", built honestly.

    `now` comes from the live graph, which is the one thing that is always true.
    `earlier` and `story` come from the interest snapshots stored on past session
    reports, because the graph itself cannot answer them: `InterestEdge` holds a
    weight and the date it was last reinforced, so it knows what a child loves
    today and has no memory of March (see `SessionReport.interests`).

    The sentence names the *leaders* — the top topic in each snapshot, with
    consecutive repeats collapsed — so it describes changes of heart rather than
    the fortnight of frogs between two of them. With fewer than two distinct
    leaders there is no trend, and the sentence is empty rather than a restatement
    of `now` dressed as history.
    """
    when = now or timezone.now()
    live = [topic.name for topic in interest.top_interests(child, limit=TOP_INTERESTS)]

    snapshots = list(
        SessionReport.objects.filter(
            child=child, occurred_at__gte=when - datetime.timedelta(days=TREND_WINDOW_DAYS)
        )
        .exclude(interests=[])
        .order_by("occurred_at")
        .values_list("interests", flat=True)
    )

    leaders: list[str] = []
    for snapshot in snapshots:
        top = next((str(item.get("topic")) for item in snapshot if item.get("topic")), "")
        if top and (not leaders or leaders[-1] != top):
            leaders.append(top)

    earlier = [
        str(item.get("topic")) for item in (snapshots[0] if snapshots else []) if item.get("topic")
    ]

    story = ""
    if len(leaders) >= 2:
        phases = leaders[-TREND_PHASES:]
        story = f"Over the last few weeks: {', then '.join(phases[:-1])}, and now {phases[-1]}."

    return {"now": live, "earlier": earlier[:TOP_INTERESTS], "story": story}


def _recommendation(child: Child, live: dict[str, skills.SkillView]) -> str:
    """One low-pressure nudge, grounded in this child's own data (§12.4).

    Written here rather than generated, for the same reason most of this file is:
    a recommendation is a suggestion about somebody's child, and a model asked for
    one will produce a confident suggestion whether or not there is anything to
    suggest. Both branches below name something real — the top of the interest
    graph, or the objective the skill model says is at their edge — and both end in
    a question, because §12.4 is explicit that the parent stays in control and the
    product advises.
    """
    name = child.first_name
    top = interest.top_interests(child, limit=1)
    practising = [view for view in live.values() if view.status == skills.PRACTISING]

    if top:
        topic = top[0].name
        return (
            f"{name} is deep in {topic} at the moment. Want the next few stories to "
            f"stay there, or shall we widen it out?"
        )
    if practising:
        view = min(practising, key=lambda item: item.depth)
        return (
            f"{name} is in the middle of {view.milestone.lower()}. Want a week that "
            "leans into it, or would you rather we kept mixing things up?"
        )
    return (
        f"Nothing needs changing — there is not enough of {name}'s play here yet for "
        "us to suggest anything, and that is fine."
    )
