"""The objective, embedded and adapting (PRODUCT.md §8.9, §8.10, §10.4).

Two acceptance criteria live here and they pull in opposite directions, which is
the whole difficulty of this day.

**Criterion 3 — invisible.** A reviewer reading a beat sees a story, not a lesson.
The child must finish believing they went somewhere.

**Criterion 4 — adapting.** The story must *react* to how the last ask went, within
the same sitting: a struggle brings the companion alongside, ease reaches further.

A system that did only the first would be a storybook. One that did only the
second would be a worksheet with pictures. These tests are what stop either from
being quietly true.
"""

from __future__ import annotations

import json

import pytest

from apps.learning import demands, engine
from apps.learning.demands import NO_OBJECTIVE
from apps.learning.tests.conftest import set_progress
from apps.story import orchestrator, streaming
from apps.story.ai.prompts import story as story_prompts
from apps.story.models import Beat

pytestmark = pytest.mark.django_db


def play_to(adventure, seq: int) -> Beat:
    """Generate and answer every beat up to `seq`, returning the last one.

    Answers are read off the beat rather than assumed, so this keeps working when
    the engine changes what it asks for.
    """
    previous = None
    for position in range(1, seq + 1):
        beat = orchestrator.create_beat(
            adventure, seq=position, prev_choice=orchestrator.turn_from(previous)
        )
        if position == seq:
            return beat
        key = next((option["key"] for option in beat.options), "continue")
        orchestrator.record_choice(
            beat, chosen_key=key, value=(beat.interaction or {}).get("expected")
        )
        previous = beat
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# The tag (§8.10, criterion 3)
# ---------------------------------------------------------------------------
def test_the_learning_beats_carry_the_tag_and_the_others_say_so(aarav, fake_ai, enqueued) -> None:
    adventure = orchestrator.create_adventure(aarav)

    previous = None
    for seq in range(1, adventure.estimated_beats + 1):
        beat = orchestrator.create_beat(
            adventure, seq=seq, prev_choice=orchestrator.turn_from(previous)
        )
        orchestrator.record_choice(
            beat,
            chosen_key=next((option["key"] for option in beat.options), "continue"),
            value=(beat.interaction or {}).get("expected"),
        )
        previous = beat

    carried = {
        beat.seq: (beat.learning or {})["objective_id"] for beat in adventure.beats.order_by("seq")
    }
    asked = orchestrator.learning_seqs(adventure)

    assert asked, "an adventure with an objective must ask something"
    for seq in asked:
        assert carried[seq] == adventure.primary_objective_id, seq
    for seq, code in carried.items():
        if seq not in asked:
            assert code == NO_OBJECTIVE, seq


def test_the_tag_is_stamped_by_the_engine_and_not_by_the_model(aarav, fake_ai, enqueued) -> None:
    """§8.10's tag exists so we can verify the objective was exercised.

    A tag whose objective and difficulty were self-reported by the thing being
    verified verifies nothing — and `difficulty` is read straight back by the
    mastery update as the difficulty the child faced, so a model's guess at it
    would corrupt the skill model directly (§10.5).
    """
    set_progress(aarav, "count_to_3", mastery=0.85, practised_days_ago=40)
    set_progress(aarav, "count_to_5", mastery=0.5, practised_days_ago=40, due_in_days=20)
    adventure = orchestrator.create_adventure(aarav)
    seq = orchestrator.learning_seqs(adventure)[0]

    # Everything up to the learning beat, played normally, so the lie below is
    # consumed by the beat it is meant for and not by the opening.
    play_to(adventure, seq - 1)
    previous = adventure.beats.get(seq=seq - 1)
    orchestrator.record_choice(previous, chosen_key="follow_river")

    demand = orchestrator.learning_ask_at(adventure, seq)
    assert demand is not None
    lie = fake_ai.beat(
        "", f"Write beat {seq} of this adventure.\n\n{story_prompts.learning_rule(demand)}"
    )
    lie["learning"] = {
        "objective_id": "banana_studies",
        "difficulty": 0.99,
        "skill_evidence_key": "counted_the_lanterns",
    }
    fake_ai.queue_beat_response(json.dumps(lie))

    beat = orchestrator.create_beat(
        adventure, seq=seq, prev_choice=orchestrator.turn_from(previous)
    )

    tag = beat.learning or {}
    assert tag["objective_id"] == adventure.primary_objective_id == "count_to_5"
    assert tag["difficulty"] == pytest.approx(demand.difficulty, abs=0.01)
    # The one field the writer *is* in a position to know is kept as written.
    assert tag["skill_evidence_key"] == "counted_the_lanterns"


