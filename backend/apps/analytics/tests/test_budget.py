"""The global inference-budget circuit breaker (PRODUCT.md §18.8, Day 15).

The unit half. `apps/story/tests/test_degradation.py` is the half that matters
most — it takes a real child through a real adventure with the breaker open and
asserts what lands on their screen — but a breaker also has to be *right about
money*, and this is where that is checked: it counts what the cost table counts,
it refuses only new spend, it fails open, and it says so when it is not
enforcing.

Redis is faked in-process for the same reasons `memory.tests.conftest` fakes it:
the interesting behaviour includes what happens when Redis is *missing*, which a
real server cannot demonstrate. One test at the bottom runs against the live
server so the wire protocol is not taken on trust either.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from apps.analytics import budget, cost
from apps.analytics.models import CostKind


class FakeRedis:
    """The four commands `apps.analytics.budget` uses."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttl: dict[str, int] = {}
        self.fail = False

    def pipeline(self) -> FakePipeline:
        self._check()
        return FakePipeline(self)

    def incrby(self, key: str, amount: int) -> int:
        self._check()
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    def expire(self, key: str, seconds: int) -> None:
        self._check()
        self.ttl[key] = seconds

    def get(self, key: str) -> str | None:
        self._check()
        value = self.values.get(key)
        return None if value is None else str(value)

    def delete(self, key: str) -> None:
        self._check()
        self.values.pop(key, None)

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("fake redis is having a bad day")


class FakePipeline:
    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._queued: list[tuple[str, tuple[Any, ...]]] = []

    def incrby(self, *args: Any) -> FakePipeline:
        self._queued.append(("incrby", args))
        return self

    def expire(self, *args: Any) -> FakePipeline:
        self._queued.append(("expire", args))
        return self

    def execute(self) -> list[Any]:
        return [getattr(self._client, name)(*args) for name, args in self._queued]


@pytest.fixture
def redis(monkeypatch: Any, settings: Any) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(budget, "_client", lambda: client)
    # A cap, so the breaker is actually enforcing. `base.py` ships 0.00 = off.
    settings.INFERENCE_BUDGET_USD_PER_WINDOW = 1.00
    settings.INFERENCE_BUDGET_WINDOW_S = 3600
    budget._ANNOUNCED.clear()
    return client


@pytest.fixture
def no_redis(monkeypatch: Any, settings: Any) -> None:
    monkeypatch.setattr(budget, "_client", lambda: None)
    settings.INFERENCE_BUDGET_USD_PER_WINDOW = 1.00
    budget._ANNOUNCED.clear()


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------
class TestTheCounter:
    def test_spend_accumulates_within_a_window(self, redis: FakeRedis) -> None:
        budget.spend(Decimal("0.30"))
        budget.spend(Decimal("0.12"))
        assert budget.state().spent_usd == Decimal("0.42")

    def test_it_counts_in_integers_so_a_thousand_small_spans_do_not_drift(
        self, redis: FakeRedis
    ) -> None:
        """The reason the counter is micro-dollars and not `INCRBYFLOAT`.

        A breaker whose reading disagrees with the cost table it is protecting is
        a breaker nobody acts on. A thousand spans of $0.000123 is exactly
        $0.123000 here, and would not be in binary floating point.
        """
        for _ in range(1000):
            budget.spend(Decimal("0.000123"))
        assert budget.state().spent_usd == Decimal("0.123")

    def test_a_new_window_starts_at_zero(self, redis: FakeRedis, monkeypatch: Any) -> None:
        budget.spend(Decimal("0.90"))
        assert budget.state().tripped is False

        # Same key function, one window later.
        real_key = budget.window_key
        monkeypatch.setattr(budget, "window_key", lambda now=None: real_key(now=10**9))
        assert budget.state().spent_usd == Decimal(0)

    @pytest.mark.django_db
    def test_every_cost_span_feeds_the_counter(self, redis: FakeRedis, settings: Any) -> None:
        """The counter's source is `cost.record` and nothing else.

        A second place that decided what to count would eventually disagree with
        the cost table, and the two numbers are supposed to be the same money.
        """
        settings.AI_PRICE_TABLE = {"llm": {"scripted:test-model": [10.0, 30.0]}}
        cost.record(
            kind=CostKind.LLM,
            operation="beat",
            provider="scripted",
            model="test-model",
            units={"input_tokens": 1_000_000, "output_tokens": 0},
        )
        assert budget.state().spent_usd == Decimal("10.000000")


