"""The counting contract: does a beat agree with itself before a child sees it?

The regression these exist for is a real one and worth naming: a beat asked "how
many frogs?", recorded `expected` of 2, and told the illustrator to draw three.
The child counted what was in front of them, got three, and was quietly recorded
as having answered wrongly — which then moved their difficulty. Every test below
is a way that beat could have been built.

No database and no model calls: `coherence` reads a contract and a demand and
returns strings, which is the whole reason it is a separate module from the
orchestrator that calls it.
"""

from __future__ import annotations

import pytest

from apps.learning import demands as learning_demands
from apps.learning.demands import Demand
from apps.story import coherence
from apps.story.ai.schemas import (
    Beat as BeatContract,
)
from apps.story.ai.schemas import (
    ChoiceOption,
    Interaction,
    InteractionType,
    Learning,
    Scene,
)

pytestmark = pytest.mark.django_db(transaction=False)


def count_demand(quantity: int = 3) -> Demand:
    return Demand(
        objective_code="count_to_5",
        domain="numeracy",
        difficulty=0.4,
        kind=learning_demands.COUNT,
        quantity=quantity,
        gap=0,
        choice_count=2,
        scaffold=False,
        stretch=False,
        novel_word=False,
    )


def compare_demand(more: int = 5, gap: int = 2) -> Demand:
    return Demand(
        objective_code="compare_groups",
        domain="numeracy",
        difficulty=0.5,
        kind=learning_demands.COMPARE,
        quantity=more,
        gap=gap,
        choice_count=2,
        scaffold=False,
        stretch=False,
        novel_word=False,
    )


def contract(
    *,
    illustration_prompt: str,
    interaction_type: InteractionType = InteractionType.COUNT,
    expected: int | str | None = 3,
    choices: list[ChoiceOption] | None = None,
    narration: str = "The log was warm and somebody was already sitting on it.",
) -> BeatContract:
    return BeatContract(
        beat_id="b3",
        narration=narration,
        scene=Scene(
            setting="A mossy log at the edge of the pond.",
            illustration_prompt=illustration_prompt,
            characters_present=["pip"],
            new_characters=[],
        ),
        interaction=Interaction(
            type=interaction_type,
            prompt_audio="How many can you see?",
            expected=expected,
            choices=choices,
        ),
        learning=Learning(
            objective_id="count_to_5",
            difficulty=0.4,
            skill_evidence_key="count_to_5",
        ),
        emotion="calm",
        continuity_refs=[],
        next_hint="They hop away together.",
    )


# ---------------------------------------------------------------------------
# Reading the quantity out of a prompt
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("prompt", "want"),
    [
        ("exactly 3 (three) green frogs on a log", {3}),
        ("exactly three green frogs on a log", {3}),
        ("EXACTLY 7 pebbles", {7}),
        ("exactly 5 (five) shells beside exactly 2 (two) stones", {2, 5}),
        ("exactly 10 (ten) fireflies", {10}),
        ("three green frogs on a log", set()),
        ("", set()),
    ],
)
def test_it_reads_only_quantities_the_prompt_commits_to(prompt: str, want: set[int]) -> None:
    """`exactly` is the contract, and a bare number is deliberately not counted.

    "three frogs beside two stones" names two numbers and commits to neither as
    *the* count; guessing which one the child is meant to count is exactly the
    ambiguity the required literal exists to remove.
    """
    assert coherence.stated_quantities(prompt) == want


# ---------------------------------------------------------------------------
# The counting contract
# ---------------------------------------------------------------------------
def test_a_beat_that_agrees_with_itself_has_no_problems() -> None:
    beat = contract(illustration_prompt="exactly 3 (three) round green frogs in a row")
    assert coherence.count_problems(beat, count_demand(3)) == ()


