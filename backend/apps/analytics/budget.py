"""The global inference-budget circuit breaker (PRODUCT.md §18.8, §19.9, §22.4).

§19.9 lists it among the cost levers and §18.8 puts it next to the rate limits:
*"per-parent and per-child request limits **+ a global inference-budget circuit
breaker** (cost protection)"*. Rate limits bound what one caller can do; this
bounds what the whole product can spend in a window, which is the failure the
limits cannot catch — a hundred families each behaving perfectly, on the morning
a vendor's price changed or a prompt regression tripled the token count.

**How it works.** Every span `analytics.cost` records also adds its dollars to a
rolling counter in Redis. Before any *new* paid generation, `check()` compares
that counter against `INFERENCE_BUDGET_USD_PER_WINDOW`. Over the cap, it raises,
and the raise is the point:

    ai.router.generate_structured  -> BudgetExceeded (an AIError)
                                   -> orchestrator's existing AIError handler
                                   -> the pre-vetted safe fallback beat (§13.3)

    media.service._get_or_generate -> BudgetExceeded (a MediaError)
                                   -> orchestrator's existing MediaError handler
                                   -> stock art / no picture, beat still playable

So tripping the breaker does not add a new degraded mode to the product. It
re-uses the one that already exists, is already tested, and already ends with a
warm illustrated beat rather than an error — which is the whole reason the
breaker raises instead of returning a sentinel nobody would remember to check.

**Three properties, each a decision.**

*The cache is never refused.* `check()` is called at the one line in each seam
that actually pays a provider, after the cache lookups. A tripped breaker
therefore still serves every picture and every voice line we already hold — which
is most of them, since the companion's reference set and the fallback beats' art
are shared across children (§16.6). "Degrade to cached content" (§18.8) is not a
mode the code enters; it is what refusing new spend leaves behind.

*It fails open.* No Redis, no counter, no breaker: `check()` returns and the
generation proceeds. A cost control that takes the product down when its own
storage blinks has converted a spending problem into an outage, and the spending
problem is the smaller one. Every open-failure is logged, and `state()` reports
`available=False` so the dashboard says "not enforcing" rather than "under cap".

*The window is a fixed bucket, not a sliding one.* Spend is keyed by the window
the clock is in, and the key expires two windows later. A sliding window would
need the individual spans; the aggregate is what a breaker needs, and a bucket
that resets on the hour is a bound a human can reason about at 3am.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

#: Redis key for the current window's spend.
KEY_PREFIX = "primer:budget:"

#: Logged (and counted) once per process per window when the breaker first trips,
#: so a tripped breaker is one alertable line rather than one per refused
#: generation — the log during a trip would otherwise be the size of the traffic.
_ANNOUNCED: set[str] = set()


class BudgetExceeded(Exception):
    """The window's inference budget is spent.

    Deliberately **not** raised as itself anywhere a caller can see. The two
    seams that spend money each re-raise it as the error type their own callers
    already handle — `ai.router.BudgetExceededError` (an `AIError`) and
    `media.exceptions.MediaBudgetExceeded` (a `MediaError`) — so the degradation
    path is the one that has been tested since Day 7 rather than a new one added
    on launch day.
    """


@dataclass(frozen=True)
class State:
    """What the breaker knows right now — for the dashboard and the preflight."""

    #: Is the counter readable? `False` means the breaker is not enforcing.
    available: bool
    spent_usd: Decimal
    cap_usd: Decimal
    window_s: int
    #: Seconds until this bucket resets.
    resets_in_s: int

    @property
    def tripped(self) -> bool:
        return self.available and self.cap_usd > 0 and self.spent_usd >= self.cap_usd

    @property
    def fraction(self) -> float:
        """Spend as a fraction of the cap; `0.0` when there is no cap."""
        return float(self.spent_usd / self.cap_usd) if self.cap_usd > 0 else 0.0


def _cap() -> Decimal:
    """The cap, or zero meaning "no cap configured — do not enforce"."""
    try:
        return Decimal(str(settings.INFERENCE_BUDGET_USD_PER_WINDOW))
    except Exception:  # pragma: no cover - a non-numeric setting
        logger.exception("budget.bad_cap value=%r", settings.INFERENCE_BUDGET_USD_PER_WINDOW)
        return Decimal(0)


def _window_s() -> int:
    return max(1, int(settings.INFERENCE_BUDGET_WINDOW_S))


def window_key(now: float | None = None) -> str:
    """The key for the bucket the clock is currently in."""
    window = _window_s()
    moment = time.time() if now is None else now
    return f"{KEY_PREFIX}{window}:{int(moment // window)}"


def _client() -> Any:
    """A Redis client, or `None`.

    Built per call for the same reason `memory.stm._client` is: a module-level
    client freezes `REDIS_URL` at import time, which makes a settings override in
    a test do nothing. Timeouts are short because this sits on the generation
    path — a slow Redis must not become latency a child feels.
    """
    try:
        import redis

        return redis.Redis.from_url(
            settings.REDIS_URL,
            socket_timeout=0.5,
            socket_connect_timeout=0.5,
            decode_responses=True,
        )
    except Exception as exc:  # pragma: no cover - import/URL errors only
        logger.warning("budget.client_unavailable error=%s", exc)
        return None


def spend(amount: Decimal | float | int) -> None:
    """Add one generation's dollars to the window. Never raises.

    Called by `cost._record` for every span, so the counter follows the same rule
    the cost table does: what we count is what we actually spent, including the
    spans that arrived from a failed generation.

    Stored in **micro-dollars as an integer**, because `INCRBYFLOAT` on a
    counter this hot accumulates binary-float drift, and a breaker whose reading
    disagrees with the table it is supposed to be protecting is a breaker nobody
    trusts. Sub-micro-dollar spans (a hundred characters of TTS) round to zero and
    are the honest loss in that trade.
    """
    micros = int(Decimal(str(amount)) * 1_000_000)
    if micros <= 0:
        return

    client = _client()
    if client is None:
        return

    key = window_key()
    try:
        pipeline = client.pipeline()
        pipeline.incrby(key, micros)
        # Two windows, so the *previous* bucket is still readable for a moment
        # after a roll-over — a reporting convenience, and it costs one key.
        pipeline.expire(key, _window_s() * 2)
        pipeline.execute()
    except Exception as exc:
        # Losing a count understates spend, which is the direction that lets the
        # breaker trip late. It is still better than raising on the path that has
        # just successfully generated something for a child.
        logger.warning("budget.spend_failed error=%s", exc)


def state() -> State:
    """Read the counter. Never raises."""
    cap = _cap()
    window = _window_s()
    client = _client()
    resets_in = window - int(time.time() % window)

    if client is None:
        return State(False, Decimal(0), cap, window, resets_in)

    try:
        raw = client.get(window_key())
    except Exception as exc:
        logger.warning("budget.read_failed error=%s", exc)
        return State(False, Decimal(0), cap, window, resets_in)

    micros = int(raw or 0)
    return State(True, Decimal(micros) / 1_000_000, cap, window, resets_in)


def check(*, operation: str) -> None:
    """Raise `BudgetExceeded` if this window's budget is gone.

    `operation` is only for the log line; the breaker is global by design — a cap
    that let the expensive generation through because it was categorised
    differently would not be a cap.
    """
    current = state()
    if not current.tripped:
        return

    key = window_key()
    if key not in _ANNOUNCED:
        _ANNOUNCED.add(key)
        # ERROR, not WARNING: this is the line an alert fires on. Everything the
        # product does from here is safe but degraded, and somebody has to know.
        logger.error(
            "budget.tripped spent_usd=%s cap_usd=%s window_s=%d resets_in_s=%d "
            "first_refused_operation=%s",
            current.spent_usd,
            current.cap_usd,
            current.window_s,
            current.resets_in_s,
            operation,
        )
        _record_trip(current)

    raise BudgetExceeded(
        f"Inference budget for this window is spent: ${current.spent_usd} of "
        f"${current.cap_usd} over {current.window_s}s. Resets in "
        f"{current.resets_in_s}s. Refused: {operation}."
    )


def _record_trip(current: State) -> None:
    """Put the trip on the ops timeline as well as in the log.

    A `safety` incident rather than an analytics event, and the reasoning is
    §13.11: this is the queue a person actually works, and a breaker trip means
    children are being served fallback content right now. It is `degraded`
    because that is precisely what it is — a guard on spend has fired and the
    experience is running on cached and pre-vetted material.

    Wrapped, like everything else here: an incident row that failed to write must
    not become an exception on a generation path.
    """
    try:
        from apps.safety.incidents import record
        from apps.safety.models import Action, Severity

        record(
            category="inference_budget",
            action=Action.DEGRADED,
            severity=Severity.HIGH,
            detail={
                "spent_usd": str(current.spent_usd),
                "cap_usd": str(current.cap_usd),
                "window_s": current.window_s,
            },
        )
    except Exception:
        logger.exception("budget.trip_not_recorded")


def reset() -> None:
    """Clear the current window. For the drill and for tests, not for production
    traffic — a breaker somebody can zero is a breaker that stops being a bound."""
    _ANNOUNCED.clear()
    client = _client()
    if client is None:
        return
    try:
        client.delete(window_key())
    except Exception as exc:  # pragma: no cover
        logger.warning("budget.reset_failed error=%s", exc)
