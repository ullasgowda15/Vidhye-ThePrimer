"""The internal metrics dashboard (PRODUCT.md §5.6, §22, the brief's §5).

One page, at `/admin/metrics`, showing §22.3's five numbers, §22.1's North Star
and §22.4's cost/session. Staff only.

**This is the ops side of §5.6's hard boundary, and the boundary is what this
module is for.** Everything a parent sees is served by `apps.analytics.views`
under `/api/v1/children/{id}/...`, owner-scoped and curated. Everything here is
aggregate product telemetry and per-generation spend, and it is reachable only
by a staff account. The two never meet:

* nothing in this module is mounted under `/api/v1/`;
* nothing under `/api/v1/` imports `metrics`, `cost` or the `Event` /
  `GenerationCost` models — asserted by
  `tests/test_analytics_pipeline.py::TestTheSeparationIsStructural`;
* every number on this page is an aggregate across families. There is no
  per-child view here, deliberately: a staff page that could be pointed at one
  named child is a raw behavioural record with a nicer font (§13.8), and the
  parent's own dashboard is where one child is legitimately readable.

**Why a Django view and not a DRF endpoint.** A DRF route would live in the same
URL space as the parent API and be one router registration away from being
serialised into it. A `staff_member_required` template view mounted above
`admin/` cannot be reached by a client that has an access token and no staff
flag, and it costs no schema surface — `make schema` and the generated TS client
stay exactly as they were, which is the property that says the parent app cannot
call this even by accident.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone

from apps.analytics import cost, metrics
from apps.analytics.models import Event, GenerationCost

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

#: The windows the page offers. Seven days is the default because §22.1's North
#: Star is a *weekly* number and a shorter window cannot contain one.
WINDOWS = (7, 14, 30, 90)
DEFAULT_DAYS = 7


def _days_from(request: HttpRequest) -> int:
    """`?days=` if it is one of the offered windows, else the default.

    Clamped to the list rather than to a range: an arbitrary `?days=100000` is a
    full-table scan behind a login, and the four windows are the four questions
    anyone actually asks.
    """
    try:
        requested = int(request.GET.get("days", DEFAULT_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return requested if requested in WINDOWS else DEFAULT_DAYS


@staff_member_required
def metrics_dashboard(request: HttpRequest) -> HttpResponse:
    """`/admin/metrics` — §22's numbers for one window.

    `staff_member_required` rather than `user_passes_test`, so an unauthenticated
    visitor is redirected to the admin login they already have rather than shown a
    403 with no way forward, and so this page's idea of "staff" is the same one
    `/admin/` uses.
    """
    days = _days_from(request)
    window = metrics.Window.last(days)

    snapshot = metrics.snapshot(window)
    spend = cost.cost_per_session(since=window.since, until=window.until)

    return render(
        request,
        "analytics/metrics.html",
        {
            "days": days,
            "windows": WINDOWS,
            "window": window,
            "snapshot": snapshot,
            "targets": TARGETS,
            # The cohort table's column headings. Built here rather than in the
            # template because Django's template language cannot count, and a
            # hard-coded `W1 W2 W3 W4` would silently stop matching the cells the
            # day `COHORT_WEEKS` changes.
            "cohort_offsets": range(1, metrics.COHORT_WEEKS + 1),
            "spend": spend,
            "cost_rows": _recent_spans(),
            "silent": sorted(Event.NAMES - set(metrics.names_emitted())),
            "generated_at": timezone.now(),
        },
    )


#: §22.3's early targets, so a number is read against the thing it is supposed to
#: be rather than against a feeling. Directional and to be calibrated on real
#: data, exactly as §22.3 says — the page prints that caveat.
TARGETS: dict[str, Any] = {
    "activation": 70.0,
    "day2_return": 40.0,
    "w1_retention": 55.0,
    "completion_rate": 80.0,
    "per_child": float(metrics.DWA_ADVENTURES),
}


def _recent_spans(limit: int = 20) -> list[dict[str, Any]]:
    """The last few generation spans, as a sanity check on the aggregate above.

    Here because an average is the easiest number in the product to be quietly
    wrong about, and one look at twenty real rows — a beat, its repair, an
    illustration, a voice line, each with its units and its price — is how you
    catch a units bug that a total would hide.

    No child, no session id, no prompt: what was generated, by whom, how much of
    it, and what it cost (§13.8).
    """
    rows = GenerationCost.objects.order_by("-occurred_at")[:limit]
    return [
        {
            "occurred_at": row.occurred_at,
            "kind": row.kind,
            "operation": row.operation,
            "label": f"{row.provider}:{row.model}" if row.model else row.provider,
            "units": row.units,
            "cost_usd": row.cost_usd,
            "priced": row.priced,
            "latency_ms": row.latency_ms,
            "attributed": row.session_key is not None,
        }
        for row in rows
    ]


def as_text(window_days: int = DEFAULT_DAYS, *, now: datetime.datetime | None = None) -> str:
    """The same page as plain text, for `manage.py metrics`.

    Shares `metrics.snapshot` and `cost.cost_per_session` with the HTML view — the
    numbers are computed once, in one module, and rendered twice. A terminal
    report with its own arithmetic is a second dashboard that will disagree with
    the first.
    """
    window = metrics.Window.last(window_days, now=now)
    snapshot = metrics.snapshot(window)
    spend = cost.cost_per_session(since=window.since, until=window.until)

    lines = [
        f"The Primer — metrics for the last {window_days} days",
        f"  {window.since:%Y-%m-%d %H:%M} .. {window.until:%Y-%m-%d %H:%M}",
        "",
        "Funnel and engagement (PRODUCT.md 22.3)",
        _rate_line("Activation", snapshot["activation"], TARGETS["activation"]),
        _rate_line("Day-2 return", snapshot["day2_return"], TARGETS["day2_return"]),
        _rate_line("W1 retention", snapshot["w1_retention"], TARGETS["w1_retention"]),
        _rate_line("Completion rate", snapshot["completion_rate"], TARGETS["completion_rate"]),
        "",
        "Onboarding funnel - where the signup cohort stopped",
    ]
    lines += _funnel_lines(snapshot["funnel"])

    active = snapshot["active"]
    stickiness = f"{active['stickiness'] * 100:.0f}%" if active["stickiness"] is not None else "-"
    lines += [
        "",
        "Children still playing (started an adventure)",
        f"  DAU (last day)          {active['dau']}",
        f"  avg DAU over {active['days_in_window']:>3} days   {active['avg_dau']:.2f}"
        f"   ({active['days_with_play']} of those days had any play)",
        f"  WAU (last {active['wau_days']} days)       {active['wau']}",
        f"  MAU (last {active['mau_days']} days)      {active['mau']}",
        f"  stickiness              {stickiness}   (avg DAU / WAU)",
        "",
        "Retention by weekly cohort (W1-W4 after their first story)",
    ]
    lines += _cohort_lines(snapshot["cohorts"])

    lines += ["", "Adventures per child per week (22.1: DWA = children with 3+)"]

    if not snapshot["weeks"]:
        lines.append("  no completed adventures in this window")
    for week in snapshot["weeks"]:
        # `per_child` is None only when the week has no active children, which
        # `weekly_adventures` never emits a row for — but it is typed optional, so
        # it is handled rather than assumed.
        per_child = f"{week['per_child']:.2f}" if week["per_child"] is not None else "-"
        children = week["active_children"]
        lines.append(
            f"  week of {week['week_start']}: "
            f"{week['adventures']} adventures / {children} "
            f"{'child' if children == 1 else 'children'}"
            f" = {per_child} per child   DWA {week['dwa']}"
        )

    lines += ["", "Worlds children picked (started, and whether they finished)"]
    if not snapshot["worlds"]:
        lines.append("  no adventures started in this window")
    for row in snapshot["worlds"]:
        finished = f"{row['completion'].pct:.0f}%" if row["completion"].pct is not None else "-"
        lines.append(
            f"  {(row['world'] or '(unrecorded)'):<22} "
            f"{row['started']:>4} started  {row['children']:>3} children  "
            f"finished {finished:>5} ({row['completed']}/{row['started']})"
        )

    lines += ["", "Unit economics (22.4)"]
    per_session = spend["cost_per_session_usd"]
    lines.append(
        f"  cost/session   ${per_session:.4f}"
        if per_session is not None
        else "  cost/session   no attributed sessions in this window"
    )
    lines.append(f"  total spend    ${spend['total_usd']:.4f} over {spend['spans']} generations")
    lines.append(f"  unattributed   ${spend['unattributed_usd']:.4f}")
    lines.append(f"  unpriced spans {spend['unpriced_spans']}")
    for row in spend["by_kind"]:
        lines.append(f"    {row['kind']:<6} ${row['total_usd']:.4f}  ({row['spans']} spans)")

    guardrails = snapshot["guardrails"]
    length = guardrails["session_minutes"]
    lines += [
        "",
        "Wellbeing guardrails (22.5) — watched, never maximised",
        f"  sittings                {guardrails['sessions']}",
        f"  ended reasons           {guardrails['ended_reasons'] or '-'}",
        (
            f"  sitting length          median {length['median_min']}min  "
            f"p90 {length['p90_min']}min  "
            f"over {length['over_long_threshold_min']}min: {length['over_long']}"
            f"  (measured {length['measured']})"
            if length["measured"]
            else "  sitting length          no durations recorded in this window"
        ),
        f"  max sittings/child/day  {guardrails['max_sessions_per_child_day']}",
        f"  safety incidents        {guardrails['safety_incidents']}"
        f" ({guardrails['serious_safety_incidents']} serious)",
        "",
        # §18.8's breaker, on the page it protects. It belongs next to the cost
        # numbers above rather than in a log, because "cost/session looks great
        # this week" and "the breaker has been open since Tuesday and children
        # have been getting cached art" are the same figure with opposite
        # meanings, and only one of these lines can tell them apart.
        "Inference budget breaker (18.8)",
        _breaker_line(),
        "",
        "Events seen",
    ]
    for name, count in snapshot["events"]:
        lines.append(f"  {name:<28} {count}")
    silent = sorted(Event.NAMES - set(metrics.names_emitted()))
    if silent:
        lines.append(f"  never emitted: {', '.join(silent)}")

    return "\n".join(lines)


def _breaker_line() -> str:
    """The breaker's state, in one line, distinguishing all three of them.

    "Under cap", "tripped" and **"not enforcing"** are three different situations
    and the third is the one worth a word: a Redis the breaker cannot read means
    there is no cap in force at all, and a dashboard that rendered that as
    `$0.00 of $50.00` would be reporting the safest-looking number precisely when
    nothing is being protected.
    """
    from apps.analytics import budget

    state = budget.state()
    if not state.available:
        return "  NOT ENFORCING - the spend counter is unreadable (Redis). No cap is in force."
    if not state.cap_usd:
        return "  no cap set (INFERENCE_BUDGET_USD_PER_WINDOW=0). Not enforcing."

    label = "TRIPPED" if state.tripped else "ok"
    return (
        f"  {label:<8} ${state.spent_usd:.4f} of ${state.cap_usd:.2f} "
        f"({state.fraction * 100:.0f}%) this {state.window_s}s window, "
        f"resets in {state.resets_in_s}s"
    )


def _funnel_lines(rows: list[dict[str, Any]]) -> list[str]:
    """The funnel, one line per step, with the step-to-step conversion.

    Both percentages, always. The share of the top is what says how big the
    problem is; the share of the *previous* step is what says whose problem it is,
    and a funnel printed only against its top makes every step after a bad one
    look bad too. `lost_here` is printed as a count of families rather than as a
    percentage, because that is the number you go and talk to.
    """
    if not rows or not rows[0]["reached"]:
        return ["  no signups in this window"]

    lines = []
    for row in rows:
        of_top = f"{row['pct_of_top']:.0f}%" if row["pct_of_top"] is not None else "-"
        step = f"{row['pct_of_previous']:.0f}%" if row["pct_of_previous"] is not None else "-"
        lost = f"  lost {row['lost_here']}" if row["lost_here"] else ""
        lines.append(
            f"  {row['label']:<24} {row['reached']:>4}   "
            f"{of_top:>5} of top   {step:>5} of previous{lost}"
        )
    return lines


def _cohort_lines(table: list[dict[str, Any]]) -> list[str]:
    """The retention triangle, in text.

    An unfinished week prints `.` and not `0%` — the cell has not happened yet,
    and a terminal is no more entitled to draw a cliff out of a calendar than the
    HTML page is.
    """
    if not table:
        return ["  no child has finished a first story in this window"]

    columns = "  ".join(f"W{offset:<4}" for offset in range(1, metrics.COHORT_WEEKS + 1))
    lines = ["  cohort        n  " + columns]
    for row in table:
        cells = "  ".join(
            f"{cell['pct']:>4.0f}%" if cell["pct"] is not None else "    ." for cell in row["cells"]
        )
        lines.append(f"  {row['week_start']}  {row['size']:>3}  {cells}")
    return lines


def _rate_line(label: str, rate: metrics.Rate, target: float) -> str:
    """One funnel line: the fraction, the percentage, and the §22.3 target.

    Always all three. The fraction is what stops `100%` over two signups from
    being read as a result, and the target is what stops any of them from being
    read without a yardstick.
    """
    if rate.pct is None:
        return f"  {label:<16} -           ({rate.numerator}/{rate.denominator})  target {target}%"
    return (
        f"  {label:<16} {rate.pct:>5.1f}%     "
        f"({rate.numerator}/{rate.denominator})  target {target}%"
    )