# ---------------------------------------------------------------------------
# Invisible (criterion 3)
# ---------------------------------------------------------------------------
def test_nothing_the_child_receives_mentions_learning(aarav, fake_ai, enqueued) -> None:
    """The client's own view of a learning beat (§18.5).

    This is the assertion closest to the child: whatever the engine decided, the
    frame their tablet is sent has no objective in it, no difficulty, no mastery
    and no score. There is nowhere for a lesson to leak through.
    """
    adventure = orchestrator.create_adventure(aarav)
    beat = play_to(adventure, orchestrator.learning_seqs(adventure)[0])

    payload = streaming.beat_payload(beat, urls={}, is_final=False)
    body = json.dumps(payload).lower()

    assert "learning" not in payload
    for banned in ("objective", "difficulty", "mastery", "score", "correct", "lesson"):
        assert banned not in body, banned


def test_a_reader_of_the_beat_sees_a_story(aarav, fake_ai, enqueued) -> None:
    adventure = orchestrator.create_adventure(aarav)
    beat = play_to(adventure, orchestrator.learning_seqs(adventure)[0])

    spoken = f"{beat.narration} {(beat.interaction or {}).get('prompt_audio') or ''}".lower()

    assert spoken.strip()
    for banned in (
        "objective",
        "learning",
        "practise",
        "practice",
        "lesson",
        "test",
        "quiz",
        "score",
        "skill",
        "correct",
        "wrong",
    ):
        assert banned not in spoken, banned


def test_the_instruction_asks_for_a_number_and_never_names_the_skill(
    aarav, fake_ai, enqueued
) -> None:
    """The user turn a learning beat is written from (§8.10).

    It carries the *count*, because that is what makes the objective exercisable.
    It must not carry the objective's code or the difficulty float, because those
    are the two things a model tends to repeat back into narration.
    """
    set_progress(aarav, "count_to_3", mastery=0.85, practised_days_ago=40)
    set_progress(aarav, "count_to_5", mastery=0.5, practised_days_ago=40, due_in_days=20)
    adventure = orchestrator.create_adventure(aarav)
    seq = orchestrator.learning_seqs(adventure)[0]

    user = orchestrator.build_beat_context(adventure, seq=seq)["user"]
    demand = orchestrator.learning_ask_at(adventure, seq)

    assert demand is not None
    assert f"interaction.expected to {demand.quantity}" in user
    assert "count_to_5" not in user
    assert str(adventure.difficulty) not in user


def test_the_directive_layer_names_the_objective_and_forbids_saying_it(
    aarav, fake_ai, enqueued
) -> None:
    """The model has to be told what to teach; the child must never hear it."""
    adventure = orchestrator.create_adventure(aarav)
    system = orchestrator.build_beat_context(adventure, seq=2)["system"]

    assert adventure.primary_objective_id in system
    assert "Embed it invisibly" in system
    assert "never name the skill" in system


