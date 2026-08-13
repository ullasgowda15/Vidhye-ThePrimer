"""Golden scenarios (PRODUCT.md §19.7's (a)).

> *"golden scenarios (does a counting objective actually get exercised? does the
> story stay coherent? is the companion consistent?)"*

Four graders, one whole played adventure each, and then — the part that matters
more than any of them — **a test per grader proving it bites**. A golden suite
whose graders have never once returned a failure is a suite that will go green
through the regression it was written for, and the only way to know is to break
the output on purpose and watch the number fall through the gate.

Every case plays the real loop: `orchestrator.create_adventure`, the real prompt
assembly, the real Learning Engine selection, the real safety pipeline. What
changes between scripted and `--live` is only who writes the beat.
"""

from __future__ import annotations

from typing import Any

import pytest

import graders
import thresholds
from harness import Score, play

pytestmark = pytest.mark.django_db


@pytest.fixture
def run(child: Any, provider: Any, spotcheck: Any) -> Any:
    """One adventure, played to the warm close."""
    played = play(child)
    spotcheck.write("golden", played)
    return played


# ---------------------------------------------------------------------------
# The four graders, against a healthy run
# ---------------------------------------------------------------------------
def test_the_story_stays_coherent(run: Any) -> None:
    """Beats belong to their story and to the tap that produced them (§8.5, §8.6)."""
    score = Score("golden.coherence")
    previous = None
    for turn in run.turns:
        reason = graders.coherence(turn, previous=previous)
        score.record(reason is None, reason or "")
        previous = turn

    score.assert_at_least(thresholds.gate_for("golden.coherence"))


def test_the_selected_objective_is_actually_exercised(run: Any) -> None:
    """§19.7's own example, and the quiet failure it names.

    The objective is read off the *adventure* — what the Learning Engine actually
    chose for this child — rather than hard-coded, so this fails if the engine's
    choice stops reaching the beats for any reason, including a change to the
    engine.
    """
    objective = str(run.adventure.primary_objective_id or "")
    assert objective, "the adventure carries no objective at all; §5.5 is broken upstream"

    score = Score("golden.objective_exercised")
    reason = graders.objective_exercised(run, objective_id=objective)
    score.record(reason is None, reason or "")

    score.assert_at_least(thresholds.gate_for("golden.objective_exercised"))


def test_the_companion_is_one_character_throughout(run: Any, child: Any) -> None:
    """§8.2's uncanny failure, on the relationship the whole product rests on."""
    score = Score("golden.companion_consistency")
    reason = graders.companion_consistency(run, name=child.companion.name)
    score.record(reason is None, reason or "")

    score.assert_at_least(thresholds.gate_for("golden.companion_consistency"))


def test_the_sitting_closes_warm(run: Any) -> None:
    """§8.8. The last thing a child hears before bed."""
    score = Score("golden.warm_close")
    reason = graders.warm_close(run.close)
    score.record(reason is None, reason or "")

    score.assert_at_least(thresholds.gate_for("golden.warm_close"))


# ---------------------------------------------------------------------------
# The graders bite
# ---------------------------------------------------------------------------
class TestTheGradersActuallyCatchThings:
    """Each grader, shown failing on the regression it exists to catch.

    Scripted mode only: these work by telling the provider to answer badly, and a
    real model cannot be instructed to regress on command. Skipped rather than
    silently passing under `--live`, because a green tick from a test that did not
    run is the thing this whole file is written against.
    """

    def test_a_dropped_objective_fails_the_objective_grader(
        self, child: Any, provider: Any
    ) -> None:
        if provider is None:
            pytest.skip("scripted mode only")
        provider.regress("ignores_objective")
        run = play(child)

        objective = str(run.adventure.primary_objective_id or "")
        assert graders.objective_exercised(run, objective_id=objective) is not None

    def test_a_renamed_companion_fails_the_consistency_grader(
        self, child: Any, provider: Any
    ) -> None:
        if provider is None:
            pytest.skip("scripted mode only")
        provider.regress("renames_companion")
        run = play(child)

        reason = graders.companion_consistency(run, name=child.companion.name)
        assert (
            reason is not None
        ), "the consistency grader passed a sitting in which the companion was renamed"
        assert child.companion.name in reason

    def test_a_cliffhanger_fails_the_warm_close_grader(self, child: Any, provider: Any) -> None:
        if provider is None:
            pytest.skip("scripted mode only")
        provider.regress("cliffhanger_close")
        run = play(child)

        assert graders.warm_close(run.close) is not None

    def test_a_consequence_that_ignores_the_choice_fails_coherence(
        self, child: Any, provider: Any
    ) -> None:
        if provider is None:
            pytest.skip("scripted mode only")
        provider.regress("ignores_choice")
        run = play(child)

        previous = None
        reasons = []
        for turn in run.turns:
            reasons.append(graders.coherence(turn, previous=previous))
            previous = turn
        assert any(
            reason is not None for reason in reasons
        ), "the coherence grader passed a story that ignored every choice the child made"


# ---------------------------------------------------------------------------
# Divergence — the property a single playthrough cannot show
# ---------------------------------------------------------------------------
def test_two_children_who_choose_differently_get_different_stories(
    child: Any, provider: Any, db: Any
) -> None:
    """§8.5: choices have consequences, and a suite that always taps the first
    card would never notice if they stopped.

    Deliberately compared on the *consequence* beat rather than on the opening,
    which is identical by construction — both children start in the same place.
    """
    from apps.story import orchestrator

    first = orchestrator.create_adventure(child)
    opening = orchestrator.create_beat(first, seq=1)
    options = [str(o["key"]) for o in (opening.interaction or {}).get("choices") or []]
    if len(options) < 2:
        pytest.skip("the opening beat offered fewer than two paths")

    orchestrator.record_choice(opening, chosen_key=options[0])
    left = orchestrator.create_beat(first, seq=2, prev_choice=orchestrator.turn_from(opening))

    second = orchestrator.create_adventure(child)
    other_opening = orchestrator.create_beat(second, seq=1)
    orchestrator.record_choice(other_opening, chosen_key=options[1])
    right = orchestrator.create_beat(
        second, seq=2, prev_choice=orchestrator.turn_from(other_opening)
    )

    assert (
        left.narration != right.narration
    ), "two different choices produced the same beat: §8.5's consequences are not landing"
