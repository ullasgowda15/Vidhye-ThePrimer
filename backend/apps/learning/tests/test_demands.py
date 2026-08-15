"""Difficulty becoming a thing a story can contain (PRODUCT.md §8.9).

§8.9 names three translations and all three are asserted here: count to 3 vs.
count to 10, a 2-choice vs. a 3-choice decision, a familiar vs. a novel word.

Pure functions, no database, no model — which is the point of `demands` being its
own module. If these ever need a fixture, difficulty has stopped being a pure
function of the objective and the number, and the mastery update can no longer
reconstruct what a child was actually asked.
"""

from __future__ import annotations

import pytest

from apps.learning import demands


# ---------------------------------------------------------------------------
# §8.9's first translation: count to 3 vs. count to 10
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("difficulty", "quantity"),
    [(0.05, 2), (0.3, 3), (0.5, 5), (0.7, 7), (0.95, 10)],
)
def test_the_count_climbs_with_difficulty(difficulty: float, quantity: int) -> None:
    demand = demands.demand_for("count_to_10", "numeracy", difficulty)

    assert demand.kind == demands.COUNT
    assert demand.quantity == quantity


def test_an_objective_never_asks_for_more_than_its_own_ceiling() -> None:
    """`count_to_3` at the top of the range asks for three things, not ten.

    The objective names *what* is being practised; difficulty only says how hard
    to make that. Without the clamp, a child who got good at counting to three
    would be handed ten of something under a `count_to_3` tag — and the mastery
    update would then credit ten things to an objective about three.
    """
    assert demands.demand_for("count_to_3", "numeracy", 0.95).quantity == 3
    assert demands.demand_for("count_to_5", "numeracy", 0.95).quantity == 5


@pytest.mark.parametrize("code", sorted(demands.COUNT_CEILING))
def test_the_stamped_difficulty_describes_the_ask_that_was_actually_made(code: str) -> None:
    """The other half of the ceiling clamp, and the anchor of §10.5.

    `Demand.difficulty` is what the beat is stamped with and what the mastery
    update reads back as "the difficulty this child faced". So it has to be a
    difficulty the ladder would have answered with *this* ask: reading the ladder
    at the stamped number must give back the number of things the child was
    actually shown.

    Without the clamp `count_to_5` at 0.85 stamps 0.85 against five frogs — a
    difficulty the ladder answers with ten — and a clean win on those five frogs
    is credited as though ten had been counted.
    """
    for step in range(101):
        demand = demands.demand_for(code, "numeracy", step / 100)
        rung = demands._rung(demands.COUNT_LADDER, demand.difficulty)

        assert rung == demand.quantity, (
            f"{code} at {step / 100}: stamped {demand.difficulty} describes "
            f"{rung} things, but the child was shown {demand.quantity}"
        )


#: The counting objectives whose own ceiling sits below the ladder's top rung —
#: the only ones where the ask the ladder reached for and the ask the objective is
#: allowed to make can differ, and so the only ones where the anchor can drift.
CLAMPED_CODES = sorted(
    code for code, ceiling in demands.COUNT_CEILING.items() if ceiling < demands.COUNT_LADDER[-1][1]
)


@pytest.mark.parametrize("code", CLAMPED_CODES)
def test_the_same_ask_twice_is_the_same_difficulty_twice(code: str) -> None:
    """The invariant the clamp buys: an unchanged ask cannot be worth more.

    Once an objective's ceiling governs the ask, raising the difficulty changes
    nothing the child can see — so it must change nothing the skill model reads
    either. Every difficulty in the clamped region produces one quantity and one
    difficulty, which is what stops a child counting the same five frogs four
    times from climbing to mastery on the fourth.

    Below the ceiling the ladder's own bands are coarse and difficulty still
    varies inside them; that is the pre-existing granularity the bottom of the
    ladder is built on, and it is untouched here.
    """
    ceiling = demands.COUNT_CEILING[code]
    clamped = [
        demands.demand_for(code, "numeracy", step / 100)
        for step in range(101)
        if demands._rung(demands.COUNT_LADDER, step / 100) > ceiling
    ]

    assert clamped, f"{code} is never clamped — this test would prove nothing"
    assert {demand.quantity for demand in clamped} == {ceiling}
    assert len({demand.difficulty for demand in clamped}) == 1


