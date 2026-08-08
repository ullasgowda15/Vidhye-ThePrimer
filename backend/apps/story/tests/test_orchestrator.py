"""The orchestrator's own guarantees (PRODUCT.md §8.1, §8.5, §8.8, §19.1, §19.6).

The API-level tests in `tests/test_adventures.py` and `tests/test_choices.py` prove
the endpoints. These prove the parts of the orchestrator that are hard to see from
outside it: what actually goes into a prompt, what happens when the plan call
fails, that the media stages really do overlap rather than merely being written as
if they do, and — from Day 5 — the rules that hold a branching story together.

Those last ones are worth naming, because each is a rule the *product* has rather
than a behaviour the code happens to have: the last beat always ends, no option
can lose, the child's position is their progress and not the newest row, and a
branch is never guessed at before the child has taken it.
"""

import asyncio
import datetime
import json
import logging
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


@pytest.fixture(autouse=True)
def no_media_worker(monkeypatch) -> None:
    """Keep the beat-media task out of these tests by default.

    This file is about *generating* — prompts, plans, beats, and the two media
    stages themselves. Handing every persisted beat to a worker as well would
    attach media behind the back of the tests that assert on its absence (a failed
    illustration must leave `beat.media_id` null) and would put traffic on a broker
    the suite does not own. The scheduling rules have their own tests below, which
    opt back in explicitly.
    """
    monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)


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