def test_the_original_bug_the_picture_and_the_question_disagree() -> None:
    """The reported defect, as a test: three in the picture, two in the question."""
    beat = contract(
        illustration_prompt="exactly 3 (three) round green frogs in a row",
        expected=2,
    )
    problems = coherence.count_problems(beat, count_demand(2))
    assert coherence.ILLUSTRATION_CONFLICT in problems


def test_an_expected_that_ignores_the_engines_number_is_caught() -> None:
    beat = contract(illustration_prompt="exactly 3 (three) frogs", expected=4)
    assert coherence.EXPECTED_MISMATCH in coherence.count_problems(beat, count_demand(3))


def test_an_illustration_that_never_says_how_many_is_caught() -> None:
    """The failure mode with no visible symptom until a child is looking at it.

    Nothing in the beat is wrong; the illustrator simply was not told a number, so
    what it draws is whatever it felt like, and the question is a coin flip.
    """
    beat = contract(illustration_prompt="some round green frogs sitting on a log")
    assert coherence.ILLUSTRATION_SILENT in coherence.count_problems(beat, count_demand(3))


def test_a_second_group_in_the_prompt_is_caught() -> None:
    beat = contract(illustration_prompt="exactly 3 (three) frogs near exactly 2 (two) other frogs")
    assert coherence.ILLUSTRATION_CONFLICT in coherence.count_problems(beat, count_demand(3))


def test_a_string_expected_is_not_a_count() -> None:
    beat = contract(illustration_prompt="exactly 3 (three) frogs", expected="three")
    assert coherence.EXPECTED_NOT_A_NUMBER in coherence.count_problems(beat, count_demand(3))


def test_a_beat_that_did_not_take_the_counting_ask_is_not_this_checks_business() -> None:
    """A `choose` beat under a counting demand is a missed objective, not a lie.

    `learning_tag_for` already records the objective as unexercised. Reporting it
    here as well would spend the rewrite budget regenerating a beat that is
    perfectly coherent.
    """
    beat = contract(
        illustration_prompt="a quiet pond with nobody about",
        interaction_type=InteractionType.NONE,
        expected=None,
    )
    assert coherence.count_problems(beat, count_demand(3)) == ()


def test_no_demand_means_nothing_to_disagree_with() -> None:
    beat = contract(illustration_prompt="exactly 9 (nine) frogs", expected=2)
    assert coherence.problems_for(beat, None) == ()


# ---------------------------------------------------------------------------
# The comparison contract
# ---------------------------------------------------------------------------
def _two_options() -> list[ChoiceOption]:
    return [
        ChoiceOption(key="shells", label="The pile of shells", icon_prompt="a pile of shells"),
        ChoiceOption(key="stones", label="The pile of stones", icon_prompt="a pile of stones"),
    ]


def test_a_comparison_that_agrees_with_itself_has_no_problems() -> None:
    beat = contract(
        illustration_prompt="exactly 5 (five) shells beside exactly 3 (three) stones",
        interaction_type=InteractionType.CHOOSE,
        expected="shells",
        choices=_two_options(),
    )
    assert coherence.compare_problems(beat, compare_demand(5, 2)) == ()


def test_a_comparison_whose_answer_names_no_card_is_caught() -> None:
    beat = contract(
        illustration_prompt="exactly 5 (five) shells beside exactly 3 (three) stones",
        interaction_type=InteractionType.CHOOSE,
        expected="the_bigger_one",
        choices=_two_options(),
    )
    problems = coherence.compare_problems(beat, compare_demand(5, 2))
    assert coherence.EXPECTED_NOT_A_CHOICE in problems


def test_a_comparison_drawn_with_the_wrong_group_sizes_is_caught() -> None:
    beat = contract(
        illustration_prompt="exactly 5 (five) shells beside exactly 4 (four) stones",
        interaction_type=InteractionType.CHOOSE,
        expected="shells",
        choices=_two_options(),
    )
    problems = coherence.compare_problems(beat, compare_demand(5, 2))
    assert coherence.ILLUSTRATION_CONFLICT in problems


