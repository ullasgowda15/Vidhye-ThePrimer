"""Consolidation, end to end (PRODUCT.md §5.4, §11.3, §11.4, §11.5, §11.10).

A whole sitting is played through the orchestrator, ended, and consolidated — and
then the questions the day's acceptance criteria ask are asked of the rows that
came out:

* did session-end return **without** consolidating (§5.4's "must not delay the child");
* are the memories **typed** and **embedded** (§11.3);
* are there a **few** of them rather than a transcript (§11.4);
* are the **child-initiated** moments among them (§11.5);
* is it all **deletable via the child cascade** and never plaintext-logged (§13.8).

The scripted provider answers the consolidation schema (see `conftest`), so both
halves of the pipeline — the deterministic extraction and the model proposals —
run in every test here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.memory import consolidation, prompts, stm
from apps.memory.models import Memory, MemoryType
from apps.memory.tasks import consolidate
from apps.profiles.models import Child
from apps.story import orchestrator
from apps.story.models import Adventure, PlaySession

pytestmark = pytest.mark.django_db


def contents(child: Child) -> str:
    return " | ".join(Memory.objects.filter(child=child).values_list("content", flat=True))


# ---------------------------------------------------------------------------
# It must not delay the child (§5.4, criterion 2)
# ---------------------------------------------------------------------------
def test_session_end_enqueues_and_does_not_consolidate(closed) -> None:
    """The endpoint's whole contract: it hands the work over and returns.

    `closed` stubs `consolidate.delay`, so this asserts both halves — the task was
    handed exactly one session id, and nothing was written on the request path.
    """
    assert closed.enqueued == [str(closed.pk)]
    assert closed.consolidated_at is None
    assert Memory.objects.count() == 0


def test_ending_twice_enqueues_once(
    played, monkeypatch, django_capture_on_commit_callbacks
) -> None:
    """A tap on "all done" plus the page unloading must not consolidate twice."""
    enqueued: list[str] = []
    monkeypatch.setattr(orchestrator.consolidate, "delay", lambda sid: enqueued.append(sid))
    adventure = Adventure.objects.get(session=played)

    with django_capture_on_commit_callbacks(execute=True):
        orchestrator.end_adventure(adventure, reason=PlaySession.EndedReason.COMPLETED)
    with django_capture_on_commit_callbacks(execute=True):
        orchestrator.end_adventure(adventure, reason=PlaySession.EndedReason.EXIT)

    assert enqueued == [str(played.pk)]


def test_a_rolled_back_close_enqueues_nothing(played, monkeypatch) -> None:
    """`on_commit`, not a bare call: a close that did not land is not a sitting."""
    from django.db import transaction

    enqueued: list[str] = []
    monkeypatch.setattr(orchestrator.consolidate, "delay", lambda sid: enqueued.append(sid))
    adventure = Adventure.objects.get(session=played)

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback), transaction.atomic():
        orchestrator.end_adventure(adventure)
        raise Rollback

    assert enqueued == []


def test_an_adventure_with_no_sitting_is_logged_rather_than_guessed_at(
    aarav, fake_ai, caplog, django_capture_on_commit_callbacks
) -> None:
    """`session` is nullable by design (§17.2); the gap is visible, not silent."""
    adventure = orchestrator.create_adventure(aarav)
    orchestrator.create_beat(adventure, seq=1)
    adventure.session = None
    adventure.save(update_fields=["session"])

    with caplog.at_level("WARNING"), django_capture_on_commit_callbacks(execute=True):
        orchestrator.end_adventure(adventure)

    assert "story.consolidate_skipped" in caplog.text
    assert "no_session" in caplog.text


# ---------------------------------------------------------------------------
# What it writes (§11.3, criterion 3)
# ---------------------------------------------------------------------------
def test_a_played_session_yields_a_few_typed_embedded_memories(closed, aarav) -> None:
    outcome = consolidation.consolidate_session(closed)
    memories = list(Memory.objects.filter(child=aarav))

    assert outcome.status == "consolidated"
    # A few, not dozens. Nine raw interactions (five beats, four choices) plus the
    # model's proposals went in.
    assert 2 <= len(memories) <= 6, contents(aarav)
    assert outcome.considered > len(memories)

    for memory in memories:
        assert memory.type in MemoryType.values
        assert memory.content.strip()
        assert memory.embedding is not None
        assert len(memory.embedding) == 1536
        assert 0.0 <= memory.salience <= 1.0
        assert -1.0 <= memory.valence <= 1.0
        assert memory.source_adventure_id is not None


def test_a_session_of_taps_does_not_become_a_row_per_tap(closed, aarav) -> None:
    """The transcript failure the founder's first real session produced.

    Seven beats, five branch picks, and the memory table came back four rows of
    `Spidey chose <label>` with nothing about the elephant they met. The rows were
    not duplicates — five different labels are five different sentences — so no
    lexical filter could have saved it. `candidates._acts` now proposes one branch
    pick per adventure, the one the child weighed longest.
    """
    consolidation.consolidate_session(closed)

    picks = [
        memory.content
        for memory in Memory.objects.filter(child=aarav, type=MemoryType.EVENT)
        if "chose" in memory.content
    ]
    assert len(picks) <= 1, picks

    # And the one kept is the deliberated one, not merely the first or the last.
    # `played` pauses 6.1s on the river and taps the dragonflies in 1.5s.
    if picks:
        assert "river" in picks[0].lower(), picks


def test_meeting_a_character_can_clear_the_threshold_on_its_own(aarav) -> None:
    """§11.5 calls relationships first-class; the arithmetic has to agree.

    At `MET_SOMEONE = 0.5` the best a newly-met character could score was 0.480
    against a 0.50 threshold — so no character a child met was *ever* rememberable,
    uncontested. Asserted as arithmetic rather than through a session, because that
    is the form the bug took: invisible until a real elephant went unremembered.
    """
    from apps.memory import candidates as rules
    from apps.memory.salience import WEIGHTS, Candidate, Signals, salience, threshold

    met = Candidate(
        type=MemoryType.ENTITY,
        content="Aarav met Ellie the elephant.",
        signals=Signals(
            child_initiated=rules.MET_SOMEONE,
            emotional_intensity=0.5,
            novelty=1.0,
        ),
    )
    assert salience(met) >= threshold(), (
        f"a first-class §11.5 relationship memory scores {salience(met):.3f} "
        f"against a threshold of {threshold():.2f} and can never be written"
    )
    # It must still sit below something the child authored themselves (§11.4).
    assert rules.MET_SOMEONE < rules.SPOKE
    assert WEIGHTS["child_initiated"] == max(WEIGHTS.values())


def test_what_consolidation_writes_is_ciphertext_on_disk(closed, aarav) -> None:
    """`test_crypto` proves the field; this proves the *write path* uses it.

    Consolidation writes through `bulk_create`, which is a different code path from
    the `objects.create` that file tests — and it is the only path that ever writes a
    real child's memory (§11.10).
    """
    from django.db import connection

    consolidation.consolidate_session(closed)
    written = list(Memory.objects.filter(child=aarav))
    assert written

    with connection.cursor() as cursor:
        cursor.execute("SELECT content FROM memory_memory")
        stored = [row[0] for row in cursor.fetchall()]

    assert all(value.startswith("gAAAAA") for value in stored)
    for memory in written:
        assert memory.content not in stored
    assert not any("Aarav" in value for value in stored)


def test_every_memory_cleared_the_threshold(closed, aarav, settings) -> None:
    consolidation.consolidate_session(closed)

    for memory in Memory.objects.filter(child=aarav):
        assert memory.salience >= settings.MEMORY_SALIENCE_THRESHOLD


def test_the_mundane_is_not_remembered(closed, aarav) -> None:
    """§11.4: the transcript is what we are *not* keeping."""
    consolidation.consolidate_session(closed)
    kept = contents(aarav).lower()

    assert "carried on" not in kept
    assert "tapped to carry on" not in kept


def test_child_initiated_moments_are_among_them(closed, aarav) -> None:
    """§11.5's first-class cases: what the child chose, and what they managed.

    Asserted as "the child's own acts are represented", not as an exact sentence:
    the phrasing is `candidates`' business and would make this a test of wording.
    """
    consolidation.consolidate_session(closed)
    kept = contents(aarav).lower()

    assert "aarav" in kept
    assert "chose" in kept or "counted" in kept

    types = set(Memory.objects.filter(child=aarav).values_list("type", flat=True))
    assert types & {MemoryType.EVENT, MemoryType.SKILL_MOMENT}


def test_a_skill_moment_records_its_objective_for_the_learning_engine(closed, aarav) -> None:
    """Day 10 reads this back as mastery evidence; the ref has to be there now."""
    consolidation.consolidate_session(closed)

    refs = [
        entity
        for entities in Memory.objects.filter(child=aarav).values_list("entities", flat=True)
        for entity in entities
    ]
    assert any(ref.startswith("objective:") for ref in refs)
    assert any(ref.startswith("companion:") or ref.startswith("topic:") for ref in refs)


def test_a_character_the_child_named_becomes_a_high_salience_entity(
    aarav, fake_ai, fake_redis, django_capture_on_commit_callbacks
) -> None:
    """§11.5's headline example — "named the dragon Sparkle" — end to end.

    A beat that asks the child to name someone, a beat that introduces that someone
    as persisted cast, and the child's own word as the answer.
    """
    naming = ScriptedNamingBeat.build()
    fake_ai.queue_beat_response(json.dumps(naming))

    with django_capture_on_commit_callbacks(execute=True):
        adventure = orchestrator.create_adventure(aarav)
        beat = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(beat, chosen_key="named", value="Sparkle", latency_ms=8000)
        orchestrator.create_beat(
            adventure, seq=adventure.estimated_beats, prev_choice=orchestrator.turn_from(beat)
        )

    session = adventure.session
    assert session is not None
    consolidation.consolidate_session(session)

    entities = Memory.objects.filter(child=aarav, type=MemoryType.ENTITY)
    assert entities.exists(), contents(aarav)
    sparkle = entities.first()
    assert sparkle is not None
    assert "sparkle" in sparkle.content.lower()
    # Named by the child, so it outranks a character they merely met.
    assert sparkle.salience >= 0.6
    assert any(ref.startswith("character:") for ref in sparkle.entities)

    # And the child's own words made it into an event memory too.
    assert "sparkle" in contents(aarav).lower()


class ScriptedNamingBeat:
    """A beat that asks the child to name a creature it has just introduced."""

    @staticmethod
    def build() -> dict[str, Any]:
        from conftest import ScriptedAIProvider

        beat = ScriptedAIProvider.beat("Aarav loves frogs.", "Write beat 1")
        beat["interaction"] = {
            "type": "say",
            "prompt_audio": "What shall we call the little dragon?",
            "expected": None,
            "choices": None,
        }
        beat["scene"]["characters_present"] = ["pip", "sparkle"]
        beat["scene"]["new_characters"] = [
            {
                "key": "sparkle",
                "name": "Sparkle",
                "visual_descriptor": (
                    "a small round dragon, ember-orange with a cream belly and one "
                    "crooked golden horn"
                ),
            }
        ]
        beat["emotion"] = "delighted"
        return beat


# ---------------------------------------------------------------------------
# Idempotency (Celery is at-least-once)
# ---------------------------------------------------------------------------
def test_consolidating_twice_does_not_double_a_childs_memories(closed, aarav) -> None:
    first = consolidation.consolidate_session(closed)
    written = Memory.objects.filter(child=aarav).count()

    closed.refresh_from_db()
    second = consolidation.consolidate_session(closed)

    assert first.status == "consolidated"
    assert second.status == "already"
    assert Memory.objects.filter(child=aarav).count() == written


def test_the_near_duplicate_filter_is_the_belt_to_that_braces(closed, aarav) -> None:
    """Past the gate, a second run finds its own output and writes nothing new."""
    consolidation.consolidate_session(closed)
    written = Memory.objects.filter(child=aarav).count()

    closed.refresh_from_db()
    closed.consolidated_at = None
    closed.save(update_fields=["consolidated_at"])
    consolidation.consolidate_session(closed)

    assert Memory.objects.filter(child=aarav).count() == written


def test_the_task_stamps_and_discards_the_working_copy(closed, fake_redis) -> None:
    """§11.2: STM is discarded after consolidation, not merely left to expire."""
    assert fake_redis.lists[stm.key_for(closed.pk)]

    assert consolidate(str(closed.pk)) == "consolidated"

    closed.refresh_from_db()
    assert closed.consolidated_at is not None
    assert stm.key_for(closed.pk) not in fake_redis.lists


def test_a_deleted_session_is_not_an_error(db) -> None:
    """A family deleted their account while the task was queued (§13.8)."""
    assert consolidate("00000000-0000-0000-0000-000000000000") == "missing"


def test_a_sitting_with_nothing_in_it_is_still_completed(aarav, fake_redis) -> None:
    """Otherwise "we looked and there was nothing" becomes a permanent retry."""
    session = PlaySession.objects.create(child=aarav)

    outcome = consolidation.consolidate_session(session)

    assert outcome.status == "empty"
    session.refresh_from_db()
    assert session.consolidated_at is not None


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------
def test_the_model_half_can_fail_and_the_factual_memories_survive(
    closed, aarav, monkeypatch, caplog
) -> None:
    """A provider outage costs the subtle memories, never the ones in columns."""

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise prompts.ConsolidationUnavailable("provider is down")

    monkeypatch.setattr(prompts, "propose", unavailable)

    with caplog.at_level("WARNING"):
        outcome = consolidation.consolidate_session(closed)

    assert outcome.status == "consolidated"
    assert Memory.objects.filter(child=aarav).exists()
    assert "memory.consolidate_model_unavailable" in caplog.text


def test_an_embedding_outage_costs_the_vector_and_not_the_memory(
    closed, aarav, monkeypatch, caplog
) -> None:
    from apps.media.exceptions import MediaProviderError

    monkeypatch.setattr(
        consolidation.embedding_adapter,
        "embed",
        lambda *args, **kwargs: (_ for _ in ()).throw(MediaProviderError("no vectors today")),
    )

    with caplog.at_level("WARNING"):
        outcome = consolidation.consolidate_session(closed)

    assert outcome.written > 0
    assert outcome.embedded == 0
    assert all(memory.embedding is None for memory in Memory.objects.filter(child=aarav))
    assert "memory.embed_failed" in caplog.text


def test_consolidation_works_with_no_redis_at_all(closed, no_redis, aarav) -> None:
    """The persisted rows are the floor under STM (§11.2)."""
    outcome = consolidation.consolidate_session(closed)

    assert outcome.status == "consolidated"
    assert Memory.objects.filter(child=aarav).exists()


def test_the_digest_falls_back_to_the_rows_when_the_log_is_gone(closed, no_redis) -> None:
    digest = prompts.session_digest(closed)

    assert "Child: Aarav" in digest
    assert "Beat 1:" in digest
    assert "The child" in digest


def test_a_proposal_of_an_unknown_type_is_dropped_not_coerced(aarav) -> None:
    """Retrieval rules key on `type`; a mislabelled memory is worse than none."""
    proposal = prompts.MemoryProposal(
        type="vibe", content="Aarav had a nice time.", entities=[], valence=0.5
    )
    assert prompts._usable(proposal) is False


# ---------------------------------------------------------------------------
# Privacy (§11.10, §13.8, criterion 6)
# ---------------------------------------------------------------------------
def test_memories_are_deleted_with_the_child(closed, aarav) -> None:
    """One operation satisfies "delete my child's data" (§17.5)."""
    consolidation.consolidate_session(closed)
    assert Memory.objects.filter(child=aarav).exists()

    aarav.delete()

    assert Memory.objects.count() == 0


def test_the_source_adventure_going_away_does_not_take_the_memory(closed, aarav) -> None:
    """§17.5 prunes raw beats and keeps memories; the FK is expected to go null."""
    consolidation.consolidate_session(closed)
    before = Memory.objects.filter(child=aarav).count()

    Adventure.objects.filter(session=closed).delete()

    assert Memory.objects.filter(child=aarav).count() == before
    assert Memory.objects.filter(source_adventure__isnull=True).count() == before


def test_consolidation_logs_counts_and_never_content(closed, aarav, caplog) -> None:
    """§13.8: never plaintext-logged. The log is how the threshold gets tuned."""
    with caplog.at_level("INFO", logger="apps.memory"):
        consolidation.consolidate_session(closed)

    logged = caplog.text
    assert "memory.consolidate " in logged
    assert "candidates=" in logged and "kept=" in logged and "top_dropped=" in logged

    for memory in Memory.objects.filter(child=aarav):
        assert memory.content not in logged


def test_the_digest_never_reaches_a_prompt_unscrubbed(closed) -> None:
    """§13.5: the sitting is the least trusted string in the system by now."""
    digest = prompts.session_digest(closed)

    assert "<" not in digest
    assert ">" not in digest
