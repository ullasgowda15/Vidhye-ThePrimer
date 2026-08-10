"""Fixtures for the memory suite.

Two things are provided here rather than repeated per file: an in-process stand-in
for Redis, and a played session to consolidate.

**Why a fake Redis and not the real one.** CI runs a Redis container and so does
local development, so hitting it would work — and would make every test in this
file depend on a service, on its state between tests, and on a network timeout
when it is down. STM is *keyed data with a TTL*; the interesting behaviour is what
`apps.memory.stm` does with it, including what it does when Redis is missing,
which a real Redis cannot demonstrate. `test_stm.py` keeps one test against the
live server, skipped when it is unreachable, so the wire protocol is not taken on
trust either.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.companion.service import create_companion
from apps.profiles.models import Child
from apps.story import orchestrator
from apps.story.models import Adventure, PlaySession

PASSWORD = "storybook-primer-42"


class FakeRedis:
    """The handful of Redis commands `apps.memory.stm` actually uses.

    A list per key plus a recorded TTL, which is enough to assert the three
    properties that matter: events land in order, the list is trimmed, and the key
    is given an expiry. `fail_on` makes it raise like a real client having a bad
    day, which is the path that must never reach a caller.
    """

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.ttl: dict[str, int] = {}
        self.fail_on: set[str] = set()

    # --- the pipeline `stm.record` uses -----------------------------------
    def pipeline(self) -> FakePipeline:
        self._maybe_fail("pipeline")
        return FakePipeline(self)

    def rpush(self, key: str, value: str) -> None:
        self._maybe_fail("rpush")
        self.lists.setdefault(key, []).append(value)

    def ltrim(self, key: str, start: int, end: int) -> None:
        self._maybe_fail("ltrim")
        if key in self.lists:
            # Only the negative-start form is used (`-N, -1`: keep the last N).
            self.lists[key] = self.lists[key][start:] if start < 0 else self.lists[key]

    def expire(self, key: str, seconds: int) -> None:
        self._maybe_fail("expire")
        self.ttl[key] = seconds

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        self._maybe_fail("lrange")
        items = self.lists.get(key, [])
        return items if (start, end) == (0, -1) else items[start : (None if end == -1 else end + 1)]

    def delete(self, key: str) -> None:
        self._maybe_fail("delete")
        self.lists.pop(key, None)
        self.ttl.pop(key, None)

    # --- helpers for tests ------------------------------------------------
    def events(self, key: str) -> list[dict[str, Any]]:
        return [json.loads(item) for item in self.lists.get(key, [])]

    def _maybe_fail(self, command: str) -> None:
        if command in self.fail_on:
            raise ConnectionError(f"fake redis refused {command}")


class FakePipeline:
    """Commands buffered until `execute`, as redis-py's pipeline behaves."""

    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._queued: list[tuple[str, tuple[Any, ...]]] = []

    def rpush(self, *args: Any) -> FakePipeline:
        self._queued.append(("rpush", args))
        return self

    def ltrim(self, *args: Any) -> FakePipeline:
        self._queued.append(("ltrim", args))
        return self

    def expire(self, *args: Any) -> FakePipeline:
        self._queued.append(("expire", args))
        return self

    def execute(self) -> list[Any]:
        return [getattr(self._client, name)(*args) for name, args in self._queued]


@pytest.fixture
def fake_redis(monkeypatch: Any) -> FakeRedis:
    """Point `stm` at an in-process store for the duration of a test."""
    from apps.memory import stm

    client = FakeRedis()
    monkeypatch.setattr(stm, "_client", lambda: client)
    return client


@pytest.fixture
def no_redis(monkeypatch: Any) -> None:
    """No Redis at all — the degradation path (§11.2's "never a lost beat")."""
    from apps.memory import stm

    monkeypatch.setattr(stm, "_client", lambda: None)


