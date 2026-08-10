"""Short-term memory (PRODUCT.md §11.1, §11.2).

Three properties, and the third is the one that would hurt if it broke.

* The sitting's events land in Redis, in order, keyed by session, with a TTL.
* The child-initiated ones are mirrored onto the session row, which is the copy
  that survives Redis restarting.
* **Nothing here can break a story.** Every function degrades to a lost signal.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.memory import capture, stm
from apps.story.models import PlaySession

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The Redis half
# ---------------------------------------------------------------------------
def test_a_played_session_leaves_beats_and_choices_in_redis(played, fake_redis) -> None:
    key = stm.key_for(played.pk)
    log = fake_redis.events(key)

    kinds = [entry["event"] for entry in log]
    assert kinds.count(stm.BEAT) == 5
    assert kinds.count(stm.CHOICE) == 4

    # Ordered as it happened, which is what makes the log a session rather than a bag.
    assert [entry["seq"] for entry in log if entry["event"] == stm.BEAT] == [1, 2, 3, 4, 5]

    # A few hours, refreshed on every write.
    assert fake_redis.ttl[key] == 6 * 60 * 60


def test_the_log_carries_the_signals_that_exist_nowhere_else(played, fake_redis) -> None:
    """Dwell, retries and assists — §11.2's named signals."""
    log = fake_redis.events(stm.key_for(played.pk))
    choices = [entry for entry in log if entry["event"] == stm.CHOICE]

    deliberate = next(entry for entry in choices if entry.get("dwell_ms") == 6100)
    assert deliberate["chosen_key"] == "follow_river"
    assert deliberate["chosen_label"]
    assert not deliberate.get("retries")

    wobble = next(entry for entry in choices if entry.get("retries") == 2)
    assert wobble["assisted"] is True
    assert wobble["dwell_ms"] == 900


def test_narration_is_not_stored_in_full(played, fake_redis) -> None:
    """STM is not a second copy of every story (§13.8's data minimisation)."""
    from apps.story.models import Beat

    beats = {beat.seq: beat for beat in Beat.objects.filter(adventure__session=played)}
    for entry in fake_redis.events(stm.key_for(played.pk)):
        if entry["event"] != stm.BEAT:
            continue
        narration = beats[entry["seq"]].narration
        anchor = entry["anchor"]
        assert anchor in narration
        assert len(anchor) <= stm.MAX_TEXT_CHARS
        # The first clause only, never the whole beat, whenever the beat had more
        # than one sentence to give.
        if len(narration) > len(anchor):
            assert anchor != narration


def test_the_list_is_trimmed_so_one_key_cannot_grow_forever(fake_redis, settings) -> None:
    settings.MEMORY_STM_MAX_EVENTS = 5
    for index in range(12):
        stm.record("session-1", stm.BEAT, seq=index)

    log = fake_redis.events(stm.key_for("session-1"))
    assert len(log) == 5
    # The *newest* five, because the mirror keeps what matters from the oldest.
    assert [entry["seq"] for entry in log] == [7, 8, 9, 10, 11]


def test_consolidation_discards_the_working_copy(fake_redis) -> None:
    stm.record("session-2", stm.BEAT, seq=1)
    assert fake_redis.lists[stm.key_for("session-2")]

    assert stm.discard("session-2") is True
    assert stm.key_for("session-2") not in fake_redis.lists


def test_long_strings_are_clipped_on_the_way_in(fake_redis) -> None:
    stm.record("session-3", stm.CHOICE, value="x" * 5000)

    stored = fake_redis.events(stm.key_for("session-3"))[0]
    assert len(stored["value"]) == stm.MAX_TEXT_CHARS


def test_none_valued_fields_are_dropped_rather_than_stored(fake_redis) -> None:
    """A null dwell and a dwell of zero are different facts; only one is recorded."""
    stm.record("session-4", stm.CHOICE, dwell_ms=None, retries=0)

    stored = fake_redis.events(stm.key_for("session-4"))[0]
    assert "dwell_ms" not in stored
    assert stored["retries"] == 0


# ---------------------------------------------------------------------------
# Degradation — the property that protects the child's story
# ---------------------------------------------------------------------------
def test_a_missing_redis_is_a_lost_signal_and_nothing_else(no_redis) -> None:
    assert stm.record("session-5", stm.BEAT, seq=1) is False
    assert stm.events("session-5") == []
    assert stm.discard("session-5") is False


def test_a_refusing_redis_never_raises(fake_redis) -> None:
    fake_redis.fail_on = {"rpush", "lrange", "delete"}

    assert stm.record("session-6", stm.BEAT, seq=1) is False
    assert stm.events("session-6") == []
    assert stm.discard("session-6") is False


def test_one_unreadable_entry_does_not_discard_the_sitting(fake_redis) -> None:
    stm.record("session-7", stm.BEAT, seq=1)
    fake_redis.lists[stm.key_for("session-7")].append("{not json")
    stm.record("session-7", stm.BEAT, seq=2)

    assert [entry["seq"] for entry in stm.events("session-7")] == [1, 2]