class TestPromptAudioIsNotTheNarrationAgain:
    """The contract says `prompt_audio` is the short question asked *after* the
    narration. Providers fill it with the narration over again — "restate the
    scene, then ask" is a natural way to write a prompt — and the result reached a
    child: the companion said the whole beat twice in one clip, and the same
    sentences were captioned twice on the page.

    Normalised where a model's output becomes our data, so the voice, the caption,
    the transcript and Day 5's memory all read one clean record.
    """

    def test_an_exact_repeat_is_dropped(self) -> None:
        narration = "Hi, Spidey! We are in a bright jungle! Do you want to walk or look?"
        interaction = {"type": "choose", "prompt_audio": narration}

        assert orchestrator.dedupe_prompt_audio(narration, interaction)["prompt_audio"] is None

    def test_case_and_spacing_do_not_hide_a_repeat(self) -> None:
        narration = "Look, three frogs!\nHow many can you count?"
        interaction = {"prompt_audio": "look, three   frogs! HOW MANY CAN YOU COUNT?"}

        assert orchestrator.dedupe_prompt_audio(narration, interaction)["prompt_audio"] is None

    def test_a_question_already_inside_the_narration_is_dropped(self) -> None:
        # The narration ends by asking, and the field repeats just that sentence.
        narration = "Pip peeks over the log. How many frogs can you count?"
        interaction = {"prompt_audio": "How many frogs can you count?"}

        assert orchestrator.dedupe_prompt_audio(narration, interaction)["prompt_audio"] is None

    def test_a_real_question_survives(self) -> None:
        # The property that matters most: a lost question is a child asked nothing,
        # which is worse than one asked clumsily. Only containment is dropped.
        narration = "Pip peeks over the log. Three little frogs sit in a row!"
        interaction = {"prompt_audio": "How many can you count?"}

        kept = orchestrator.dedupe_prompt_audio(narration, interaction)
        assert kept["prompt_audio"] == "How many can you count?"

    def test_a_paraphrase_is_left_alone_on_purpose(self) -> None:
        # A similarity threshold would eventually swallow a legitimate question.
        # The prompt and the field description discourage the paraphrase; this
        # function only catches the repeat.
        narration = "Three frogs sit on the log."
        interaction = {"prompt_audio": "Can you count the frogs on that log?"}

        assert orchestrator.dedupe_prompt_audio(narration, interaction)["prompt_audio"] is not None

    def test_nothing_to_do_when_there_is_no_question(self) -> None:
        for prompt in (None, "", "   "):
            interaction = {"type": "none", "prompt_audio": prompt}
            assert orchestrator.dedupe_prompt_audio("A quiet beat.", interaction) == interaction

    def test_the_input_is_not_modified(self) -> None:
        interaction = {"prompt_audio": "Same words."}
        orchestrator.dedupe_prompt_audio("Same words.", interaction)

        assert interaction["prompt_audio"] == "Same words."

    def test_a_repeating_provider_cannot_reach_the_child(self, frog_lover, fake_ai, caplog) -> None:
        """End to end, through the real router and the real write path."""
        beat_json = fake_ai.beat("loves frogs.")
        beat_json["interaction"]["prompt_audio"] = beat_json["narration"]
        fake_ai.queue_beat_response(json.dumps(beat_json))

        adventure = orchestrator.create_adventure(frog_lover)
        with caplog.at_level(logging.INFO):
            beat = orchestrator.create_beat(adventure)

        assert beat.interaction is not None
        assert beat.interaction["prompt_audio"] is None
        # The clip is one TTS call over `spoken_line`, so this is what the child
        # would have heard twice in a row.
        assert orchestrator.spoken_line(beat) == beat.narration
        assert "story.prompt_audio_deduped" in caplog.text


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
def test_the_two_media_stages_actually_overlap(frog_lover, fake_ai, monkeypatch) -> None:
    """The claim in `stream_media`'s docstring, measured.

    The offline adapters are fast, so each stage is padded with a deliberate sleep.
    If the two were awaited in turn — or run on a thread-sensitive executor, which
    is the realistic way this regresses — the wall time would be the sum.

    The producers are replaced on the module rather than the stage list, because
    `media_stages` looks them up at call time precisely so a test can do this.
    """
    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)

    delay = 0.4

    def slow(fn):
        def wrapped(target):
            time.sleep(delay)
            return fn(target)

        return wrapped

    monkeypatch.setattr(orchestrator, "illustrate_beat", slow(orchestrator.illustrate_beat))
    monkeypatch.setattr(orchestrator, "narrate_beat", slow(orchestrator.narrate_beat))

    started = time.monotonic()
    results = async_to_sync(orchestrator.attach_media)(beat)
    wall = time.monotonic() - started

    assert results["illustration"] is not None
    assert results["voice"] is not None
    # Serial would be at least 2 * delay. The margin is generous because this is
    # a wall-clock assertion on a shared CI runner; the gap it is detecting is a
    # factor of two, not a few milliseconds.
    assert wall < delay * 1.8, f"stages ran in sequence: {wall:.2f}s"

    beat.refresh_from_db()
    assert beat.media_id and beat.audio_id


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_media_failure_leaves_the_beat_tellable(frog_lover, fake_ai, caplog, monkeypatch) -> None:
    """A beat with narration and no picture is a story the child can still hear.

    A beat that raised would be a story that stopped, and media is the most likely
    thing in this pipeline to fail.
    """
    from apps.media.exceptions import MediaProviderError

    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)

    def explode(_target):
        raise MediaProviderError("the image provider is having a day")

    monkeypatch.setattr(orchestrator, "illustrate_beat", explode)

    with caplog.at_level("ERROR", logger="apps.story.orchestrator"):
        results = async_to_sync(orchestrator.attach_media)(beat)

    assert results["illustration"] is None
    assert results["voice"] is not None
    assert "story.media_failed" in caplog.text

    beat.refresh_from_db()
    assert beat.media_id is None
    assert beat.audio_id is not None


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_opening_beat_is_replayed_rather_than_regenerated(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)
    first = async_to_sync(orchestrator.ensure_current_beat)(adventure)
    calls = fake_ai.beat_calls

    second = async_to_sync(orchestrator.ensure_current_beat)(adventure)

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
def test_two_streams_on_one_adventure_generate_the_opening_beat_once(frog_lover, fake_ai) -> None:
    """Two clients opening the same stream at once — a reload mid-generation, two
    devices, or React StrictMode mounting the effect twice in development.

    The invariant the child experiences is one opening, and the unique constraint
    always delivered that. What it could not deliver is the invariant the *bill*
    experiences: both callers could check, both find nothing, and both call the
    provider before either wrote, so one paid-for beat was generated and thrown
    away. The lock in `_first_beat_or_generate` is what closes that window, and
    `beat_calls` is the only assertion here that would have failed before it.
    """
    adventure = orchestrator.create_adventure(frog_lover)
    before = fake_ai.beat_calls

    async def both():
        return await asyncio.gather(
            orchestrator.ensure_current_beat(adventure),
            orchestrator.ensure_current_beat(adventure),
        )

    (left, *_), (right, *_) = async_to_sync(both)()

    assert left.id == right.id
    assert adventure.beats.count() == 1
    assert fake_ai.beat_calls - before == 1, "both streams called the provider"