@pytest.fixture
def aarav(db: Any) -> Child:
    """A five-year-old with a companion, ready to play."""
    parent = get_user_model().objects.create_user(
        email="memory-parent@example.com", password=PASSWORD
    )
    child = Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
        interests=["frogs", "rivers"],
        fears=["loud noises"],
        intensity_ceiling=1,
    )
    companion = create_companion(
        child=child,
        archetype="dragon",
        name="Pip",
        palette="ember",
        personality_preset="brave_spark",
    )
    companion.visual_descriptor = "a small round friendly dragon with warm ember scales"
    companion.save(update_fields=["visual_descriptor"])
    return child


@pytest.fixture(autouse=True)
def no_media_worker(monkeypatch: Any) -> None:
    """Keep the beat-media task out of the memory suite.

    Same reasoning as `story.tests.test_orchestrator`: these tests are about what a
    session *means*, and queueing illustrations into a broker nobody is draining
    would put unrelated traffic and unrelated latency in the way.
    """
    monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)


@pytest.fixture
def played(
    aarav: Child, fake_ai: Any, fake_redis: FakeRedis, django_capture_on_commit_callbacks: Any
) -> PlaySession:
    """A whole sitting: five beats, a counted answer, two branch choices, closed.

    Played through `orchestrator` rather than assembled with `objects.create`,
    because the thing under test is what a *real* session leaves behind — the STM
    log, the mirror on the session row, the choice rows, the persisted cast — and a
    hand-built fixture would let all four drift away from what the engine actually
    writes.

    `django_capture_on_commit_callbacks(execute=True)` is not a detail: STM capture
    is registered with `transaction.on_commit` so a rolled-back beat is never
    remembered, and pytest-django's test transaction never commits. Without this
    the log would be empty and every assertion about STM would be vacuously true.
    """
    with django_capture_on_commit_callbacks(execute=True):
        adventure = orchestrator.create_adventure(aarav)

        # Beat 1 asks the child to count. They get it right, unaided — a skill
        # milestone, and the highest-scoring `skill_moment` there is.
        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key="counted", value=3, latency_ms=4200)

        # Beats 2 and 3 are branches. Different keys, so the story really diverges
        # and the two events are not near-duplicates of each other.
        second = orchestrator.create_beat(
            adventure, seq=2, prev_choice=orchestrator.turn_from(first)
        )
        orchestrator.record_choice(second, chosen_key="follow_river", latency_ms=6100)

        third = orchestrator.create_beat(
            adventure, seq=3, prev_choice=orchestrator.turn_from(second)
        )
        orchestrator.record_choice(third, chosen_key="ask_dragonflies", latency_ms=1500)

        # Beat 4 is another branch, taken quickly and with help — a wobble, warmly
        # scaffolded (§8.5). Worth *less* than the deliberate ones.
        fourth = orchestrator.create_beat(
            adventure, seq=4, prev_choice=orchestrator.turn_from(third)
        )
        orchestrator.record_choice(
            fourth, chosen_key="follow_river", latency_ms=900, retries=2, assisted=True
        )

        # The warm close asks nothing.
        orchestrator.create_beat(
            adventure, seq=adventure.estimated_beats, prev_choice=orchestrator.turn_from(fourth)
        )

    session = adventure.session
    assert session is not None
    return session


@pytest.fixture
def closed(
    played: PlaySession, monkeypatch: Any, django_capture_on_commit_callbacks: Any
) -> PlaySession:
    """`played`, with the sitting ended — and the enqueue captured, not run.

    The task is stubbed rather than executed so the *endpoint's* contract (it
    returns without consolidating) and consolidation's own behaviour stay separate
    tests. `enqueued` records what would have been sent.
    """
    from apps.story import orchestrator as story_orchestrator

    enqueued: list[str] = []
    monkeypatch.setattr(
        story_orchestrator.consolidate, "delay", lambda session_id: enqueued.append(session_id)
    )

    adventure = Adventure.objects.get(session=played)
    with django_capture_on_commit_callbacks(execute=True):
        story_orchestrator.end_adventure(adventure, reason=PlaySession.EndedReason.COMPLETED)

    played.refresh_from_db()
    played.enqueued = enqueued  # type: ignore[attr-defined]
    return played