def test_a_session_that_was_pruned_is_simply_not_recorded(fake_redis) -> None:
    """`Adventure.session` is nullable by design (§17.2)."""
    assert stm.record(None, stm.BEAT, seq=1) is False
    assert fake_redis.lists == {}


def test_capture_of_a_sessionless_adventure_is_a_no_op(aarav, fake_ai, fake_redis) -> None:
    from apps.story import orchestrator

    adventure = orchestrator.create_adventure(aarav)
    adventure.session = None
    adventure.save(update_fields=["session"])
    beat = orchestrator.create_beat(adventure, seq=1)

    capture.beat_recorded(beat)
    assert fake_redis.lists == {}


# ---------------------------------------------------------------------------
# The session-row mirror
# ---------------------------------------------------------------------------
def test_the_mirror_keeps_what_the_child_originated(played) -> None:
    digest = PlaySession.objects.get(pk=played.pk).stm_digest

    assert digest["counts"][stm.BEAT] == 5
    assert digest["counts"][stm.CHOICE] == 4
    # Aggregates the salience pass reads: the wobble on beat 4.
    assert digest["counts"]["retries"] == 2
    assert digest["counts"]["assisted"] == 1
    assert digest["counts"]["dwell_ms"] == 4200 + 6100 + 1500 + 900

    highlights = digest["highlights"]
    # Branch picks are the child's. There were three, two of them the same label,
    # and the mirror does not keep the same highlight twice.
    assert any("follow" in item.lower() for item in highlights)
    assert len(highlights) == len(set(highlights))


def test_a_tap_that_only_carried_on_is_counted_but_not_a_highlight(
    aarav, fake_ai, fake_redis
) -> None:
    """A fact about the sitting, not a fact about the child (§11.4)."""
    from apps.story import orchestrator

    adventure = orchestrator.create_adventure(aarav)
    beat = orchestrator.create_beat(adventure, seq=1)
    # A beat that offered nothing to pick between: `chosen_key` matches no option.
    capture.choice_recorded(beat, orchestrator.record_choice(beat, chosen_key="", value=None)[0])

    assert adventure.session_id is not None
    digest = PlaySession.objects.get(pk=adventure.session_id).stm_digest
    assert digest["counts"][stm.CHOICE] >= 1
    assert digest.get("highlights", []) == []


def test_the_mirror_is_bounded(aarav) -> None:
    session = PlaySession.objects.create(child=aarav)
    for index in range(stm.MAX_DIGEST_HIGHLIGHTS + 8):
        stm.mirror(session, stm.CHOICE, highlight=f"said: thing-{index}")

    highlights = PlaySession.objects.get(pk=session.pk).stm_digest["highlights"]
    assert len(highlights) == stm.MAX_DIGEST_HIGHLIGHTS
    # The most recent, because a child who names something late in a sitting has
    # still named something.
    assert highlights[-1].endswith(f"thing-{stm.MAX_DIGEST_HIGHLIGHTS + 7}")


def test_the_mirror_survives_a_deleted_session(aarav) -> None:
    session = PlaySession.objects.create(child=aarav)
    session_id = session.pk
    PlaySession.objects.filter(pk=session_id).delete()

    stm.mirror(session, stm.BEAT)  # must not raise
    assert not PlaySession.objects.filter(pk=session_id).exists()


# ---------------------------------------------------------------------------
# One test against the real thing
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_against_a_live_redis(settings: Any) -> None:
    """The wire protocol, not taken on trust.

    Skipped when Redis is not running, because a developer must be able to run the
    suite without `make up`. CI has the container, so this is not a test that only
    ever skips.
    """
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=1, decode_responses=True
    )
    try:
        client.ping()
    except Exception:  # pragma: no cover - Redis genuinely not running
        pytest.skip("Redis is not reachable at REDIS_URL")

    session_id = "live-stm-test"
    client.delete(stm.key_for(session_id))
    try:
        assert stm.record(session_id, stm.CHOICE, chosen_key="follow_river", dwell_ms=1200) is True
        assert stm.record(session_id, stm.BEAT, seq=2) is True

        # Read back through the module *and* through a raw client, so this proves
        # the key shape a human would `LRANGE` in a terminal (§7's acceptance ask).
        assert [entry["event"] for entry in stm.events(session_id)] == [stm.CHOICE, stm.BEAT]
        raw = client.lrange(stm.key_for(session_id), 0, -1)
        assert json.loads(raw[0])["chosen_key"] == "follow_river"
        assert 0 < client.ttl(stm.key_for(session_id)) <= settings.MEMORY_STM_TTL_S

        assert stm.discard(session_id) is True
        assert client.exists(stm.key_for(session_id)) == 0
    finally:
        client.delete(stm.key_for(session_id))