# ---------------------------------------------------------------------------
# Invisibility, watched in production rather than only in fixtures (§8.10)
# ---------------------------------------------------------------------------
# The tests above prove the *pipeline* injects no lesson language, and they prove
# it against a provider that was written to avoid it. They cannot prove a real
# model — handed `Primary objective: count_to_5` and `Target difficulty: 0.6` in
# its system layer — never says either out loud. `_log_lesson_leakage` is the
# check that watches the real ones; these are the tests that it fires, and that it
# does nothing else.
def test_a_beat_that_names_the_lesson_is_reported(mid_level, fake_ai, enqueued, caplog) -> None:
    """A leaky beat warns — and is still delivered exactly as written.

    Both halves matter. A probe that did not fire would leave the day's headline
    guarantee unobserved outside fixtures; a probe that blocked would turn a beat
    that is merely clumsy into a child waiting at a dead screen (§13.2 keeps
    blocking for safety and measurement for quality).
    """
    adventure = orchestrator.create_adventure(mid_level)
    seq = orchestrator.learning_seqs(adventure)[0]
    play_to(adventure, seq - 1)
    previous = adventure.beats.get(seq=seq - 1)
    orchestrator.record_choice(previous, chosen_key="follow_river")

    demand = orchestrator.learning_ask_at(adventure, seq)
    assert demand is not None

    leaky = fake_ai.beat(
        "", f"Write beat {seq} of this adventure.\n\n{story_prompts.learning_rule(demand)}"
    )
    narration = (
        f"Time to practise counting, said Pip — this is a {demand.objective_code} lesson "
        f"at difficulty {round(demand.difficulty, 2)}."
    )
    leaky["narration"] = narration
    fake_ai.queue_beat_response(json.dumps(leaky))

    with caplog.at_level("WARNING", logger="apps.story.orchestrator"):
        beat = orchestrator.create_beat(
            adventure, seq=seq, prev_choice=orchestrator.turn_from(previous)
        )

    leaks = [record for record in caplog.records if "story.lesson_language" in record.getMessage()]
    assert leaks, "a beat that says 'practise' and names the objective must be reported"
    reported = leaks[0].getMessage()
    for expected in ("practice", "lesson", demand.objective_code, str(round(demand.difficulty, 2))):
        assert expected in reported, expected

    # Warn-only: the beat the child receives is the one the model wrote.
    assert beat.narration == narration


def test_a_clean_beat_is_reported_as_nothing(mid_level, fake_ai, enqueued, caplog) -> None:
    """The other half of a probe worth having: it has to be quiet when it should be."""
    adventure = orchestrator.create_adventure(mid_level)

    with caplog.at_level("WARNING", logger="apps.story.orchestrator"):
        beat = play_to(adventure, orchestrator.learning_seqs(adventure)[0])

    assert beat.narration.strip()
    assert not [
        record for record in caplog.records if "story.lesson_language" in record.getMessage()
    ]


def test_the_leakage_probe_reads_the_words_and_not_the_scenery() -> None:
    """A log line nobody trusts is a log line nobody reads.

    "Contest" is not "test" and a story about a treasure chest is not a lesson —
    the leading word boundary is what keeps the rate meaningful. The words it does
    catch are caught in the forms a model writes them in.
    """
    assert orchestrator.lesson_language("Pip and the frogs entered a contest by the pond") == []
    assert orchestrator.lesson_language("The chest under the reeds was shut tight") == []
    assert orchestrator.lesson_language("They scored the softest moss for the nest") == ["score"]
    assert orchestrator.lesson_language("Let us keep practising our counting") == ["practice"]
    assert orchestrator.lesson_language("Was that the correct answer?") == ["correct answer"]

    # The two strings the system layer actually hands the model (§8.10).
    assert (
        orchestrator.lesson_language(
            "Five frogs sat on the log", objective_code="count_to_5", difficulty=0.6
        )
        == []
    )
    assert orchestrator.lesson_language(
        "This is count_to_5 at 0.6", objective_code="count_to_5", difficulty=0.6
    ) == ["0.6", "count_to_5"]
    # `NO_OBJECTIVE` is the sentinel for a beat that practises nothing, not a code
    # a narration could leak — matching on it would flag the word "none".
    assert orchestrator.lesson_language("There were none left", objective_code=NO_OBJECTIVE) == []


