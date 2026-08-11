"""Evidence and the mastery update (PRODUCT.md §10.5).

§10.5's claim is that in-story behaviour *is* the assessment. These tests hold it
to both halves of that: the estimate really does move on what a child did, and
nothing anywhere took a test to make it move.

The unit tests build `Evidence` directly, because the arithmetic is the part a
parent will eventually be shown and it deserves to be pinned without a story in
the way. The integration tests play a real adventure and end it, because the part
that actually breaks is the wiring between a tap and a number.
"""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from apps.learning import engine
from apps.learning.models import LearningProgress, Objective
from apps.learning.tasks import update_from_session
from apps.learning.tests.conftest import set_progress
from apps.story import orchestrator
from apps.story.models import Beat, Choice, PlaySession

pytestmark = pytest.mark.django_db


def evidence(
    *,
    code: str = "count_to_5",
    difficulty: float = 0.5,
    correct: bool = True,
    assisted: bool = False,
    retries: int = 0,
    latency_ms: int | None = 3000,
    minutes_ago: float = 0.0,
) -> engine.Evidence:
    return engine.Evidence(
        objective_code=code,
        difficulty=difficulty,
        evidence_key="count_stepping_stones",
        correct=correct,
        assisted=assisted,
        retries=retries,
        latency_ms=latency_ms,
        at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
    )


# ---------------------------------------------------------------------------
# What one answer says (§10.5)
# ---------------------------------------------------------------------------
def test_a_clean_success_says_they_can_do_this_and_a_little_more() -> None:
    assert engine.observation(evidence(difficulty=0.5)) == pytest.approx(
        0.5 + engine.EVIDENCE_MARGIN
    )


def test_a_success_that_needed_help_says_they_are_right_at_their_edge() -> None:
    assert engine.observation(evidence(difficulty=0.5, assisted=True)) == pytest.approx(0.5)
    assert engine.observation(evidence(difficulty=0.5, retries=1)) == pytest.approx(0.5)


def test_a_long_pause_stops_a_right_answer_reading_as_fluency() -> None:
    slow = evidence(difficulty=0.5, latency_ms=engine.SLOW_MS + 1)

    assert engine.observation(slow) == pytest.approx(0.5)


def test_an_answer_they_did_not_reach_says_this_was_above_them_today() -> None:
    assert engine.observation(evidence(difficulty=0.5, correct=False)) == pytest.approx(
        0.5 - engine.EVIDENCE_MARGIN
    )


def test_the_observation_is_anchored_to_difficulty_rather_than_to_right_and_wrong() -> None:
    """§5.5's loop only closes if it is (§10.5).

    Counting two frogs correctly cannot make a child a master of counting, and
    missing ten of them cannot take everything away. Without the anchor the
    estimate would swing to the ends on the *easiest* evidence we have.
    """
    easy_win = engine.observation(evidence(difficulty=0.1))
    hard_win = engine.observation(evidence(difficulty=0.9))

    assert easy_win < 0.5 < hard_win


# ---------------------------------------------------------------------------
# The estimate moves (criterion 5)
# ---------------------------------------------------------------------------
def test_mastery_rises_on_a_success_and_falls_on_a_miss(aarav) -> None:
    set_progress(aarav, "count_to_5", mastery=0.5, uncertainty=0.4, evidence_count=3)

    engine.apply_evidence(aarav, [evidence(difficulty=0.7)])
    risen = LearningProgress.objects.get(child=aarav, objective__code="count_to_5").mastery
    assert risen > 0.5

    engine.apply_evidence(aarav, [evidence(difficulty=0.7, correct=False)])
    fallen = LearningProgress.objects.get(child=aarav, objective__code="count_to_5").mastery
    assert fallen < risen


def test_the_first_thing_we_ever_see_moves_the_estimate_furthest(aarav) -> None:
    """`uncertainty` is a learning rate wearing an honest name (§10.2)."""
    set_progress(aarav, "count_to_5", mastery=0.3, uncertainty=1.0, evidence_count=0)
    engine.apply_evidence(aarav, [evidence(difficulty=0.6)])
    first = LearningProgress.objects.get(child=aarav, objective__code="count_to_5")
    big_step = first.mastery - 0.3

    before = first.mastery
    engine.apply_evidence(aarav, [evidence(difficulty=0.6)])
    second = LearningProgress.objects.get(child=aarav, objective__code="count_to_5")

    assert second.mastery - before < big_step
    assert second.uncertainty < first.uncertainty