def test_the_ceiling_only_ever_clamps_the_difficulty_downwards() -> None:
    """A clamp that could raise the stamped number would be the same bug, mirrored."""
    for code in demands.COUNT_CEILING:
        for step in range(101):
            requested = step / 100
            assert demands.demand_for(code, "numeracy", requested).difficulty <= requested


def test_the_smallest_ask_is_still_counting() -> None:
    assert demands.demand_for("count_to_10", "numeracy", 0.0).quantity == demands.MIN_COUNT


# ---------------------------------------------------------------------------
# §8.9's second translation: 2 choices vs. 3
# ---------------------------------------------------------------------------
def test_an_easier_difficulty_puts_fewer_cards_on_the_screen() -> None:
    assert demands.demand_for("count_to_5", "numeracy", 0.2).choice_count == 2
    assert demands.demand_for("count_to_5", "numeracy", 0.8).choice_count == 3


def test_scaffolding_narrows_the_cards_whatever_the_difficulty_says() -> None:
    """§10.4's "narrows choices". Fewer things to hold is the reduction available
    on a beat that has no number in it."""
    stretched = demands.demand_for("count_to_5", "numeracy", 0.8)
    helped = demands.demand_for("count_to_5", "numeracy", 0.8, scaffold=True)

    assert stretched.choice_count == 3
    assert helped.choice_count == 2


# ---------------------------------------------------------------------------
# §8.9's third translation: a familiar vs. a novel word
# ---------------------------------------------------------------------------
def test_a_harder_vocabulary_ask_reaches_for_a_word_they_have_not_met() -> None:
    assert not demands.demand_for("name_colours", "vocabulary", 0.2).novel_word
    assert demands.demand_for("size_words", "vocabulary", 0.8).novel_word


def test_a_scaffolded_word_beat_stays_on_familiar_ground() -> None:
    assert not demands.demand_for("size_words", "vocabulary", 0.8, scaffold=True).novel_word


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def test_comparing_gets_harder_by_closing_the_gap_as_well_as_by_growing() -> None:
    easy = demands.demand_for("more_fewer", "numeracy", 0.2)
    hard = demands.demand_for("more_fewer", "numeracy", 0.95)

    assert easy.kind == demands.COMPARE
    assert hard.quantity > easy.quantity
    assert hard.gap < easy.gap, "eight against nine is counting; one against three is a glance"
    assert easy.smaller >= 1 and hard.smaller >= 1


def test_scaffolding_a_comparison_widens_the_gap_rather_than_shrinking_the_world() -> None:
    plain = demands.demand_for("more_fewer", "numeracy", 0.5)
    helped = demands.demand_for("more_fewer", "numeracy", 0.5, scaffold=True)

    assert helped.quantity == plain.quantity
    assert helped.gap > plain.gap


# ---------------------------------------------------------------------------
# Scaffolding and stretching, together
# ---------------------------------------------------------------------------
def test_scaffolding_does_not_shrink_the_count_a_second_time() -> None:
    """The step down has already happened in the difficulty (`engine.adapt`).

    Taking another rung off here would drop a child from five things to two for
    one wobble, which reads as a demotion to anyone watching the screen (§8.5).
    """
    plain = demands.demand_for("count_to_10", "numeracy", 0.5)
    helped = demands.demand_for("count_to_10", "numeracy", 0.5, scaffold=True)

    assert helped.quantity == plain.quantity


def test_a_beat_is_never_scaffolded_and_stretched_at_once() -> None:
    demand = demands.demand_for("count_to_5", "numeracy", 0.5, scaffold=True, stretch=True)

    assert demand.scaffold and not demand.stretch