# ---------------------------------------------------------------------------
# Resuming an adventure by name (§18.4, §14.5)
# ---------------------------------------------------------------------------
def test_an_in_progress_adventure_is_resumed_rather_than_replaced(frog_lover, fake_ai) -> None:
    first, created_first = orchestrator.resume_or_create_adventure(frog_lover)
    plans = fake_ai.plan_calls

    second, created_second = orchestrator.resume_or_create_adventure(frog_lover)

    assert created_first is True
    assert created_second is False
    assert second.id == first.id
    assert fake_ai.plan_calls == plans
    assert Adventure.objects.count() == 1


def test_only_an_in_progress_adventure_counts_as_current(frog_lover, fake_ai) -> None:
    adventure, _ = orchestrator.resume_or_create_adventure(frog_lover)
    assert orchestrator.current_adventure(frog_lover) == adventure

    for finished in (Adventure.Status.COMPLETED, Adventure.Status.ABANDONED):
        Adventure.objects.filter(pk=adventure.pk).update(status=finished)
        assert orchestrator.current_adventure(frog_lover) is None, finished


def test_one_child_s_story_is_never_another_child_s(frog_lover, fake_ai) -> None:
    import datetime as dt

    sibling = Child.objects.create(
        parent=frog_lover.parent,
        first_name="Nia",
        birthdate=dt.date(dt.date.today().year - 3, 2, 2),
    )
    mine, _ = orchestrator.resume_or_create_adventure(frog_lover)

    assert orchestrator.current_adventure(sibling) is None
    theirs, created = orchestrator.resume_or_create_adventure(sibling)
    assert created is True
    assert theirs.id != mine.id