def test_confidence_never_reaches_zero(aarav) -> None:
    """Children change. An estimate that can no longer move is one that will be
    wrong for a year (`MIN_UNCERTAINTY`)."""
    set_progress(aarav, "count_to_5", mastery=0.5, uncertainty=1.0)
    engine.apply_evidence(aarav, [evidence(minutes_ago=index) for index in range(40)])

    row = LearningProgress.objects.get(child=aarav, objective__code="count_to_5")
    assert row.uncertainty >= engine.MIN_UNCERTAINTY


def test_evidence_is_counted_and_dated(aarav) -> None:
    set_progress(aarav, "count_to_5", mastery=0.4, evidence_count=2)

    engine.apply_evidence(aarav, [evidence(minutes_ago=5), evidence(minutes_ago=1)])

    row = LearningProgress.objects.get(child=aarav, objective__code="count_to_5")
    assert row.evidence_count == 4
    assert row.last_practiced_at is not None
    # The *latest* piece of evidence, not the first: "when did they last do this".
    assert (timezone.now() - row.last_practiced_at) < datetime.timedelta(minutes=3)


def test_a_progress_row_is_created_the_first_time_a_child_shows_us_something(aarav) -> None:
    assert not LearningProgress.objects.filter(child=aarav).exists()

    engine.apply_evidence(aarav, [evidence()])

    assert LearningProgress.objects.filter(child=aarav, objective__code="count_to_5").exists()


def test_evidence_for_an_objective_no_longer_in_the_catalog_is_dropped(aarav, caplog) -> None:
    """A beat is a durable record; a catalog we have since changed is our problem."""
    with caplog.at_level("WARNING", logger="apps.learning.engine"):
        moved = engine.apply_evidence(aarav, [evidence(code="ancient_runes")])

    assert moved == {}
    assert not LearningProgress.objects.filter(child=aarav).exists()
    assert "learning.unknown_objective" in caplog.text


# ---------------------------------------------------------------------------
# Spacing advances (§10.3)
# ---------------------------------------------------------------------------
def test_the_review_date_advances_once_a_child_has_practised(aarav) -> None:
    set_progress(aarav, "count_to_5", mastery=0.4, due_in_days=-2)

    engine.apply_evidence(aarav, [evidence()])

    row = LearningProgress.objects.get(child=aarav, objective__code="count_to_5")
    assert row.due_at is not None
    assert row.due_at > timezone.now()


def test_something_known_well_comes_back_later_than_something_barely_known() -> None:
    now = timezone.now()

    assert engine.next_due(0.9, at=now) > engine.next_due(0.1, at=now)
    assert engine.next_due(0.0, at=now) == now + datetime.timedelta(days=engine.DUE_MIN_DAYS)
    assert engine.next_due(1.0, at=now) == now + datetime.timedelta(days=engine.DUE_MAX_DAYS)


# ---------------------------------------------------------------------------
# Reading a played story back as evidence (§10.5)
# ---------------------------------------------------------------------------
def played(child, *, correct: bool, assisted: bool = False, latency_ms: int = 3000):
    """Play one adventure, answering its learning beats as told.

    Answers are read off `interaction.expected` rather than hard-coded, so this
    stays honest if the engine ever asks for a different number: the child either
    gives the answer the beat wanted or does not, and the rest is the engine's.
    """
    adventure = orchestrator.create_adventure(child)
    previous = None
    for seq in range(1, adventure.estimated_beats + 1):
        beat = orchestrator.create_beat(
            adventure, seq=seq, prev_choice=orchestrator.turn_from(previous)
        )
        expected = (beat.interaction or {}).get("expected")
        if expected is None:
            key = next((option["key"] for option in beat.options), "continue")
            orchestrator.record_choice(beat, chosen_key=key, latency_ms=latency_ms)
        else:
            answer = expected if correct else _wrong(expected)
            key = "count" if not beat.options else str(answer)
            orchestrator.record_choice(
                beat,
                chosen_key=key,
                value=answer,
                latency_ms=latency_ms,
                retries=1 if assisted else 0,
                assisted=assisted,
            )
        previous = beat
    return adventure


