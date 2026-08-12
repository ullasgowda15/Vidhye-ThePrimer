"""Fixtures for the Learning Engine suite.

Two children with companions, a way to put a skill model in a known state, and a
way to play a story far enough for it to have taught something.

The catalog itself is **not** a fixture: it arrives through
`0002_seed_catalog`, which is the same way it arrives in production. A suite that
built its own objectives would pass while the seed migration was empty, and the
first acceptance criterion of this day is that the seed exists.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.companion.service import create_companion
from apps.learning.models import LearningProgress, Objective
from apps.profiles.models import Child
from apps.story import orchestrator

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
    """Five years old, loves frogs and rivers, has never played."""
    return _make_child("learning-aarav@example.com", "Aarav", age=5, interests=["frogs", "rivers"])


@pytest.fixture
def nia(db: Any) -> Child:
    """The same age as Aarav, and deliberately so.

    Every difference the tests observe between these two has to come from their
    skill models rather than from their birthdays — otherwise "two children get
    different difficulty for the same objective" would be a test about the age
    filter (§10.3, criterion 6).
    """
    return _make_child("learning-nia@example.com", "Nia", age=5, interests=["boats", "the sea"])


@pytest.fixture(autouse=True)
def no_media_worker(monkeypatch: Any) -> None:
    """Keep illustration and voice work out of this suite.

    Same reasoning as the story and memory suites: nothing here is about pictures,
    and queueing them into a broker nobody is draining would add latency and noise
    to every test that persists a beat.
    """
    monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)


@pytest.fixture
def enqueued(monkeypatch: Any) -> dict[str, list[str]]:
    """Capture what `end_adventure` hands to workers, instead of sending it.

    Every job the close enqueues that this suite has an opinion about, because a
    test that only stubbed one would post real traffic to a broker the suite does
    not own — and would hide a regression in which another enqueue silently
    disappeared.
    """
    sent: dict[str, list[str]] = {"consolidate": [], "learning": [], "report": []}
    monkeypatch.setattr(
        orchestrator.consolidate, "delay", lambda session_id: sent["consolidate"].append(session_id)
    )
    monkeypatch.setattr(
        orchestrator.update_from_session,
        "delay",
        lambda session_id: sent["learning"].append(session_id),
    )
    # Four from Day 12 (§12.1). The parent's report reads what the mastery update
    # writes, so a learning test that let it run would be paying for a model call
    # to produce a sentence nothing in this suite reads.
    monkeypatch.setattr(
        orchestrator.generate_report,
        "delay",
        lambda session_id: sent["report"].append(session_id),
    )
    return sent


def set_progress(
    child: Child,
    code: str,
    *,
    mastery: float,
    uncertainty: float = 0.3,
    practised_days_ago: float | None = None,
    due_in_days: float | None = None,
    evidence_count: int = 4,
) -> LearningProgress:
    """Put one objective of one child's skill model into a known state.

    A helper rather than a fixture because most tests need two or three of them,
    and because the *shape* of the state is what each test is about. Days rather
    than datetimes so a test reads as "practised a fortnight ago" rather than as
    arithmetic.
    """
    now = timezone.now()
    progress, _ = LearningProgress.objects.get_or_create(
        child=child, objective=Objective.objects.get(code=code)
    )
    progress.mastery = mastery
    progress.uncertainty = uncertainty
    progress.evidence_count = evidence_count
    progress.last_practiced_at = (
        None if practised_days_ago is None else now - datetime.timedelta(days=practised_days_ago)
    )
    progress.due_at = None if due_in_days is None else now + datetime.timedelta(days=due_in_days)
    progress.save()
    return progress
