"""Fixtures for the parent dashboard suite.

Two children who have actually played, because every claim this day makes is a
claim about a real sitting. A report built from a hand-written `SessionReport` row
would test the serializer and nothing that matters: the acceptance criteria are
about whether the sentence a parent reads is *true of what happened*, which can
only be checked against a story that was played.

The catalog is not a fixture, for the same reason it is not one in the learning
suite: it arrives through `learning.0002_seed_catalog`, exactly as it does in
production.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.companion.service import create_companion
from apps.profiles.models import Child
from apps.story import orchestrator
from apps.story.models import Adventure, Choice, PlaySession

PASSWORD = "storybook-primer-42"


def _make_child(email: str, name: str, *, age: int, interests: list[str]) -> Child:
    parent = get_user_model().objects.create_user(email=email, password=PASSWORD)
    child = Child.objects.create(
        parent=parent,
        first_name=name,
        birthdate=datetime.date(datetime.date.today().year - age, 5, 4),
        interests=interests,
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


@pytest.fixture
def aarav(db: Any) -> Child:
    """Five years old, loves frogs and rivers."""
    return _make_child("report-aarav@example.com", "Aarav", age=5, interests=["frogs", "rivers"])


@pytest.fixture
def nia(db: Any) -> Child:
    """Another family's child. Exists so every scoping test has somewhere to fail."""
    return _make_child("report-nia@example.com", "Nia", age=5, interests=["boats"])


@pytest.fixture(autouse=True)
def no_media_worker(monkeypatch: Any) -> None:
    """Keep illustration and voice work out of this suite.

    Nothing here is about pictures, and queueing them into a broker nobody drains
    would add latency and noise to every test that persists a beat.
    """
    monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)


@pytest.fixture(autouse=True)
def no_post_session_jobs(monkeypatch: Any) -> None:
    """Nothing is enqueued by ending a sitting in this suite.

    The tests here call the pieces directly — `reports.generate_session_report`,
    `learning.update_from_adventures`, `interest.update_from_session` — so that
    each one's behaviour is asserted rather than the four of them arriving in some
    order a broker chose. `test_reports.py::TestTheCloseEnqueuesTheReport` is the
    one place the wiring itself is checked, and it un-stubs what it needs.
    """
    for task in (
        orchestrator.consolidate,
        orchestrator.update_from_session,
        orchestrator.update_interests,
        orchestrator.generate_report,
    ):
        monkeypatch.setattr(task, "delay", lambda session_id: None)


def play(child: Child, *, correct: bool = True, assisted: bool = False) -> Adventure:
    """Play one adventure through, answering its learning beats as told.

    Lifted from the learning suite's helper, and deliberately the same shape: the
    answers are read off `interaction.expected` rather than hard-coded, so this
    keeps telling the truth if the engine ever asks for a different number.
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
            orchestrator.record_choice(beat, chosen_key=key, latency_ms=3000)
        else:
            answer = expected if correct else _wrong(expected)
            key = "count" if not beat.options else str(answer)
            orchestrator.record_choice(
                beat,
                chosen_key=key,
                value=answer,
                latency_ms=3000,
                retries=1 if assisted else 0,
                assisted=assisted,
            )
        previous = beat
    return adventure


def _wrong(expected: Any) -> Any:
    if isinstance(expected, int):
        return expected + 1
    return f"not_{expected}"


def finish(adventure: Adventure, *, reason: str = PlaySession.EndedReason.COMPLETED) -> PlaySession:
    """End the sitting and run the two updates the report snapshots.

    Run here rather than left to a worker, because a report's whole job is to say
    what the skill model and the interest graph looked like *after* this sitting —
    and a test that skipped them would be asserting against a snapshot of nothing.
    This is what `analytics.tasks.generate_report`'s gate waits for in production.
    """
    from apps.learning import engine
    from apps.memory import interest

    orchestrator.end_adventure(adventure, reason=reason)
    # Asserted rather than cast: this helper's contract is that the adventure it
    # was handed opened a sitting, and if that ever stops being true the test
    # should say so here rather than fail three lines later on a null lookup.
    assert adventure.session_id is not None
    session = PlaySession.objects.get(pk=adventure.session_id)
    engine.update_from_adventures(session.child, [adventure])
    session.learning_updated_at = datetime.datetime.now(tz=datetime.UTC)
    session.save(update_fields=["learning_updated_at"])
    interest.update_from_session(session)
    session.refresh_from_db()
    return session


def spoke(adventure: Adventure, words: str) -> None:
    """Put a word of the child's own into the sitting.

    The strongest signal a sitting produces (§11.4) and the thing a highlight is
    supposed to be about, so several tests need one and none of them should have to
    know which beat asked a question.
    """
    choice = Choice.objects.filter(adventure=adventure).order_by("created_at").first()
    assert choice is not None, "the adventure produced no choices to speak into"
    choice.value = words
    choice.save(update_fields=["value"])
