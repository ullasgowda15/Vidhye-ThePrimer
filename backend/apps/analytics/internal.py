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
        "Adventures per child per week (22.1: DWA = children with 3+)",
    ]

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
    lines += [
        "",
        "Wellbeing guardrails (22.5) — watched, never maximised",
        f"  sittings                {guardrails['sessions']}",
        f"  ended reasons           {guardrails['ended_reasons'] or '-'}",
        f"  max sittings/child/day  {guardrails['max_sessions_per_child_day']}",
        f"  safety incidents        {guardrails['safety_incidents']}"
        f" ({guardrails['serious_safety_incidents']} serious)",
        "",
        "Events seen",
    ]
    for name, count in snapshot["events"]:
        lines.append(f"  {name:<28} {count}")
    silent = sorted(Event.NAMES - set(metrics.names_emitted()))
    if silent:
        lines.append(f"  never emitted: {', '.join(silent)}")

    return "\n".join(lines)


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