# ---------------------------------------------------------------------------
# Tripping
# ---------------------------------------------------------------------------
class TestTheBreaker:
    def test_under_the_cap_nothing_is_refused(self, redis: FakeRedis) -> None:
        budget.spend(Decimal("0.99"))
        budget.check(operation="beat")  # must not raise

    def test_at_the_cap_it_trips(self, redis: FakeRedis) -> None:
        budget.spend(Decimal("1.00"))
        with pytest.raises(budget.BudgetExceeded):
            budget.check(operation="beat")

    def test_it_is_global_and_not_per_operation(self, redis: FakeRedis) -> None:
        """A cap that let the expensive generation through because it was
        categorised differently would not be a cap."""
        budget.spend(Decimal("2.00"))
        for operation in ("beat", "plan", "illustration", "narration", "greeting"):
            with pytest.raises(budget.BudgetExceeded):
                budget.check(operation=operation)

    def test_no_cap_configured_means_no_enforcement(self, redis: FakeRedis, settings: Any) -> None:
        settings.INFERENCE_BUDGET_USD_PER_WINDOW = 0.0
        budget.spend(Decimal("500.00"))
        budget.check(operation="beat")  # must not raise
        assert budget.state().tripped is False

    def test_it_fails_open_when_redis_is_gone(self, no_redis: None) -> None:
        """A cost control that takes the product down when its own storage blinks
        has converted a spending problem into an outage, and the spending problem
        is the smaller one."""
        budget.check(operation="beat")  # must not raise
        assert budget.state().available is False

    def test_it_fails_open_when_redis_raises(self, redis: FakeRedis) -> None:
        budget.spend(Decimal("5.00"))
        redis.fail = True
        budget.check(operation="beat")  # must not raise
        state = budget.state()
        assert state.available is False
        assert state.tripped is False

    def test_a_failed_write_never_reaches_the_caller(self, redis: FakeRedis) -> None:
        """`spend` is called from `cost.record`, which is called from a generation
        that has just succeeded for a child. Losing a count is acceptable; raising
        there is not."""
        redis.fail = True
        budget.spend(Decimal("1.00"))  # must not raise

    @pytest.mark.django_db
    def test_a_trip_lands_on_the_incident_queue(self, redis: FakeRedis) -> None:
        """§13.11: the queue a person actually works.

        A tripped breaker means children are being served fallback content right
        now, which is a thing somebody has to know without reading a log.
        """
        from apps.safety.models import Action, SafetyIncident, Severity

        budget.spend(Decimal("1.50"))
        with pytest.raises(budget.BudgetExceeded):
            budget.check(operation="beat")

        incident = SafetyIncident.objects.get(category="inference_budget")
        assert incident.action_taken == Action.DEGRADED
        assert incident.severity == Severity.HIGH
        assert Decimal(incident.detail["cap_usd"]) == Decimal("1.00")
        assert Decimal(incident.detail["spent_usd"]) == Decimal("1.50")
        # No child, no adventure, no text: a breaker trip is a fact about the
        # deployment, and §13.8's rule about what may go in `detail` holds here
        # exactly as it does for a blocked beat.
        assert incident.child_id is None

    @pytest.mark.django_db
    def test_the_trip_is_announced_once_per_window_not_once_per_refusal(
        self, redis: FakeRedis
    ) -> None:
        """Otherwise the log during a trip is the size of the traffic, and the one
        line an alert fires on is buried in the thousand after it."""
        from apps.safety.models import SafetyIncident

        budget.spend(Decimal("9.00"))
        for _ in range(20):
            with pytest.raises(budget.BudgetExceeded):
                budget.check(operation="beat")

        assert SafetyIncident.objects.filter(category="inference_budget").count() == 1


# ---------------------------------------------------------------------------
# What the dashboard is told
# ---------------------------------------------------------------------------
class TestTheReportedState:
    def test_not_enforcing_reads_differently_from_under_cap(self, no_redis: None) -> None:
        """The line `internal._breaker_line` exists to get right: an unreadable
        counter means *no cap is in force*, and rendering that as `$0.00 of
        $50.00` would show the safest-looking number precisely when nothing is
        being protected."""
        from apps.analytics import internal

        assert "NOT ENFORCING" in internal._breaker_line()

    def test_a_tripped_breaker_says_so_on_the_page(self, redis: FakeRedis) -> None:
        from apps.analytics import internal

        budget.spend(Decimal("1.20"))
        line = internal._breaker_line()
        assert "TRIPPED" in line
        assert "$1.2000 of $1.00" in line


# ---------------------------------------------------------------------------
# One test against the real thing
# ---------------------------------------------------------------------------
def test_against_a_live_redis(settings: Any) -> None:
    """The wire protocol, not taken on trust.

    Skipped when Redis is not running, because a developer must be able to run the
    suite without `make up`. CI has the container, so this is not a test that only
    ever skips.
    """
    redis_lib = pytest.importorskip("redis")
    settings.INFERENCE_BUDGET_USD_PER_WINDOW = 1.00
    settings.INFERENCE_BUDGET_WINDOW_S = 3600
    budget._ANNOUNCED.clear()

    client = redis_lib.Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=1, decode_responses=True
    )
    try:
        client.ping()
    except Exception:  # pragma: no cover - Redis genuinely not running
        pytest.skip("Redis is not reachable at REDIS_URL")

    budget.reset()
    try:
        budget.spend(Decimal("0.25"))
        assert budget.state().spent_usd == Decimal("0.25")

        # The key shape a human would `GET` in a terminal, and the TTL that keeps
        # a window from living forever.
        assert client.get(budget.window_key()) == "250000"
        assert 0 < client.ttl(budget.window_key()) <= settings.INFERENCE_BUDGET_WINDOW_S * 2

        budget.spend(Decimal("0.80"))
        with pytest.raises(budget.BudgetExceeded):
            budget.check(operation="beat")
    finally:
        budget.reset()
