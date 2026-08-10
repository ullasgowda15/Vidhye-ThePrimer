"""Salience — the selectivity guarantee (PRODUCT.md §11.4).

The acceptance criterion for the day is that **N raw events produce k ≪ N
memories**, and that is what most of this file is. It is worth being explicit
about why that is a *correctness* property and not a performance one: a companion
that remembers everything cannot remember like a friend, retrieval stops being
about what matters, and the prompt cost grows with the child's history forever.

No database, no model, no Redis. Scoring is a pure function of a candidate and its
neighbours, which is exactly what makes "why was this kept and that dropped"
answerable here rather than only in production.
"""

from __future__ import annotations

import pytest

from apps.memory.models import MemoryType
from apps.memory.salience import (
    WEIGHTS,
    Candidate,
    Signals,
    is_the_same_memory,
    novelty_against,
    salience,
    select,
)


def candidate(content: str, **signals: float) -> Candidate:
    return Candidate(
        type=MemoryType.EVENT,
        content=content,
        signals=Signals(**signals),
    )


# ---------------------------------------------------------------------------
# The score itself
# ---------------------------------------------------------------------------
def test_the_weights_are_a_partition_of_one() -> None:
    """So a score is directly comparable to the threshold and reads as a fraction."""
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_child_initiated_outweighs_every_other_single_factor() -> None:
    """§11.4: things the child chose, said or named "weigh heavily"."""
    assert WEIGHTS["child_initiated"] == max(WEIGHTS.values())
    for name, weight in WEIGHTS.items():
        if name != "child_initiated":
            assert WEIGHTS["child_initiated"] > weight


def test_nothing_notable_scores_nothing() -> None:
    assert salience(candidate("the child tapped to carry on")) == 0.0


def test_a_model_that_returns_a_wild_valence_cannot_dominate_the_table() -> None:
    """Signals are clamped, not trusted (the model half supplies one of them)."""
    absurd = candidate("x", emotional_intensity=40.0, child_initiated=-3.0)
    assert salience(absurd) == pytest.approx(WEIGHTS["emotional_intensity"])


def test_a_named_thing_beats_a_deliberate_choice_beats_a_tap() -> None:
    """The ordering §11.4 asks for, asserted as an ordering rather than a number."""
    named = candidate("Aarav named the dragon Sparkle", child_initiated=1.0, novelty=1.0)
    chose = candidate("Aarav chose the river", child_initiated=0.6, novelty=1.0)
    tapped = candidate("Aarav carried on", child_initiated=0.0, novelty=1.0)

    assert salience(named) > salience(chose) > salience(tapped)


# ---------------------------------------------------------------------------
# Novelty
# ---------------------------------------------------------------------------
def test_novelty_is_measured_against_what_we_already_hold() -> None:
    held = ["Aarav counted three frogs on the log all by himself"]

    assert novelty_against("Aarav counted three frogs on the log by himself", held) < 0.4
    assert novelty_against("Aarav named the little dragon Sparkle", held) > 0.9


def test_a_first_memory_is_wholly_novel() -> None:
    assert novelty_against("anything at all", []) == 1.0


# ---------------------------------------------------------------------------
# The selection pass — the day's acceptance criterion
# ---------------------------------------------------------------------------
def test_many_raw_events_become_a_few_memories() -> None:
    """N raw events -> k << N. The property the whole memory system rests on.

    Twenty events shaped like a real sitting: mostly taps and travel, a couple of
    real choices, one thing the child said. A friend would remember two or three of
    these, and so does this.
    """
    mundane = [
        candidate(
            f"Aarav tapped to carry on at the {place}",
            emotional_intensity=0.35,
            child_initiated=0.0,
        )
        for place in (
            "gate",
            "bridge",
            "meadow",
            "hollow",
            "stream",
            "hedge",
            "stile",
            "orchard",
            "well",
            "lane",
            "ford",
            "copse",
            "bank",
            "reeds",
            "ridge",
        )
    ]
    notable = [
        candidate(
            "Aarav said the dragon should be called Sparkle",
            child_initiated=1.0,
            emotional_intensity=0.8,
        ),
        candidate(
            "Aarav chose to help the lost frog find its chorus",
            child_initiated=0.6,
            emotional_intensity=0.7,
        ),
        candidate(
            "Aarav counted three frogs on the log unaided",
            child_initiated=0.4,
            emotional_intensity=0.4,
            skill_milestone=1.0,
        ),
        candidate(
            "Aarav was delighted when the whole chorus sang",
            child_initiated=0.4,
            emotional_intensity=0.9,
        ),
        candidate("Aarav went home along the lane", emotional_intensity=0.2),
    ]

    selection = select(mundane + notable, existing=[], cut=0.5, cap=6)

    assert selection.considered == 20
    assert 1 <= len(selection.kept) <= 4, [item.content for item in selection.kept]
    # And the ones kept are the ones that mattered, not merely few.
    kept = " | ".join(item.content for item in selection.kept)
    assert "Sparkle" in kept
    assert "tapped to carry on" not in kept


def test_the_same_act_on_two_beats_is_one_memory_however_the_scene_differed() -> None:
    """The bug the founder's first real consolidation exposed.

    `candidates` appends the beat's first clause to a memory so the sentence reads
    in context — and that clause is exactly what differs between two records of the
    same tap. Comparing whole sentences let the context defeat the filter, and a
    child who chose "Count the spiders" on two beats got two memories that said the
    same thing. Comparisons run on the gist for this reason.
    """
    selection = select(
        [
            candidate(
                'Spidey chose "Count the spiders" — Good morning, Spidey!',
                child_initiated=0.6,
                emotional_intensity=0.5,
            ),
            candidate(
                'Spidey chose "Count the spiders" — Spidey looked under the leaf and '
                "found one more spider waving hello!",
                child_initiated=0.6,
                emotional_intensity=0.5,
            ),
        ],
        existing=[],
        cut=0.5,
        cap=6,
    )

    assert len(selection.kept) == 1, [item.content for item in selection.kept]
    assert len(selection.duplicates) == 1


