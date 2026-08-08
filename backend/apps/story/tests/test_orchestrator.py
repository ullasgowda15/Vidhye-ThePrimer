"""The orchestrator's own guarantees (PRODUCT.md §8.1, §19.1, §19.6).

The API-level tests in `tests/test_adventures.py` prove the endpoint. These prove
the parts of the orchestrator that are hard to see from outside it: what actually
goes into a prompt, what happens when the plan call fails, and that the two media
stages really do overlap rather than merely being written as if they do.
"""

import asyncio
import datetime
import time
from typing import Any

import pytest
from asgiref.sync import async_to_sync

from apps.companion.service import create_companion
from apps.profiles.models import Child
from apps.story import orchestrator
from apps.story.ai.router import AIConfigurationError
from apps.story.models import Adventure

pytestmark = pytest.mark.django_db


@pytest.fixture
def frog_lover(db: Any) -> Child:
    from django.contrib.auth import get_user_model

    parent = get_user_model().objects.create_user(
        email="orchestrator-parent@example.com",
        password="storybook-primer-42",
    )
    child = Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 4, 5, 4),
        interests=["frogs", "rivers"],
        fears=["loud noises"],
        avoid_topics=["hospitals"],
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


# ---------------------------------------------------------------------------
# Prompt inputs (§19.1)
# ---------------------------------------------------------------------------
def test_seeds_become_the_memory_bundle(frog_lover) -> None:
    bundle = orchestrator.seed_memories(frog_lover)

    assert {"type": "interest", "content": "Aarav loves frogs."} in bundle
    assert {"type": "fear", "content": "Aarav is afraid of loud noises."} in bundle
    # Interests first: they are what makes beat one recognisably this child's.
    assert bundle[0]["type"] == "interest"


def test_a_child_with_no_seeds_gets_an_empty_bundle(db) -> None:
    from django.contrib.auth import get_user_model

    parent = get_user_model().objects.create_user(email="bare@example.com", password="pw-42-abcdef")
    bare = Child.objects.create(
        parent=parent,
        first_name="Nia",
        birthdate=datetime.date(datetime.date.today().year - 3, 1, 1),
    )
    assert orchestrator.seed_memories(bare) == []


def test_the_beat_prompt_carries_every_layer_and_the_plan_stage(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)
    context = orchestrator.build_beat_context(adventure, seq=2)

    system = context["system"]
    for layer in (
        "system_identity",
        "safety_policy",
        "world_and_companion_state",
        "memory_bundle",
        "learning_directive",
        "story_plan",
        "current_turn",
        "output_contract",
    ):
        assert f"<{layer}" in system, layer

    assert "Pip, a dragon" in system
    assert "count_to_3" in system
    assert "Never depict, name, or allude to" in system
    assert "hospitals" in system

    # The stage this beat is meant to land, taken from the plan by position — the
    # one thing about the plan a stateless call cannot infer.
    assert adventure.plan["skeleton"][1] in context["user"]
    assert 'Use "b2" as its beat_id' in context["user"]


def test_the_first_beat_is_told_it_is_the_opening(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)
    assert "This is the opening" in orchestrator.build_beat_context(adventure, seq=1)["user"]


def test_only_the_companion_may_be_named(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)
    user = orchestrator.build_beat_context(adventure, seq=1)["user"]

    # A recurring cast lives in world state, which does not exist yet — so any
    # other character has to be described rather than named (§8.2, §8.3).
    assert "Pip" in user
    assert "must be described in scene.illustration_prompt" in user


# ---------------------------------------------------------------------------
# The plan (§8.1)
# ---------------------------------------------------------------------------
def test_the_plan_is_generated_and_persisted(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)

    assert fake_ai.plan_calls == 1
    assert adventure.plan["title"] == "The Frog Chorus"
    assert len(adventure.plan["skeleton"]) == 5
    assert adventure.estimated_beats == 5
    assert "source" not in adventure.plan