# ---------------------------------------------------------------------------
# The warm close, and no way to lose (§8.5, §8.8)
# ---------------------------------------------------------------------------
class TestTheLastBeatIsAlwaysAnEnding:
    """§8.8: the last beat of every sitting is warm and safe.

    Two halves. The prompt has to *ask* for a close — a stateless call cannot know
    it is writing the last beat — and the write has to make the result presentable
    whatever came back, because there is no beat after it for an option to lead to.
    """

    def test_the_prompt_asks_for_a_close_and_a_hook(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        last = adventure.estimated_beats

        user = orchestrator.build_beat_context(adventure, seq=last)["user"]

        assert "last beat of the sitting" in user
        assert "hook for tomorrow" in user
        # And the beats before it are asked for the opposite.
        assert "last beat" not in orchestrator.build_beat_context(adventure, seq=2)["user"]

    def test_options_on_a_final_beat_are_dropped(self) -> None:
        # Cards on the close are cards that lead nowhere: the child taps one and
        # watches the story end on what looks like a mistake.
        interaction = {
            "type": "choose",
            "prompt_audio": "Shall we come back tomorrow?",
            "expected": None,
            "choices": [{"key": "yes", "label": "Yes!", "icon_prompt": "a sunrise"}],
        }

        closed = orchestrator.normalise_choices(interaction, is_final=True)

        assert closed["type"] == "none"
        assert closed["choices"] is None
        # The spoken warmth survives — it is not a question anyone has to answer.
        assert closed["prompt_audio"] == "Shall we come back tomorrow?"

    def test_a_model_that_will_not_stop_is_made_to(self, frog_lover, fake_ai) -> None:
        """End to end through the real write path, with a provider that ignores the
        instruction and offers choices on the close anyway."""
        adventure = orchestrator.create_adventure(frog_lover)
        last = adventure.estimated_beats
        beat_json = fake_ai.beat("loves frogs.")
        beat_json["interaction"] = {
            "type": "choose",
            "prompt_audio": "Which way now?",
            "expected": None,
            "choices": [{"key": "on", "label": "Keep going", "icon_prompt": "a path"}],
        }
        fake_ai.queue_beat_response(json.dumps(beat_json))

        beat = orchestrator.create_beat(adventure, seq=last)

        assert beat.options == []
        assert (beat.interaction or {})["type"] == "none"


class TestTheChoicesAChildIsOffered:
    def test_more_than_three_options_are_trimmed(self) -> None:
        # §14.5 caps a decision at two or three. Four cards on a tablet is four
        # things a four-year-old is holding at once.
        interaction = {
            "type": "choose",
            "choices": [
                {"key": f"o{index}", "label": f"Option {index}", "icon_prompt": "a path"}
                for index in range(5)
            ],
        }

        kept = orchestrator.normalise_choices(interaction, is_final=False)["choices"]

        assert len(kept) == orchestrator.MAX_CHOICES
        assert [option["key"] for option in kept] == ["o0", "o1", "o2"]

    def test_duplicate_keys_are_dropped_rather_than_merged(self) -> None:
        # Two cards that report the same tap are two cards the consequence cannot
        # tell apart — the child would pick one and see the other's outcome.
        interaction = {
            "type": "choose",
            "choices": [
                {"key": "river", "label": "The river", "icon_prompt": "water"},
                {"key": "river", "label": "The other river", "icon_prompt": "more water"},
            ],
        }

        kept = orchestrator.normalise_choices(interaction, is_final=False)["choices"]

        assert [option["label"] for option in kept] == ["The river"]

    def test_an_empty_choice_list_becomes_no_choices_at_all(self) -> None:
        # `[]` and `null` must not be two different things a client has to handle.
        assert orchestrator.normalise_choices({"choices": []}, is_final=False)["choices"] is None

    def test_the_options_are_read_out_in_the_same_clip(self, frog_lover, fake_ai) -> None:
        """A picture says one card is a river; only the voice says those are the two
        things you may pick between (§14.4)."""
        adventure = orchestrator.create_adventure(frog_lover)
        orchestrator.create_beat(adventure, seq=1)
        decision = orchestrator.create_beat(adventure, seq=2)

        line = orchestrator.spoken_line(decision)

        assert decision.options, "the scripted second beat is a decision point"
        for option in decision.options:
            assert option["label"] in line
        # One sentence, one TTS call, and it scans as a question.
        assert line.rstrip().endswith("?")
        assert " or " in line


class TestNoChoiceCanLose:
    def test_an_unrecognised_key_is_recorded_rather_than_refused(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        beat = orchestrator.create_beat(adventure, seq=1)

        choice, created = orchestrator.record_choice(beat, chosen_key="something_else", value=99)

        assert created is True
        assert choice.chosen_key == "something_else"
        assert choice.value == 99

    def test_the_consequence_summary_never_grades(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        beat = orchestrator.create_beat(adventure, seq=1)

        choice, _ = orchestrator.record_choice(
            beat, chosen_key="continue", value=7, retries=3, assisted=True
        )

        text = choice.consequence_summary.lower()
        assert "aarav chose" in text
        assert "helped them get there" in text
        for verdict in ("wrong", "incorrect", "failed", "correct", "right answer"):
            assert verdict not in text, verdict

    def test_a_second_answer_on_one_beat_adopts_the_first(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        beat = orchestrator.create_beat(adventure, seq=1)

        first, created_first = orchestrator.record_choice(beat, chosen_key="left")
        second, created_second = orchestrator.record_choice(beat, chosen_key="right")

        assert created_first is True
        assert created_second is False
        assert second.id == first.id
        assert second.chosen_key == "left"
        assert beat.choices.count() == 1


# ---------------------------------------------------------------------------
# Where the child is (§8.5)
# ---------------------------------------------------------------------------
class TestNextPosition:
    def test_a_fresh_adventure_starts_at_beat_one(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        assert orchestrator.next_position(adventure) == (1, None)

    def test_an_unanswered_beat_is_where_the_child_is(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        beat = orchestrator.create_beat(adventure, seq=1)

        seq, prev = orchestrator.next_position(adventure)

        assert (seq, prev) == (beat.seq, None)

    def test_answering_moves_the_child_on(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        beat = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(beat, chosen_key="continue")

        seq, prev = orchestrator.next_position(adventure)

        assert seq == 2
        assert prev is not None and prev.id == beat.id

    def test_a_beat_generated_ahead_of_the_child_does_not_move_them(
        self, frog_lover, fake_ai
    ) -> None:
        """The reason the cursor is progress and not the newest row.

        The prefetch writes beat two while the child is still on beat one. A cursor
        that meant "the last beat" would jump them straight to it — a beat they
        never played, and one they paid for.
        """
        adventure = orchestrator.create_adventure(frog_lover)
        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.create_beat(adventure, seq=2)

        seq, _ = orchestrator.next_position(adventure)

        assert seq == first.seq


class TestEndingAnAdventure:
    def test_ending_closes_the_story_and_the_sitting(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)

        ended = orchestrator.end_adventure(adventure)

        assert ended.status == Adventure.Status.COMPLETED
        assert ended.completed_at is not None
        session = ended.session
        assert session is not None
        assert session.ended_reason == "completed"
        assert session.duration_s is not None

    def test_leaving_early_is_abandoned_and_never_failed(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)

        ended = orchestrator.end_adventure(adventure, reason="exit")

        assert ended.status == Adventure.Status.ABANDONED
        assert ended.session is not None
        assert ended.session.ended_reason == "exit"

    def test_ending_is_idempotent(self, frog_lover, fake_ai, caplog) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        first = orchestrator.end_adventure(adventure)

        with caplog.at_level("INFO", logger="apps.story.orchestrator"):
            second = orchestrator.end_adventure(adventure, reason="exit")

        assert second.status == first.status
        assert second.completed_at == first.completed_at
        assert "story.end_noop" in caplog.text


# ---------------------------------------------------------------------------
# Prefetch (§4.9)
# ---------------------------------------------------------------------------
class TestSchedulingTheNextBeat:
    def test_a_settled_branch_is_queued(
        self, frog_lover, fake_ai, monkeypatch, django_capture_on_commit_callbacks
    ) -> None:
        queued: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "apps.story.tasks.advance_adventure.delay",
            lambda *args: queued.append(args),
        )
        adventure = orchestrator.create_adventure(frog_lover)
        beat = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(beat, chosen_key="continue")

        # Enqueued `on_commit`, like the media task and for the same reason: a
        # worker must not look up rows a transaction has not written yet. The test
        # therefore has to let the commit happen.
        with django_capture_on_commit_callbacks(execute=True):
            status = orchestrator.schedule_advance(adventure, after=beat)

        assert status == orchestrator.ADVANCE_QUEUED
        assert queued == [(str(adventure.id), str(beat.id))]

    def test_an_unanswered_decision_is_not_guessed(self, frog_lover, fake_ai) -> None:
        """Guessing a branch means paying for beats the child will never see, and a
        beat generated before `assisted` is known is a beat that ignores the child
        needing help (§8.5)."""
        adventure = orchestrator.create_adventure(frog_lover)
        beat = orchestrator.create_beat(adventure, seq=1)

        assert (
            orchestrator.schedule_advance(adventure, after=beat) == orchestrator.ADVANCE_BRANCHING
        )
        assert adventure.beats.count() == 1

    def test_the_close_has_nothing_after_it(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        last = orchestrator.create_beat(adventure, seq=adventure.estimated_beats)

        assert orchestrator.schedule_advance(adventure, after=last) == orchestrator.ADVANCE_FINAL

    def test_a_beat_already_written_is_reported_ready(self, frog_lover, fake_ai) -> None:
        adventure = orchestrator.create_adventure(frog_lover)
        beat = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(beat, chosen_key="continue")
        orchestrator.create_beat(adventure, seq=2)

        assert orchestrator.schedule_advance(adventure, after=beat) == orchestrator.ADVANCE_READY


# ---------------------------------------------------------------------------
# Handing the media to a worker (§16.3)
# ---------------------------------------------------------------------------
def test_a_persisted_beat_is_handed_to_the_media_worker(frog_lover, fake_ai, monkeypatch) -> None:
    """The enqueue is the whole fix for the orphaned beat, so it is asserted
    directly rather than inferred from the media turning up."""
    handed: list[Any] = []
    monkeypatch.setattr(orchestrator, "schedule_beat_media", handed.append)

    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)

    assert [b.id for b in handed] == [beat.id]


def test_pending_stages_name_what_is_missing(frog_lover, fake_ai) -> None:
    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)

    assert set(orchestrator.pending_media_stages(beat)) == {
        orchestrator.ILLUSTRATION_STAGE,
        orchestrator.VOICE_STAGE,
    }

    beat.audio = orchestrator.narrate_beat(beat)
    beat.save(update_fields=["audio", "updated_at"])
    assert orchestrator.pending_media_stages(beat) == (orchestrator.ILLUSTRATION_STAGE,)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_repair_run_only_does_the_stage_that_is_missing(frog_lover, fake_ai, monkeypatch) -> None:
    """Half a beat's media is the state a dead worker leaves behind.

    Finishing it must not re-run the half that landed: the media pipeline is
    cache-first so a re-run is cheap rather than free, and "cheap" multiplied by
    every reload is how the last cost leak got in.
    """
    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)
    beat.audio = orchestrator.narrate_beat(beat)
    beat.save(update_fields=["audio", "updated_at"])

    ran: list[str] = []

    def record(label, fn):
        def wrapped(target):
            ran.append(label)
            return fn(target)

        return wrapped

    monkeypatch.setattr(
        orchestrator, "illustrate_beat", record("illustration", orchestrator.illustrate_beat)
    )
    monkeypatch.setattr(orchestrator, "narrate_beat", record("voice", orchestrator.narrate_beat))

    async_to_sync(orchestrator.attach_media)(beat, stages=orchestrator.pending_media_stages(beat))

    assert ran == ["illustration"]
    beat.refresh_from_db()
    assert beat.media_id is not None and beat.audio_id is not None


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_media_task_finishes_a_beat_nobody_is_watching(frog_lover, fake_ai) -> None:
    """The task, called the way a worker calls it — no stream, no client."""
    from apps.story.tasks import generate_beat_media

    adventure = orchestrator.create_adventure(frog_lover)
    beat = orchestrator.create_beat(adventure)
    assert beat.media_id is None and beat.audio_id is None

    assert generate_beat_media(str(beat.id)) == "complete"

    beat.refresh_from_db()
    assert beat.media_id is not None
    assert beat.audio_id is not None
    # Idempotent: a second open queueing a repair for a beat that is already whole
    # must cost nothing at all.
    assert generate_beat_media(str(beat.id)) == "skipped"


def test_the_media_task_shrugs_at_a_deleted_beat(frog_lover, fake_ai, caplog) -> None:
    """The child was deleted while the task was queued. The deletion is the newer
    instruction, not an error to retry."""
    from apps.story.tasks import generate_beat_media

    with caplog.at_level("INFO", logger="apps.story.tasks"):
        assert generate_beat_media("00000000-0000-4000-8000-000000000000") == "missing"

    assert "story.media_task_skipped" in caplog.text