# ---------------------------------------------------------------------------
# The summary that reaches the prompt
# ---------------------------------------------------------------------------
def test_the_summary_describes_a_story_and_never_a_lesson() -> None:
    """What the `learning_directive` layer says out loud (§8.10).

    It has to be a fact about a riverbank rather than a fact about our database:
    the writer can put five things in a picture and cannot act on "0.55".
    """
    summary = demands.demand_for("count_to_5", "numeracy", 0.5).summary()

    assert "5" in summary
    for forbidden in ("0.5", "mastery", "objective", "difficulty", "count_to_5", "test"):
        assert forbidden not in summary.lower(), forbidden


def test_an_unknown_objective_code_still_produces_a_playable_ask() -> None:
    """A catalog row added by a later migration must not crash a beat."""
    demand = demands.demand_for("rhyming_pairs", "vocabulary", 0.5)

    assert demand.kind == demands.WORD
    assert demand.choice_count in (2, 3)
    assert demand.summary()


def test_difficulty_is_clamped_rather_than_trusted() -> None:
    """The input range. `count_to_10` because its ceiling is the top of the ladder,
    so this asserts the 0-1 clamp and nothing else (the objective ceiling's own
    effect on `difficulty` is `test_the_stamped_difficulty_describes_the_ask...`).
    """
    assert demands.demand_for("count_to_10", "numeracy", 4.0).difficulty == 1.0
    assert demands.demand_for("count_to_10", "numeracy", -2.0).difficulty == 0.0


# ---------------------------------------------------------------------------
# The reading ask (Docs/Build/Reading-Integration.md §7)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("difficulty", "words"),
    [(0.05, 3), (0.4, 5), (0.7, 6), (0.95, 8)],
)
def test_the_line_lengthens_with_difficulty(difficulty: float, words: int) -> None:
    demand = demands.demand_for("digraph_sh", "literacy", difficulty)

    assert demand.kind == demands.READ
    assert demand.quantity == words


def test_every_literacy_objective_takes_the_reading_shape() -> None:
    """Unlike numeracy, the domain decides rather than the code.

    The ten phonics objectives differ in *which* sounds the line may contain —
    a fact the reading engine owns — not in how the child answers it.
    """
    for code in ("cvc_short_a", "digraph_th", "blends_s", "vowel_team_ai"):
        assert demands.demand_for(code, "literacy", 0.5).kind == demands.READ


def test_scaffolding_shortens_the_line_rather_than_changing_the_sounds() -> None:
    """The sounds *are* the objective and cannot be traded away.

    What can come down is how much the child holds at once — the same move
    COMPARE makes when it widens its gap instead of shrinking the groups.
    """
    plain = demands.demand_for("digraph_sh", "literacy", 0.7)
    helped = demands.demand_for("digraph_sh", "literacy", 0.7, scaffold=True)

    assert helped.quantity < plain.quantity
    assert helped.kind == plain.kind


def test_a_scaffolded_line_never_falls_below_a_readable_length() -> None:
    """Below three words there is no sentence left to read."""
    demand = demands.demand_for("cvc_short_a", "literacy", 0.05, scaffold=True)

    assert demand.quantity == demands.READ_LADDER[0][1]


def test_the_reading_summary_asks_for_a_line_and_names_no_machinery() -> None:
    summary = demands.demand_for("digraph_sh", "literacy", 0.5).summary()

    assert "read" in summary.lower()
    for forbidden in ("digraph_sh", "0.5", "phonics", "objective", "difficulty"):
        assert forbidden not in summary.lower(), forbidden


def test_the_summary_does_not_dictate_which_sounds_to_use() -> None:
    """Decodability is checked afterwards, never asked for in the prompt.

    A free model asked for "a sentence using only short-a and sh" slips in words
    the child cannot decode. The engine owns correctness; the writer writes.
    """
    summary = demands.demand_for("digraph_sh", "literacy", 0.5).summary()

    assert "sh" not in summary.split()
    assert "sound" not in summary.lower()