def test_a_failed_plan_call_degrades_instead_of_raising(frog_lover, caplog) -> None:
    """No provider is configured, which is the harshest version of a plan failure.

    A child who tapped the star must get a story, not a 502 — and the beats are
    generated *against* the plan rather than dictated by it, so a generic arc told
    in their own world is a far better outcome than an error screen. The fallback
    is recorded on the row and logged, so it is visible to an operator even though
    it is invisible to the child.
    """
    with caplog.at_level("ERROR", logger="apps.story.orchestrator"):
        adventure = orchestrator.create_adventure(frog_lover)

    assert adventure.plan["source"] == "fallback"
    assert adventure.plan["skeleton"] == list(orchestrator.FALLBACK_SKELETON)
    # Built around what the child loves even in the degraded path.
    assert "frogs" in adventure.plan["title"]
    assert "story.plan_failed" in caplog.text
    assert 5 <= adventure.estimated_beats <= 7


def test_estimated_beats_is_clamped_to_a_believable_arc(frog_lover) -> None:
    adventure = Adventure(child=frog_lover, plan={"skeleton": ["one", "two"]})
    # A two-step plan must not make a child's story look nearly over before it
    # starts — `estimatedBeats` is a promise made to a client (§18.4).
    assert adventure.estimated_beats == 5

    adventure.plan = {"skeleton": ["x"] * 20}
    assert adventure.estimated_beats == 7


# ---------------------------------------------------------------------------
# Beats (§8.1, §19.6)
# ---------------------------------------------------------------------------
def test_a_beat_is_persisted_with_its_slices_split_out(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)

    assert beat.seq == 1
    assert "frogs" in beat.narration
    assert beat.scene["illustration_prompt"]
    assert (beat.interaction or {})["type"] == "count"
    assert (beat.learning or {})["objective_id"] == "count_to_3"
    assert beat.emotion == "playful_curious"
    # Unchecked, and it says so: the runtime output classifier is Day 7, and a
    # column with no unchecked state would make every unmoderated beat look
    # moderated.
    assert beat.safety_status == "unchecked"

    adventure.refresh_from_db()
    assert adventure.emotion_arc == ["playful_curious"]


def test_beats_are_numbered_sequentially(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)

    assert orchestrator.create_beat(adventure).seq == 1
    assert orchestrator.create_beat(adventure).seq == 2

    adventure.refresh_from_db()
    assert adventure.emotion_arc == ["playful_curious", "playful_curious"]


def test_the_spoken_line_is_the_narration_plus_the_invitation(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)

    line = orchestrator.spoken_line(beat)

    # One clip, therefore one TTS call, and no silence where the child is being
    # asked a question.
    assert line.startswith(beat.narration)
    assert line.endswith("How many can you count?")


def test_a_beat_with_no_provider_raises_rather_than_inventing_one(
    frog_lover, fake_ai, monkeypatch
) -> None:
    """The plan degrades; a beat does not.

    A generic *arc* is still this child's story. A generic *beat* would be words
    nobody generated, so the failure travels to the stream, which ends gracefully
    (Day 7 replaces the silence with a safe fallback beat).
    """
    adventure = orchestrator.create_adventure(frog_lover)
    monkeypatch.setattr(
        orchestrator.ai_router.ModelRouter,
        "from_settings",
        classmethod(lambda cls: orchestrator.ai_router.ModelRouter(providers=[])),
    )
    with pytest.raises(AIConfigurationError):
        orchestrator.create_beat(adventure)