def test_two_different_choices_are_not_the_same_memory() -> None:
    """The other side of that fix: the filter must not over-collapse.

    "Count the spiders" and "Count the elephants" are two things the child did, and
    treating them as one record would lose one — the same failure as keeping
    duplicates, pointing the other way.

    Asserted on the *identity* question rather than on how many rows survive.
    Whether the second one also clears the threshold is a scoring matter — it
    depends on how much text the two share, which is what `novelty` is for and what
    `top_dropped` in the consolidation log exists to make visible.
    """
    spiders = 'Spidey chose "Count the spiders" — a web by the window.'
    elephants = 'Spidey chose "Count the elephants" — a parade in the hall.'

    assert is_the_same_memory(elephants, [spiders]) is False

    selection = select(
        [
            candidate(spiders, child_initiated=0.6, emotional_intensity=0.6),
            candidate(elephants, child_initiated=0.6, emotional_intensity=0.6),
        ],
        existing=[],
        cut=0.5,
        cap=6,
    )
    assert selection.duplicates == []
    assert len(selection.kept) >= 1


def test_a_memory_with_no_anchor_is_compared_whole() -> None:
    """Model-proposed content carries no separator; it must still dedupe."""
    selection = select(
        [
            candidate(
                "Spidey named the little dragon Sparkle.",
                child_initiated=1.0,
                emotional_intensity=0.9,
            ),
        ],
        existing=["Spidey named the little dragon Sparkle."],
        cut=0.5,
    )

    assert selection.kept == []
    assert len(selection.duplicates) == 1


def test_identity_and_novelty_ask_different_questions_of_different_text() -> None:
    """The split `select` depends on, pinned directly.

    Identity reads the gist, so the same act in two scenes is one memory. Novelty
    reads the whole sentence, so it stays a usable continuous score instead of
    collapsing to a handful of values off a four-token gist — which is what made
    two genuinely different choices both score as barely novel.
    """
    held = ['Spidey chose "Count the spiders" — Good morning, Spidey!']
    repeat = 'Spidey chose "Count the spiders" — the garden gate creaked open at dawn.'

    # Same act, different scene: one memory...
    assert is_the_same_memory(repeat, held) is True
    # ...but not *nothing* new, because where it happened is real information.
    assert novelty_against(repeat, held) > 0.3


def test_two_tellings_of_one_moment_become_one_memory() -> None:
    """The diversity half: a candidate can be dropped for being *like* a better one."""
    selection = select(
        [
            candidate(
                "Aarav chose the sparkling river path", child_initiated=0.6, emotional_intensity=0.7
            ),
            candidate(
                "Aarav chose the sparkling river again",
                child_initiated=0.6,
                emotional_intensity=0.7,
            ),
        ],
        existing=[],
        cut=0.5,
        cap=6,
    )

    assert len(selection.kept) == 1
    assert len(selection.dropped) + len(selection.duplicates) == 1


def test_something_we_already_remember_is_not_remembered_twice() -> None:
    existing = ["Aarav named the little dragon Sparkle"]
    selection = select(
        [
            candidate(
                "Aarav named the little dragon Sparkle",
                child_initiated=1.0,
                emotional_intensity=0.9,
            )
        ],
        existing=existing,
        cut=0.5,
    )

    assert selection.kept == []
    assert len(selection.duplicates) == 1


def test_the_cap_holds_even_when_everything_scored_well() -> None:
    """A sitting is a sitting, however rich (§11.4's bounded cost)."""
    plenty = [
        candidate(
            f"Aarav named the {creature} and chose to help it",
            child_initiated=1.0,
            emotional_intensity=0.9,
        )
        for creature in ("dragon", "otter", "heron", "badger", "kestrel", "hare", "newt", "vole")
    ]
    selection = select(plenty, existing=[], cut=0.5, cap=3)

    assert len(selection.kept) == 3
    assert len(selection.dropped) == 5


def test_selection_is_deterministic() -> None:
    """The same session consolidated twice keeps the same memories."""
    pool = [
        candidate("Aarav chose the river", child_initiated=0.6, emotional_intensity=0.7),
        candidate("Aarav met a heron by the reeds", child_initiated=0.5, emotional_intensity=0.5),
        candidate("Aarav counted four stones", child_initiated=0.4, skill_milestone=1.0),
    ]
    first = [item.content for item in select(pool, existing=[]).kept]
    second = [item.content for item in select(list(reversed(pool)), existing=[]).kept]

    assert first == second


def test_the_threshold_is_what_decides_and_it_is_configurable(settings) -> None:
    """§11.4 is a tuning problem, so the dial has to actually be a dial."""
    borderline = [
        candidate("Aarav paused at the gate", emotional_intensity=0.5, child_initiated=0.2)
    ]

    assert select(borderline, existing=[], cut=0.9).kept == []
    assert len(select(borderline, existing=[], cut=0.1).kept) == 1

    settings.MEMORY_SALIENCE_THRESHOLD = 0.99
    assert select(borderline, existing=[]).kept == []


def test_an_empty_sitting_selects_nothing_without_complaint() -> None:
    selection = select([], existing=[])

    assert selection.kept == []
    assert selection.considered == 0