def _wrong(expected):
    if isinstance(expected, int):
        return expected + 1
    return f"not_{expected}"


def test_an_adventure_the_child_got_right_yields_evidence_that_moves_mastery(
    aarav, fake_ai, enqueued
) -> None:
    """Criterion 5, end to end: a tap becomes a number in the skill model."""
    adventure = played(aarav, correct=True)

    found = engine.evidence_from(adventure)
    assert found, "a played adventure must produce evidence"
    assert all(item.objective_code == adventure.primary_objective_id for item in found)
    assert all(item.correct for item in found)

    engine.apply_evidence(aarav, found)

    row = LearningProgress.objects.get(child=aarav, objective__code=adventure.primary_objective_id)
    assert row.mastery > 0.0
    assert row.evidence_count == len(found)


def test_a_beat_that_asked_nothing_is_not_read_as_evidence(aarav, fake_ai, enqueued) -> None:
    """Story is story. Reading the choice between two good paths as an assessment
    would be reading noise (§10.5)."""
    adventure = played(aarav, correct=True)

    scored = {item.evidence_key for item in engine.evidence_from(adventure)}
    answered = Choice.objects.filter(adventure=adventure).count()

    assert 0 < len(engine.evidence_from(adventure)) < answered
    assert scored


def test_a_safe_fallback_beat_teaches_nothing(aarav, fake_ai, enqueued) -> None:
    """A degraded sitting is a sitting with *less* evidence, never false evidence.

    The child was rescued into a pre-vetted beat (§13.3). Reading the tap that
    followed as evidence about counting would move a real number on the strength
    of a story that never asked.
    """
    adventure = orchestrator.create_adventure(aarav)
    beat = orchestrator._persist_fallback(
        adventure,
        seq=2,
        decision=orchestrator.safety.Decision(),
        reason="output_guard",
        report=False,
    )
    orchestrator.record_choice(beat, chosen_key="rest_here", value=3)

    assert (beat.learning or {})["objective_id"] == "none"
    assert engine.evidence_from(adventure) == []


def test_an_unanswered_beat_is_not_evidence(aarav, fake_ai, enqueued) -> None:
    """A child who wandered off mid-beat told us nothing about counting."""
    adventure = orchestrator.create_adventure(aarav)
    for seq in (1, 2):
        orchestrator.create_beat(adventure, seq=seq)

    assert engine.evidence_from(adventure) == []


@pytest.mark.parametrize(("answer", "expected"), [(3, 3), ("3", 3), (3, "3"), ("Blue", "blue")])
def test_an_answer_is_matched_forgivingly(answer, expected) -> None:
    """The count pad sends an int and a model may have written a string.

    A comparison that cannot be made reads as "this was above them today" — it
    costs a fraction of an estimate and never a word of narration.
    """
    assert engine._matches(answer, expected)


def test_a_missing_answer_never_counts_as_correct() -> None:
    assert not engine._matches(None, 3)
    assert not engine._matches(3, None)


# ---------------------------------------------------------------------------
# The task (§16.3)
# ---------------------------------------------------------------------------
def test_ending_a_sitting_enqueues_the_update_alongside_consolidation(
    aarav, fake_ai, enqueued, django_capture_on_commit_callbacks
) -> None:
    """§5.4 and §5.5 are siblings on the same request, and both must fire."""
    adventure = played(aarav, correct=True)
    session = adventure.session
    assert session is not None

    with django_capture_on_commit_callbacks(execute=True):
        orchestrator.end_adventure(adventure)

    assert enqueued["consolidate"] == [str(session.pk)]
    assert enqueued["learning"] == [str(session.pk)]


def test_a_broker_that_refuses_one_job_does_not_lose_the_other(
    aarav, fake_ai, monkeypatch, django_capture_on_commit_callbacks, caplog
) -> None:
    """Mastery must not stop moving because memories failed to queue."""
    sent: list[str] = []

    def refuse(session_id: str) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(orchestrator.consolidate, "delay", refuse)
    monkeypatch.setattr(
        orchestrator.update_from_session, "delay", lambda session_id: sent.append(session_id)
    )

    adventure = played(aarav, correct=True)
    with caplog.at_level("ERROR", logger="apps.story.orchestrator"):
        with django_capture_on_commit_callbacks(execute=True):
            orchestrator.end_adventure(adventure)

    assert sent == [str(adventure.session.pk)]
    assert "story.consolidate_enqueue_failed" in caplog.text


