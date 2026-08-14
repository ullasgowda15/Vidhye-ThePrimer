"""Per-generation cost tracing (PRODUCT.md §19.9, §22.4).

§19.9 asks for one thing: *every generation is traced with its cost so unit
economics are always visible*. This module is the tracer. Two halves:

**`trace(...)` — the span context.** A `ContextVar` holding "which child, which
sitting am I generating for". It is a context rather than an argument because the
places money is spent — `ai.router.ModelRouter.generate_structured`,
`media.service._get_or_generate` and `media.adapters.embedding.embed` — sit
several layers below any code that knows what a session is, and threading a
session id down through the prompt assembler and the media cache would put an
analytics concern into six signatures that have nothing to do with analytics. The
same reason a tracing SDK (Langfuse, Helicone) uses one.

**`record(...)` — the span.** Called by those three seams and by nobody else, so a
new generation is instrumented the moment it goes through the router, the media
service or the embedding adapter, and a caller cannot forget.

*Three, because there were two and something was missing.* Embeddings went
through neither of the first two, so every consolidation run and every
branch-point recall was real money `cost_per_session` did not count (the Vertex
review's P2-5). The rule that matters is not the number of seams — it is that a
seam is a place a *vendor is called*, never a place a caller chooses to report.

Three properties worth stating, because each of them is a decision:

*Recording never fails a generation.* Same rule as `events.track`, for the same
reason (§13): a child is on the other end of most of these. Everything is caught,
logged and dropped.

*A span with no context is still written.* With a null `session_key`, and the
aggregates count it separately. It is real money; hiding it would understate
cost/session, and guessing a session for it would be worse than either.

*Cache hits are not spans.* The second identical picture costs nothing, so there
is nothing to record. Hit-rate lives in `media.service.STATS`, which is a
different question.

**Celery is where the context does not follow.** A `ContextVar` is per-thread,
per-task; the media generated for a beat runs in a worker, off the request that
scheduled it. So the worker re-enters the context from the row it was handed
(`story.tasks.generate_beat_media`). That is the seam to check first when
unattributed spend appears in the dashboard.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db.models import Count, Sum
from django.utils import timezone

from apps.analytics import budget, pricing
from apps.analytics.models import CostKind, GenerationCost

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterator

    from apps.profiles.models import Child
    from apps.story.models import PlaySession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Attribution:
    """Who a span of spend belongs to. Ids only — this is never a place to hold a
    model instance, because a context outlives the request that opened it in
    exactly the cases (a Celery task) where a stale instance would be worst."""

    child_id: Any = None
    session_id: Any = None


_CONTEXT: ContextVar[Attribution | None] = ContextVar("primer_cost_attribution", default=None)


@contextmanager
def trace(
    *,
    child: Child | None = None,
    session: PlaySession | None = None,
) -> Iterator[None]:
    """Attribute every span raised inside this block to a child and a sitting.

    Nests: an inner `trace` replaces the outer one for its duration and the token
    restores it, so a beat generated inside an adventure-level trace does not
    leak its own narrower context to whatever the caller does next.

    A `trace` with neither argument is legal and useful — it is how a background
    job that genuinely belongs to nobody makes that explicit rather than
    inheriting whatever context happened to be current on that thread.
    """
    token = _CONTEXT.set(
        Attribution(
            child_id=getattr(child, "pk", None),
            session_id=getattr(session, "pk", None),
        )
    )
    try:
        yield
    finally:
        _CONTEXT.reset(token)


@contextmanager
def trace_ids(*, child_id: Any = None, session_id: Any = None) -> Iterator[None]:
    """`trace`, for a caller that has ids and not rows.

    Celery tasks are handed a `session_id` string. Making them fetch the row just
    to open a trace would be a query per task in aid of nothing.
    """
    token = _CONTEXT.set(Attribution(child_id=child_id, session_id=session_id))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def current() -> Attribution:
    """The attribution in force, or an empty one. Never `None`, so callers do not
    each write the same `or Attribution()`."""
    return _CONTEXT.get() or Attribution()


def record(
    *,
    kind: str,
    operation: str,
    provider: str,
    model: str = "",
    units: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    occurred_at: datetime.datetime | None = None,
) -> GenerationCost | None:
    """Write one generation span. Returns the row, or `None` if it could not be
    written.

    `provider`/`model` are the two names `ai.router.model_label` keeps apart, and
    the price key is built from them the same way, so a `GenerationCost` row and
    the `Beat.model` column of the beat it paid for name the same thing.

    As with `events.track`, **no caller may branch on the return value.**
    """
    try:
        return _record(
            kind=kind,
            operation=operation,
            provider=provider,
            model=model,
            units=units or {},
            latency_ms=latency_ms,
            occurred_at=occurred_at or timezone.now(),
        )
    except Exception:
        logger.exception("analytics.cost_failed kind=%s operation=%s", kind, operation)
        return None


def _record(
    *,
    kind: str,
    operation: str,
    provider: str,
    model: str,
    units: dict[str, Any],
    latency_ms: int | None,
    occurred_at: datetime.datetime,
) -> GenerationCost:
    label = f"{provider}:{model}" if model else provider
    amount = price(kind, label, units)

    attribution = current()
    row = GenerationCost.objects.create(
        child_id=attribution.child_id,
        session_id=attribution.session_id,
        session_key=attribution.session_id,
        kind=kind,
        operation=operation,
        provider=provider,
        model=model,
        units=units,
        cost_usd=amount if amount is not None else Decimal(0),
        priced=amount is not None,
        latency_ms=latency_ms,
        occurred_at=occurred_at,
    )
    logger.info(
        "cost.span kind=%s operation=%s label=%s units=%s usd=%s priced=%s session=%s",
        kind,
        operation,
        label,
        units,
        row.cost_usd,
        row.priced,
        attribution.session_id,
    )
    # The breaker's counter is fed from here rather than from the two spending
    # seams, so it counts exactly what the cost table counts — including the spans
    # a *failed* generation leaves behind (§22.4's expensive failures). A second
    # place that decided what to count would eventually disagree with this one,
    # and a breaker that disagrees with the table it protects is one nobody acts
    # on. Never raises; see `budget.spend`.
    budget.spend(row.cost_usd)
    return row


def price(kind: str, label: str, units: dict[str, Any]) -> Decimal | None:
    """Dollars for one span, or `None` when the route is not in the price table.

    Split out from `_record` so the pricing arithmetic can be tested without a
    database, and so the internal dashboard can re-price a span it is showing.
    """
    if kind == CostKind.LLM:
        return pricing.llm_cost(
            label,
            input_tokens=int(units.get("input_tokens") or 0),
            output_tokens=int(units.get("output_tokens") or 0),
        )
    if kind == CostKind.IMAGE:
        return pricing.image_cost(label, images=int(units.get("images") or 1))
    if kind == CostKind.TTS:
        return pricing.tts_cost(label, characters=int(units.get("characters") or 0))
    return None


# ---------------------------------------------------------------------------
# Aggregates — the numbers §22.4 asks to keep visible
# ---------------------------------------------------------------------------


def cost_per_session(*, since: datetime.datetime, until: datetime.datetime) -> dict[str, Any]:
    """`sum(generation_costs) / sessions` over a window (§22.4, the brief's §5).

    The denominator is **distinct attributed sittings inside the window**, not
    every `PlaySession` row: a session that generated nothing (a child who opened
    the app and put it down) has no spend to divide, and counting it would make
    cost/session fall for a reason that is not a saving. The two numbers are both
    reported — `sessions` and `unattributed_usd` — so anyone reading the ratio can
    see what went into it.

    `unattributed_usd` is the honesty line. It is real spend that could not be tied
    to a sitting, and if it grows the ratio is understating what a session costs.
    """
    spans = GenerationCost.objects.filter(occurred_at__gte=since, occurred_at__lt=until)

    totals = spans.aggregate(
        total=Sum("cost_usd"),
        spans=Count("id"),
        sessions=Count("session_key", distinct=True),
    )
    unattributed = spans.filter(session_key__isnull=True).aggregate(total=Sum("cost_usd"))
    unpriced = spans.filter(priced=False).count()

    total = totals["total"] or Decimal(0)
    sessions = totals["sessions"] or 0

    return {
        "since": since,
        "until": until,
        "total_usd": total,
        "sessions": sessions,
        # `None` rather than 0.0 when there is nothing to divide by. A cost/session
        # of zero is a claim about a cheap week; "no sessions" is the absence of
        # one, and the dashboard renders them differently.
        "cost_per_session_usd": (total / Decimal(sessions)) if sessions else None,
        "spans": totals["spans"] or 0,
        "unpriced_spans": unpriced,
        "unattributed_usd": unattributed["total"] or Decimal(0),
        "by_kind": _by_kind(spans),
        "by_operation": _by_operation(spans),
    }


def _by_kind(spans: Any) -> list[dict[str, Any]]:
    """LLM vs image vs voice. The split §22.4's levers act on separately — a media
    cache that has quietly stopped working is invisible in a single total."""
    rows = spans.values("kind").annotate(total=Sum("cost_usd"), spans=Count("id")).order_by("kind")
    return [
        {"kind": row["kind"], "total_usd": row["total"] or Decimal(0), "spans": row["spans"]}
        for row in rows
    ]


def _by_operation(spans: Any) -> list[dict[str, Any]]:
    """Which generation is expensive — the view that answers "should the safety
    classifier be on a smaller model" without a spreadsheet."""
    rows = (
        spans.values("operation", "kind")
        .annotate(total=Sum("cost_usd"), spans=Count("id"))
        .order_by("-total")
    )
    return [
        {
            "operation": row["operation"],
            "kind": row["kind"],
            "total_usd": row["total"] or Decimal(0),
            "spans": row["spans"],
        }
        for row in rows
    ]