# ---------------------------------------------------------------------------
# Media, in parallel (§16.4, §18.5)
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_two_media_stages_actually_overlap(frog_lover, fake_ai) -> None:
    """The claim in `stream_media`'s docstring, measured.

    The offline adapters are fast, so each stage is padded with a deliberate sleep.
    If the two were awaited in turn — or run on a thread-sensitive executor, which
    is the realistic way this regresses — the wall time would be the sum.
    """
    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)

    delay = 0.4
    real_illustrate = orchestrator.illustrate_beat
    real_narrate = orchestrator.narrate_beat

    def slow(fn):
        def wrapped(target):
            time.sleep(delay)
            return fn(target)

        return wrapped

    orchestrator.MEDIA_STAGES = (  # type: ignore[misc]
        ("illustration", "media", slow(real_illustrate)),
        ("voice", "audio", slow(real_narrate)),
    )
    try:
        started = time.monotonic()
        results = async_to_sync(orchestrator.attach_media)(beat)
        wall = time.monotonic() - started
    finally:
        orchestrator.MEDIA_STAGES = (  # type: ignore[misc]
            ("illustration", "media", real_illustrate),
            ("voice", "audio", real_narrate),
        )

    assert results["illustration"] is not None
    assert results["voice"] is not None
    # Serial would be at least 2 * delay. The margin is generous because this is
    # a wall-clock assertion on a shared CI runner; the gap it is detecting is a
    # factor of two, not a few milliseconds.
    assert wall < delay * 1.8, f"stages ran in sequence: {wall:.2f}s"

    beat.refresh_from_db()
    assert beat.media_id and beat.audio_id


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_media_failure_leaves_the_beat_tellable(frog_lover, fake_ai, caplog) -> None:
    """A beat with narration and no picture is a story the child can still hear.

    A beat that raised would be a story that stopped, and media is the most likely
    thing in this pipeline to fail.
    """
    from apps.media.exceptions import MediaProviderError

    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)

    real_illustrate = orchestrator.illustrate_beat

    def explode(_target):
        raise MediaProviderError("the image provider is having a day")

    orchestrator.MEDIA_STAGES = (  # type: ignore[misc]
        ("illustration", "media", explode),
        ("voice", "audio", orchestrator.narrate_beat),
    )
    try:
        with caplog.at_level("ERROR", logger="apps.story.orchestrator"):
            results = async_to_sync(orchestrator.attach_media)(beat)
    finally:
        orchestrator.MEDIA_STAGES = (  # type: ignore[misc]
            ("illustration", "media", real_illustrate),
            ("voice", "audio", orchestrator.narrate_beat),
        )

    assert results["illustration"] is None
    assert results["voice"] is not None
    assert "story.media_failed" in caplog.text

    beat.refresh_from_db()
    assert beat.media_id is None
    assert beat.audio_id is not None


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_opening_beat_is_replayed_rather_than_regenerated(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)
    first = async_to_sync(orchestrator.ensure_first_beat)(adventure)
    calls = fake_ai.beat_calls

    second = async_to_sync(orchestrator.ensure_first_beat)(adventure)

    assert second[0].id == first[0].id
    assert fake_ai.beat_calls == calls


def test_a_beat_that_loses_the_position_race_adopts_the_winner(
    frog_lover, fake_ai, monkeypatch, caplog
) -> None:
    """Two generations aimed at the same position, forced to collide.

    A reload mid-generation is the ordinary way two streams end up writing beat 1
    of one adventure. `_next_seq` is pinned here so the collision is deterministic
    rather than a matter of which thread wins; the unique constraint settles it,
    and the loser adopts the winner's beat so the child is never told the opening
    twice.
    """
    adventure = orchestrator.create_adventure(frog_lover)
    monkeypatch.setattr(orchestrator, "_next_seq", lambda _adventure: 1)

    first = orchestrator.create_beat(adventure)
    with caplog.at_level("INFO", logger="apps.story.orchestrator"):
        second = orchestrator.create_beat(adventure)

    assert second.id == first.id
    assert adventure.beats.count() == 1
    assert "story.beat_race_lost" in caplog.text


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_streams_on_one_adventure_produce_one_opening_beat(frog_lover, fake_ai) -> None:
    """Two clients opening the same stream at once.

    Whichever way the two interleave — both finding nothing and racing to write,
    or the second finding the first's beat and replaying it — the invariant is the
    same and it is the one the child experiences: one opening.
    """
    adventure = orchestrator.create_adventure(frog_lover)

    async def both():
        return await asyncio.gather(
            orchestrator.ensure_first_beat(adventure),
            orchestrator.ensure_first_beat(adventure),
        )

    (left, *_), (right, *_) = async_to_sync(both)()

    assert left.id == right.id
    assert adventure.beats.count() == 1
