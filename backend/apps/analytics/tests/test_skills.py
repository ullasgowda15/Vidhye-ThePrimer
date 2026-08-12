"""Reading the skill model for a parent (PRODUCT.md §12.3, §10.6).

The claim under test is criterion 3: insights are per-domain, in plain language,
mapped to milestones, and **never a grade or a percentile**. Half of these tests
are therefore about what the module does *not* produce.

The other half is the question Day 10 left open in `PROGRESS.md` — a child who
counts to three perfectly asymptotes at ~0.64 mastery — and its answer:
`reach_for`. `test_the_ceiling_is_the_denominator` is the one to read first.
"""

from __future__ import annotations

import pytest

from apps.analytics import skills
from apps.analytics.tests.conftest import finish, play
from apps.learning.models import LearningProgress, Objective

pytestmark = pytest.mark.django_db


def set_progress(child, code: str, *, mastery: float, evidence_count: int = 6):
    progress, _ = LearningProgress.objects.get_or_create(
        child=child, objective=Objective.objects.get(code=code)
    )
    progress.mastery = mastery
    progress.evidence_count = evidence_count
    progress.uncertainty = 0.3
    progress.save()
    return progress


# ---------------------------------------------------------------------------
# The Day-10 question, answered
# ---------------------------------------------------------------------------
def test_the_ceiling_is_the_denominator(aarav) -> None:
    """A child who has finished counting to three is not 64% of the way there.

    `count_to_3` cannot ask for more than three things, three things is a
    0.44-difficulty ask, and a clean success is worth `EVIDENCE_MARGIN` above the
    ask — so 0.64 *is* the top of this objective. Anything that divided by 1.0
    would tell a parent their child is middling at something they have finished.
    """
    assert skills.reach_for("count_to_3", "numeracy") == pytest.approx(0.64)
    assert skills.reach_for("count_to_5", "numeracy") == pytest.approx(0.84)
    assert skills.reach_for("count_to_10", "numeracy") == pytest.approx(1.0)

    set_progress(aarav, "count_to_3", mastery=0.63)
    row = LearningProgress.objects.select_related("objective").get(
        child=aarav, objective__code="count_to_3"
    )

    assert skills.status_for(row) == skills.MASTERED


def test_the_same_number_reads_differently_on_a_longer_ladder(aarav) -> None:
    """0.63 is the top of counting to three and the middle of counting to ten."""
    set_progress(aarav, "count_to_3", mastery=0.63)
    set_progress(aarav, "count_to_10", mastery=0.63)

    by_code = {view.code: view for view in skills.skill_views(aarav)}

    assert by_code["count_to_3"].status == skills.MASTERED
    assert by_code["count_to_10"].status == skills.PRACTISING


def test_one_lucky_tap_is_not_a_mastered_skill(aarav) -> None:
    set_progress(aarav, "count_to_3", mastery=0.64, evidence_count=1)
    row = LearningProgress.objects.select_related("objective").get(
        child=aarav, objective__code="count_to_3"
    )

    assert skills.status_for(row) == skills.EMERGING


def test_an_objective_nobody_has_practised_is_not_started(aarav) -> None:
    views = {view.code: view for view in skills.skill_views(aarav)}

    assert views["count_to_5"].status == skills.NOT_STARTED
    assert views["count_to_5"].evidence_count == 0


# ---------------------------------------------------------------------------
# Criterion 3: plain language, no grades
# ---------------------------------------------------------------------------
def test_every_status_has_a_sentence_and_no_sentence_carries_a_score(aarav) -> None:
    """§12.3: plain language, never a grade or a percentile.

    A digit is not forbidden outright — "we have seen this 4 times so far" is a
    count of afternoons, which is information rather than a mark. What may never
    appear is the estimate itself, in any of the three forms a reader would take
    for a score.
    """
    set_progress(aarav, "count_to_3", mastery=0.63)
    set_progress(aarav, "count_to_5", mastery=0.45)
    set_progress(aarav, "name_colours", mastery=0.1)

    for view in skills.skill_views(aarav):
        assert view.summary, f"{view.code} has no plain-language summary"
        assert "%" not in view.summary
        assert f"{view.mastery:.2f}" not in view.summary
        assert f"{view.mastery:.1f}" not in view.summary
        assert f"{round(view.mastery * 100)}" not in view.summary


def test_no_sentence_tells_a_parent_their_child_is_behind(aarav) -> None:
    """§12.3: framed developmentally and positively. There is no failure word."""
    forbidden = {"behind", "below", "fail", "poor", "weak", "struggling", "worse", "average"}
    set_progress(aarav, "count_to_3", mastery=0.05, evidence_count=4)

    for view in skills.skill_views(aarav):
        words = set(view.summary.lower().replace(",", " ").replace(".", " ").split())
        assert not (words & forbidden), f"{view.code}: {view.summary}"


def test_a_skill_is_always_named_by_its_milestone_not_its_code(aarav) -> None:
    for view in skills.skill_views(aarav):
        assert view.milestone
        assert "_" not in view.milestone


# ---------------------------------------------------------------------------
# The milestone rollup (§10.6)
# ---------------------------------------------------------------------------
def test_a_milestone_is_only_mastered_when_everything_under_it_is(aarav) -> None:
    set_progress(aarav, "count_to_3", mastery=0.64)
    set_progress(aarav, "count_to_5", mastery=0.84)
    # Halfway up the last rung: practising, so the milestone is practising too.
    set_progress(aarav, "count_to_10", mastery=0.5)

    counting = _milestone(aarav, "Counting and quantity")

    assert counting.status == skills.PRACTISING
    # ...and the honest reading is paired with one that never goes backwards.
    assert "five" in counting.furthest


def test_the_furthest_step_follows_the_curriculum_and_not_the_alphabet(aarav) -> None:
    """`count_to_10` sorts before `count_to_3` as a string. It must not here."""
    for code in ("count_to_3", "count_to_5", "count_to_10"):
        set_progress(aarav, code, mastery=skills.reach_for(code, "numeracy"))

    counting = _milestone(aarav, "Counting and quantity")

    assert counting.status == skills.MASTERED
    assert "ten" in counting.furthest


def test_a_milestone_nobody_has_touched_says_so_rather_than_hiding(aarav) -> None:
    rollup = skills.milestone_rollup(aarav)

    assert rollup, "the rollup must list the curriculum, not only what was played"
    assert all(view.status == skills.NOT_STARTED for view in rollup)
    assert all(view.furthest == "" for view in rollup)


def test_the_rollup_covers_both_domains(aarav) -> None:
    domains = {view.domain for view in skills.milestone_rollup(aarav)}

    assert domains == {"numeracy", "vocabulary"}


# ---------------------------------------------------------------------------
# It reads the live skill model rather than a copy of it
# ---------------------------------------------------------------------------
def test_playing_a_story_moves_what_the_parent_is_shown(aarav, fake_ai) -> None:
    before = {view.code: view.status for view in skills.skill_views(aarav)}
    adventure = play(aarav, correct=True)
    finish(adventure)

    after = {view.code: view for view in skills.skill_views(aarav)}
    code = adventure.primary_objective_id
    assert code is not None

    assert before[code] == skills.NOT_STARTED
    assert after[code].status != skills.NOT_STARTED
    assert after[code].evidence_count > 0


def _milestone(child, name: str):
    found = next(view for view in skills.milestone_rollup(child) if view.name == name)
    return found