# ---------------------------------------------------------------------------
# The objective is actually exercised (criterion 3's other half)
# ---------------------------------------------------------------------------
def test_the_learning_beat_asks_for_exactly_what_the_engine_chose(aarav, fake_ai, enqueued) -> None:
    """§8.9's translation, arriving intact at the child's screen.

    The engine picked a number, the rule carried it, the writer put that many
    things in the scene, and the beat asks for it. A break anywhere along that
    chain leaves the objective riding in the prompt as data nothing acted on —
    which is exactly the state Day 10 exists to end.
    """
    set_progress(aarav, "count_to_3", mastery=0.85, practised_days_ago=40)
    set_progress(aarav, "count_to_5", mastery=0.5, practised_days_ago=40, due_in_days=20)
    adventure = orchestrator.create_adventure(aarav)
    seq = orchestrator.learning_seqs(adventure)[0]
    demand = orchestrator.learning_ask_at(adventure, seq)

    beat = play_to(adventure, seq)

    assert demand is not None
    assert (beat.interaction or {})["type"] == "count"
    assert (beat.interaction or {})["expected"] == demand.quantity
    # A count beat shows the number pad, which the client picks by the *absence*
    # of cards — options here would leave the child no way to answer (§14.4).
    assert beat.options == []


def test_the_plan_is_built_around_the_objective(aarav, fake_ai, enqueued) -> None:
    """§8.10: the arc has to *contain* the objective, not have it bolted on.

    The plan call is the only place that decides there is a river with stepping
    stones in it. A beat handed "five countable things" against a skeleton that
    never mentioned any has to invent a reason for them mid-story.
    """
    orchestrator.create_adventure(aarav)
    system, user = fake_ai.prompts[0]

    assert "Plan today's adventure" in user
    assert "Primary objective: count_to_3" in system
    assert "countable at a glance" in system


# ---------------------------------------------------------------------------
# Within-session adaptation (criterion 4)
# ---------------------------------------------------------------------------
@pytest.fixture
def mid_level(aarav):
    """A child sitting squarely in the middle of the ladder, on `count_to_10`.

    Both directions have to be reachable for these tests to mean anything, and
    `count_to_10` is the only counting objective with headroom above five —
    `count_to_5` clamps a stretch at its own ceiling, which is correct behaviour
    and would make "ease reaches further" untestable if it were the fixture.
    """
    set_progress(aarav, "count_to_3", mastery=0.90, practised_days_ago=40, due_in_days=20)
    set_progress(aarav, "count_to_5", mastery=0.85, practised_days_ago=40, due_in_days=20)
    set_progress(aarav, "count_to_10", mastery=0.5, practised_days_ago=40, due_in_days=20)
    return aarav


def test_a_struggle_brings_the_companion_alongside_for_the_next_ask(
    mid_level, fake_ai, enqueued
) -> None:
    """Criterion 4, the scaffold half (§10.4).

    The child misses the count and the client records `assisted=True` — which is
    what the number pad already does on a first miss (`StoryPage.handleCount`).
    The *next* learning beat is then written with the companion counting along and
    with one card fewer under it.
    """
    adventure = orchestrator.create_adventure(mid_level)
    first, second = orchestrator.learning_seqs(adventure)

    beat = play_to(adventure, first)
    before = orchestrator.learning_ask_at(adventure, second)
    assert before is not None and not before.scaffold

    orchestrator.record_choice(
        beat,
        chosen_key="count",
        value=(beat.interaction or {})["expected"] + 1,
        retries=1,
        assisted=True,
    )

    after = orchestrator.learning_ask_at(adventure, second)
    assert after is not None
    assert after.scaffold, "a struggle must be met with company"
    assert after.difficulty < before.difficulty
    assert after.choice_count <= before.choice_count

    user = orchestrator.build_beat_context(adventure, seq=second)["user"]
    assert story_prompts.SCAFFOLD_DEMAND in user
    assert story_prompts.STRETCH_DEMAND not in user