# ---------------------------------------------------------------------------
# The repairs
# ---------------------------------------------------------------------------
def test_aligning_moves_the_question_to_the_picture() -> None:
    beat = contract(illustration_prompt="exactly 3 (three) frogs", expected=2)
    aligned = coherence.align_to_illustration(beat)
    assert aligned is not None
    assert aligned.interaction.expected == 3
    # The story itself is untouched — only the number the child is asked for moved.
    assert aligned.narration == beat.narration
    assert aligned.scene.illustration_prompt == beat.scene.illustration_prompt


def test_aligning_refuses_when_the_picture_commits_to_no_single_number() -> None:
    beat = contract(illustration_prompt="exactly 3 (three) frogs and exactly 2 (two) frogs")
    assert coherence.align_to_illustration(beat) is None


def test_aligning_refuses_when_the_picture_names_no_number_at_all() -> None:
    assert coherence.align_to_illustration(contract(illustration_prompt="some frogs")) is None


def test_demoting_leaves_a_beat_that_is_only_a_story() -> None:
    beat = contract(illustration_prompt="some frogs", expected=3)
    demoted = coherence.demote(beat)
    assert demoted.interaction.type == InteractionType.NONE
    assert demoted.interaction.expected is None
    assert demoted.interaction.choices is None
    assert demoted.narration == beat.narration


# ---------------------------------------------------------------------------
# The constraint a regeneration carries
# ---------------------------------------------------------------------------
def test_the_constraint_names_the_defect_and_never_quotes_the_beat() -> None:
    """§13.2's rule, which applies here for the same reason it applies to safety.

    A retry handed the offending text is a retry invited to copy it.
    """
    problems = (coherence.EXPECTED_MISMATCH, coherence.ILLUSTRATION_CONFLICT)
    constraint = coherence.constraint_for(problems)
    assert "interaction.expected" in constraint
    assert "exactly N" in constraint
    assert "frogs" not in constraint


def test_the_constraint_is_stable_for_the_same_defects() -> None:
    """Two orderings of the same problems produce the same instruction."""
    forward = coherence.constraint_for((coherence.EXPECTED_MISMATCH, coherence.ILLUSTRATION_SILENT))
    backward = coherence.constraint_for(
        (coherence.ILLUSTRATION_SILENT, coherence.EXPECTED_MISMATCH)
    )
    assert forward == backward


# ---------------------------------------------------------------------------
# Which problems are worth a regeneration
# ---------------------------------------------------------------------------
def test_a_silent_illustration_is_a_risk_and_not_a_contradiction() -> None:
    """The calibration the whole module turns on.

    A prompt that never says how many to draw may still be drawn correctly. If
    that were treated the same as a genuine disagreement, every counting beat
    whose writer merely forgot the literal would cost a regeneration and then be
    demoted to a beat that asks nothing — the objective dropped and the
    difficulty loop starved, to fix something that might not have been broken.
    """
    beat = contract(illustration_prompt="some round green frogs", expected=3)
    problems = coherence.count_problems(beat, count_demand(3))

    assert coherence.ILLUSTRATION_SILENT in problems
    assert coherence.contradictions(problems) == ()


def test_a_disagreement_is_always_worth_a_regeneration() -> None:
    beat = contract(illustration_prompt="exactly 4 (four) frogs", expected=3)
    problems = coherence.count_problems(beat, count_demand(3))

    assert coherence.ILLUSTRATION_CONFLICT in coherence.contradictions(problems)


def test_silence_alongside_a_disagreement_still_regenerates() -> None:
    """A beat can be both under-specified and wrong; the wrong half decides."""
    beat = contract(illustration_prompt="some frogs", expected="three")
    problems = coherence.count_problems(beat, count_demand(3))

    assert coherence.ILLUSTRATION_SILENT in problems
    assert coherence.contradictions(problems) == (coherence.EXPECTED_NOT_A_NUMBER,)
