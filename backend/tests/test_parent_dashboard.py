"""The parent dashboard API (PRODUCT.md §12, §18.3, §18.7).

Criterion 4 is what this file exists for: *the transparency log shows the child's
actual stories verbatim, and parent B cannot read parent A's child*. Every
endpoint added today is checked in both directions — the owner gets their data,
and a second real account, signed in with a real token, is refused.

The scoping tests are deliberately repetitive. There is one shared helper
(`ParentChildView.child_for`) behind most of these endpoints, and a test per
endpoint is what stops the day somebody adds a ninth that forgets to call it.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from apps.analytics import reports
from apps.companion.service import create_companion
from apps.profiles.models import Child
from apps.story import orchestrator
from apps.story.models import Adventure, Beat, PlaySession
from tests.conftest import LOGIN_URL, PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def quiet_workers(monkeypatch: Any) -> None:
    """No pictures, no voice, no post-session tasks. This file is about HTTP."""
    monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)
    for task in (
        orchestrator.consolidate,
        orchestrator.update_from_session,
        orchestrator.update_interests,
        orchestrator.generate_report,
    ):
        monkeypatch.setattr(task, "delay", lambda session_id: None)


def make_child(parent: Any, name: str) -> Child:
    child = Child.objects.create(
        parent=parent,
        first_name=name,
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
        interests=["frogs", "rivers"],
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


def play_and_finish(child: Child) -> tuple[Adventure, PlaySession]:
    adventure = orchestrator.create_adventure(child)
    previous = None
    for seq in range(1, adventure.estimated_beats + 1):
        beat = orchestrator.create_beat(
            adventure, seq=seq, prev_choice=orchestrator.turn_from(previous)
        )
        expected = (beat.interaction or {}).get("expected")
        key = next((option["key"] for option in beat.options), "continue")
        orchestrator.record_choice(beat, chosen_key=key, value=expected, latency_ms=2500)
        previous = beat
    orchestrator.end_adventure(adventure)
    # Asserted rather than cast: this helper's contract is that the adventure it
    # was handed opened a sitting, and if that ever stops being true the test
    # should say so here rather than fail three lines later on a null lookup.
    assert adventure.session_id is not None
    return adventure, PlaySession.objects.get(pk=adventure.session_id)


@pytest.fixture
def aarav(parent, consent) -> Child:
    return make_child(parent, "Aarav")


@pytest.fixture
def played(aarav, fake_ai) -> tuple[Adventure, PlaySession]:
    return play_and_finish(aarav)


@pytest.fixture
def intruder(api, other_parent) -> Any:
    """A second, real, signed-in account. Not a forged token — a different family.

    A test that asserted scoping with an *invalid* token would only be testing
    authentication. What §17.5 and §13.8 require is that a perfectly valid parent
    cannot reach another family's child, which needs two real accounts.
    """
    from rest_framework.test import APIClient

    client = APIClient()
    response = client.post(
        LOGIN_URL, {"email": other_parent.email, "password": PASSWORD}, format="json"
    )
    assert response.status_code == 200, response.data
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    return client


def urls(child: Child, session: PlaySession, adventure: Adventure) -> dict[str, str]:
    base = f"/api/v1/children/{child.id}"
    return {
        "sessions": f"{base}/reports/sessions",
        "session": f"{base}/reports/session/{session.id}",
        "weekly": f"{base}/reports/weekly",
        "progress": f"{base}/progress",
        "insights": f"{base}/insights",
        "memories": f"{base}/memories",
        "transcripts": f"{base}/transcripts",
        "transcript": f"/api/v1/adventures/{adventure.id}/transcript",
    }


# ---------------------------------------------------------------------------
# Criterion 4: owner scoping, on every endpoint
# ---------------------------------------------------------------------------
def test_another_parent_is_refused_on_every_dashboard_endpoint(
    auth, intruder, aarav, played
) -> None:
    adventure, session = played

    for name, url in urls(aarav, session, adventure).items():
        mine = auth.get(url)
        assert mine.status_code == 200, f"{name}: {mine.status_code} {mine.data}"

        theirs = intruder.get(url)
        assert theirs.status_code == 403, f"{name} leaked to another parent: {theirs.status_code}"


def test_an_unauthenticated_visitor_reaches_nothing(api, aarav, played) -> None:
    adventure, session = played

    for name, url in urls(aarav, session, adventure).items():
        response = api.get(url)
        assert response.status_code == 401, f"{name}: {response.status_code}"


def test_a_session_id_belonging_to_another_child_is_not_readable(
    auth, other_parent, aarav, played, fake_ai
) -> None:
    """The URL names a child *and* a sitting, and both have to agree.

    Owning the child in the path is not enough: asking for another child's session
    id under your own child must not answer. Without the `child=` filter in
    `report_for_session` this is the shape the leak would take.
    """
    theirs = make_child(other_parent, "Nia")
    _, their_session = play_and_finish(theirs)

    response = auth.get(f"/api/v1/children/{aarav.id}/reports/session/{their_session.id}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Criterion 1 + 5: the session report over the wire
# ---------------------------------------------------------------------------
def test_the_session_report_is_generated_on_demand_and_is_about_this_sitting(
    auth, aarav, played
) -> None:
    adventure, session = played

    response = auth.get(f"/api/v1/children/{aarav.id}/reports/session/{session.id}")

    assert response.status_code == 200
    body = response.data
    assert body["session_id"] == str(session.id)
    assert body["adventure_title"] == adventure.title
    assert body["beats"] == Beat.objects.filter(adventure=adventure).count()
    assert body["highlight"]
    assert body["highlight_source"] in {"model", "data", "quiet"}


def test_a_sitting_that_has_not_finished_has_no_report(auth, aarav, fake_ai) -> None:
    orchestrator.create_adventure(aarav)
    session = PlaySession.objects.filter(child=aarav).first()
    assert session is not None

    response = auth.get(f"/api/v1/children/{aarav.id}/reports/session/{session.id}")

    assert response.status_code == 404


def test_the_reports_index_lists_what_was_played(auth, aarav, played) -> None:
    _, session = played
    reports.generate_session_report(session)

    response = auth.get(f"/api/v1/children/{aarav.id}/reports/sessions")

    assert response.status_code == 200
    assert [row["session_id"] for row in response.data] == [str(session.id)]


# ---------------------------------------------------------------------------
# Criterion 2: the weekly report matches §18.7
# ---------------------------------------------------------------------------
def test_the_weekly_report_is_shaped_as_18_7_writes_it(auth, aarav, played) -> None:
    _, session = played
    reports.generate_session_report(session)

    response = auth.get(f"/api/v1/children/{aarav.id}/reports/weekly")

    assert response.status_code == 200
    assert set(response.data) == {
        "week",
        "sessions",
        "completionRate",
        "highlight",
        "skills",
        "milestone",
        "interests_trend",
        "recommendation",
    }
    assert response.data["sessions"] == 1


def test_a_week_can_be_asked_for_by_date_and_a_bad_one_is_refused(auth, aarav) -> None:
    good = auth.get(f"/api/v1/children/{aarav.id}/reports/weekly?week=2026-08-05")
    assert good.status_code == 200
    assert good.data["week"] == "2026-08-03/2026-08-09"

    # Silently showing this week instead would be a report about the wrong days.
    bad = auth.get(f"/api/v1/children/{aarav.id}/reports/weekly?week=last-tuesday")
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# Criterion 3: insights carry no grades
# ---------------------------------------------------------------------------
def test_progress_is_words_and_the_words_are_not_a_grade(auth, aarav, played) -> None:
    response = auth.get(f"/api/v1/children/{aarav.id}/progress")

    assert response.status_code == 200
    assert response.data
    for row in response.data:
        assert row["status"] in {"not_started", "emerging", "practising", "mastered"}
        assert row["summary"]
        assert "%" not in row["summary"]
        assert row["milestone"] and "_" not in row["milestone"]


def test_insights_group_objectives_under_milestones_and_name_the_interests(
    auth, aarav, played
) -> None:
    _, session = played
    reports.generate_session_report(session)

    response = auth.get(f"/api/v1/children/{aarav.id}/insights")

    assert response.status_code == 200
    milestones = response.data["milestones"]
    assert milestones
    assert all(view["skills"] for view in milestones)
    assert {view["domain"] for view in milestones} == {"numeracy", "vocabulary"}
    assert response.data["interests"]["now"]


# ---------------------------------------------------------------------------
# Criterion 4: the transparency log is verbatim
# ---------------------------------------------------------------------------
def test_the_transcript_is_the_stored_beats_word_for_word(auth, aarav, played) -> None:
    adventure, _ = played
    beats = list(Beat.objects.filter(adventure=adventure).order_by("seq"))

    response = auth.get(f"/api/v1/adventures/{adventure.id}/transcript")

    assert response.status_code == 200
    told = response.data["beats"]
    assert [entry["seq"] for entry in told] == [beat.seq for beat in beats]
    for entry, beat in zip(told, beats, strict=True):
        # Verbatim: not truncated, not tidied, not summarised (§12.6).
        assert entry["narration"] == beat.narration
        assert entry["safetyStatus"] == beat.safety_status


def test_the_transcript_says_what_the_child_did_under_each_beat(auth, aarav, played) -> None:
    adventure, _ = played

    response = auth.get(f"/api/v1/adventures/{adventure.id}/transcript")

    answered = [entry["answered"] for entry in response.data["beats"] if entry["answered"]]
    assert answered, "a played story must show the child's own taps"


def test_the_transparency_index_lists_only_stories_that_happened(
    auth, aarav, played, fake_ai
) -> None:
    adventure, _ = played
    # An adventure that never produced a beat is not something the child
    # experienced, so offering it as readable would be an empty promise.
    orchestrator.create_adventure(aarav)

    response = auth.get(f"/api/v1/children/{aarav.id}/transcripts")

    assert response.status_code == 200
    assert [row["adventureId"] for row in response.data] == [str(adventure.id)]


def test_memories_are_shown_without_their_ranking_numbers(auth, aarav, played) -> None:
    from apps.memory.models import Memory, MemoryType

    Memory.objects.create(
        child=aarav,
        type=MemoryType.ENTITY,
        content="Aarav named the little frog Sparkle.",
        salience=0.8,
    )

    response = auth.get(f"/api/v1/children/{aarav.id}/memories")

    assert response.status_code == 200
    assert response.data[0]["content"] == "Aarav named the little frog Sparkle."
    assert response.data[0]["type_label"] == "A character, object or place that matters"
    assert "salience" not in response.data[0]
    assert "decay" not in response.data[0]


def test_a_deleted_memory_is_not_shown(auth, aarav) -> None:
    from django.utils import timezone

    from apps.memory.models import Memory, MemoryType

    Memory.objects.create(
        child=aarav,
        type=MemoryType.EVENT,
        content="Something the parent asked us to forget.",
        deleted_at=timezone.now(),
    )

    response = auth.get(f"/api/v1/children/{aarav.id}/memories")

    assert response.data == []