def test_an_easy_answer_reaches_a_little_further(mid_level, fake_ai, enqueued) -> None:
    """Criterion 4, the stretch half (§10.4) — the half usually forgotten.

    A child who is never stretched is a child being bored on purpose.
    """
    adventure = orchestrator.create_adventure(mid_level)
    first, second = orchestrator.learning_seqs(adventure)

    beat = play_to(adventure, first)
    before = orchestrator.learning_ask_at(adventure, second)
    assert before is not None

    orchestrator.record_choice(
        beat,
        chosen_key="count",
        value=(beat.interaction or {})["expected"],
        latency_ms=1800,
    )

    after = orchestrator.learning_ask_at(adventure, second)
    assert after is not None
    assert after.stretch and not after.scaffold
    assert after.difficulty > before.difficulty
    assert after.quantity > before.quantity, "reaching further has to be visible in the story"

    user = orchestrator.build_beat_context(adventure, seq=second)["user"]
    assert story_prompts.STRETCH_DEMAND in user
    assert story_prompts.SCAFFOLD_DEMAND not in user


def test_one_wobble_never_drops_the_ask_by_more_than_a_single_rung() -> None:
    """A struggle is met, not punished (§8.5, §10.4).

    A child dropped from five things to two for one miss is a child watching the
    story visibly decide they could not manage it. `ADAPT_STEP` is smaller than a
    rung of `COUNT_LADDER` precisely so a single wobble can cross at most one
    boundary — and this is the assertion that keeps the two numbers in step if
    either is ever tuned.
    """
    rungs = [quantity for _, quantity in demands.COUNT_LADDER]

    for difficulty in [round(step * 0.05, 2) for step in range(21)]:
        before = demands.demand_for("count_to_10", "numeracy", difficulty)
        after = demands.demand_for(
            "count_to_10", "numeracy", difficulty - engine.ADAPT_STEP, scaffold=True
        )
        assert after.quantity <= before.quantity
        dropped = rungs.index(before.quantity) - rungs.index(after.quantity)
        assert dropped <= 1, f"{difficulty}: {before.quantity} -> {after.quantity}"


def test_a_second_struggle_does_make_the_ask_itself_smaller(mid_level, fake_ai, enqueued) -> None:
    """Company first, then a smaller ask — the ordering §10.4 implies.

    Two wobbles in a row is a child who is not merely having an off moment, and
    the story has to actually come down to meet them rather than keep asking the
    same thing more kindly.
    """
    adventure = orchestrator.create_adventure(mid_level)
    code = adventure.primary_objective_id or ""
    start = orchestrator.learning_ask_at(adventure, orchestrator.learning_seqs(adventure)[0])
    assert start is not None

    twice = demands.demand_for(
        code, "numeracy", start.difficulty - 2 * engine.ADAPT_STEP, scaffold=True
    )

    assert twice.quantity < start.quantity
    assert twice.scaffold


# ---------------------------------------------------------------------------
# Degrading well
# ---------------------------------------------------------------------------
def test_an_adventure_with_no_objective_still_tells_a_story(aarav, fake_ai, enqueued) -> None:
    """A missing objective must never be a missing beat (§13)."""
    from apps.learning.models import LearningProgress, Objective

    adventure = orchestrator.create_adventure(aarav)
    LearningProgress.objects.all().delete()
    Objective.objects.all().delete()

    beat = orchestrator.create_beat(adventure, seq=1)

    assert beat.narration
    assert (beat.learning or {})["objective_id"] == NO_OBJECTIVE
    assert orchestrator.learning_ask_at(adventure, 2) is None
    assert (
        "No learning objective is set"
        in orchestrator.build_beat_context(adventure, seq=2)["system"]
    )


def test_a_child_with_no_catalog_at_all_still_gets_an_adventure(aarav, fake_ai, enqueued) -> None:
    from apps.learning.models import Objective

    Objective.objects.all().delete()

    adventure = orchestrator.create_adventure(aarav)

    assert adventure.primary_objective_id is None
    assert orchestrator.create_beat(adventure, seq=1).narration