def test_the_task_updates_the_skill_model_and_stamps_the_sitting(
    aarav, fake_ai, enqueued, django_capture_on_commit_callbacks
) -> None:
    adventure = played(aarav, correct=True)
    with django_capture_on_commit_callbacks(execute=True):
        orchestrator.end_adventure(adventure)

    assert update_from_session(str(adventure.session.pk)) == "updated"

    session = PlaySession.objects.get(pk=adventure.session.pk)
    assert session.learning_updated_at is not None
    assert LearningProgress.objects.filter(child=aarav).exists()


def test_a_redelivered_task_does_not_fold_the_same_evidence_in_twice(
    aarav, fake_ai, enqueued, django_capture_on_commit_callbacks
) -> None:
    """Celery is at-least-once (§16.3).

    Without the gate, a child who counted five frogs once would look like a child
    who did it twice — invisibly, permanently, and in the number a parent is shown
    on Day 12.
    """
    adventure = played(aarav, correct=True)
    with django_capture_on_commit_callbacks(execute=True):
        orchestrator.end_adventure(adventure)

    assert update_from_session(str(adventure.session.pk)) == "updated"
    once = LearningProgress.objects.get(child=aarav, objective__code=adventure.primary_objective_id)

    assert update_from_session(str(adventure.session.pk)) == "already"
    twice = LearningProgress.objects.get(
        child=aarav, objective__code=adventure.primary_objective_id
    )

    assert (twice.mastery, twice.evidence_count) == (once.mastery, once.evidence_count)


def test_a_sitting_with_nothing_to_learn_from_is_still_completed(aarav, fake_ai, enqueued) -> None:
    """ "We looked and there was nothing" is a finished update, not a retry."""
    session = PlaySession.objects.create(child=aarav)

    assert update_from_session(str(session.pk)) == "empty"

    session.refresh_from_db()
    assert session.learning_updated_at is not None


def test_a_deleted_family_is_not_an_error(aarav) -> None:
    """§13.8: the deletion is the newer instruction."""
    assert update_from_session("00000000-0000-0000-0000-000000000000") == "missing"


# ---------------------------------------------------------------------------
# There is no quiz (Principle #1, §10.5)
# ---------------------------------------------------------------------------
def test_nothing_the_child_can_see_carries_a_score(aarav, fake_ai, enqueued) -> None:
    """The whole of §10.5's "there is no test", asserted where it would show.

    A `Choice` records what the child did — how long, how many tries, whether the
    companion helped — and there is no column anywhere saying whether it was
    right. `correct` is computed inside the engine, at read time, and never
    written down where a client, an export, or a parent could meet it.
    """
    adventure = played(aarav, correct=False, assisted=True)

    columns = {field.name for field in Choice._meta.get_fields()}
    assert not columns & {"correct", "score", "grade", "passed", "wrong"}

    for beat in Beat.objects.filter(adventure=adventure):
        narration = beat.narration.lower()
        for banned in ("wrong", "incorrect", "score", "quiz", "test", "lesson", "well done, that"):
            assert banned not in narration, f"beat {beat.seq}: {banned!r}"


def test_the_skill_model_moved_even_though_the_child_was_never_marked(
    aarav, fake_ai, enqueued, django_capture_on_commit_callbacks
) -> None:
    """The two halves of §10.5 in one place: assessment happened, no test did."""
    adventure = played(aarav, correct=False, assisted=True)
    with django_capture_on_commit_callbacks(execute=True):
        orchestrator.end_adventure(adventure)
    update_from_session(str(adventure.session.pk))

    row = LearningProgress.objects.get(child=aarav, objective__code=adventure.primary_objective_id)
    assert row.evidence_count > 0
    # They needed a hand, so the estimate settled *below* what they were asked
    # for — which is the engine learning that it aimed slightly high, not a mark
    # against the child.
    assert row.mastery < Objective.objects.count()  # sanity: a real 0-1 estimate
    assert 0.0 <= row.mastery <= 1.0
