"""The objective selector (PRODUCT.md §10.3).

§10.3 asks for four behaviours and warns about two failure modes, and each one
below is a test rather than a paragraph: pick at the edge of ability, honour
spaced retrieval, respect the prerequisite chain, interleave domains — without
over-repeating and without ever handing a child something they cannot do.

The last two tests are the ones with product consequences. A child gets a
difficulty that came from *their* skill model and nobody else's, and two sittings
in a row do not practise the same thing.
"""

from __future__ import annotations

import pytest

from apps.learning import engine
from apps.learning.models import Domain, LearningProgress, Objective
from apps.learning.tests.conftest import set_progress
from apps.story import orchestrator

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Where a child starts
# ---------------------------------------------------------------------------
def test_a_child_who_has_never_played_starts_at_the_beginning_of_the_chain(aarav) -> None:
    selection = engine.select_objective(aarav)

    assert selection is not None
    # Not "some numeracy objective": the *first* one. Everything downstream of a
    # first session — the founder test, the seeded demo, the parent's first
    # glimpse of progress — depends on this being predictable rather than a coin
    # toss between two objectives that both score zero.
    assert selection.code == "count_to_3"
    # Gentle, because the estimate they are starting from is nothing at all.
    assert selection.difficulty == pytest.approx(engine.MIN_DIFFICULTY + 0.05, abs=0.06)


def test_selecting_does_not_write_a_progress_row(aarav) -> None:
    """Choosing what to practise is not practice.

    A row created at selection time would give a child an `evidence_count` of 0
    with a `last_practiced_at` of null and a place in the parent dashboard, for a
    story they might abandon after one beat. Rows are created by evidence
    (`apply_evidence`) and by nothing else.
    """
    engine.select_objective(aarav)

    assert not LearningProgress.objects.filter(child=aarav).exists()


def test_an_objective_outside_the_age_band_is_not_offered(aarav) -> None:
    """The band is a filter, and `count_to_3` tops out below seven."""
    aarav.birthdate = aarav.birthdate.replace(year=aarav.birthdate.year - 2)  # now 7
    aarav.save(update_fields=["birthdate"])

    for _ in range(6):
        selection = engine.select_objective(aarav)
        assert selection is not None
        assert selection.objective.min_age <= aarav.age <= selection.objective.max_age
        # Practised, so the next pass picks something else and the loop covers
        # more than one row.
        set_progress(aarav, selection.code, mastery=0.9, practised_days_ago=0)


def test_a_child_too_young_for_anything_gets_no_objective_rather_than_a_crash(aarav) -> None:
    """A story with no learning directive is a story. No story is not (§13)."""
    aarav.birthdate = aarav.birthdate.replace(year=aarav.birthdate.year + 4)  # now 1
    aarav.save(update_fields=["birthdate"])

    assert engine.select_objective(aarav) is None


# ---------------------------------------------------------------------------
# The zone of proximal development (§10.3)
# ---------------------------------------------------------------------------
def test_the_edge_of_ability_beats_both_the_mastered_and_the_untouched(aarav) -> None:
    set_progress(aarav, "count_to_3", mastery=0.95, practised_days_ago=30)
    set_progress(aarav, "count_to_5", mastery=0.55, practised_days_ago=30)

    selection = engine.select_objective(aarav)

    assert selection is not None
    assert selection.code == "count_to_5", "0.55 is the sweet spot; 0.95 is finished"


def test_an_untouched_objective_outranks_a_mastered_one(aarav) -> None:
    """The asymmetry in `zpd_score`, which is the whole reason it is asymmetric.

    Mastery 1.0 and mastery 0.0 are both outside the band. Only one of them is
    worth a sitting.
    """
    assert engine.zpd_score(0.0) > engine.zpd_score(1.0)


def test_difficulty_starts_at_the_childs_own_estimate(aarav) -> None:
    set_progress(aarav, "count_to_3", mastery=0.9, practised_days_ago=30)
    set_progress(aarav, "count_to_5", mastery=0.5, practised_days_ago=30)

    selection = engine.select_objective(aarav)

    assert selection is not None
    assert selection.code == "count_to_5"
    # §10.4: "difficulty starts at the child's estimate", plus a small reach.
    assert selection.difficulty == pytest.approx(0.5 + engine.ZPD_STRETCH)


# ---------------------------------------------------------------------------
# Criterion 6 — the same objective, two children, two difficulties
# ---------------------------------------------------------------------------
def test_two_children_at_different_levels_get_different_difficulty_for_one_objective(
    aarav, nia
) -> None:
    """The point of a per-child skill model, in one assertion (§10.2, §10.4).

    Both are five, both are pointed at `count_to_5`, and both are unlocked. What
    differs is only what each of them has shown us — so the story one of them gets
    has five stepping stones in it and the story the other gets has three.
    """
    for child, mastery in ((aarav, 0.20), (nia, 0.65)):
        set_progress(child, "count_to_3", mastery=0.85, practised_days_ago=30)
        set_progress(child, "count_to_5", mastery=mastery, practised_days_ago=30)

    easier = engine.select_objective(aarav)
    harder = engine.select_objective(nia)

    assert easier is not None and harder is not None
    assert easier.code == harder.code == "count_to_5"
    assert easier.difficulty < harder.difficulty

    # And the difference is not a number in a log line — it reaches the story as
    # a different count of things in the picture (§8.9).
    assert easier.demand.quantity < harder.demand.quantity


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
def test_an_objective_whose_prerequisite_is_unmet_is_never_offered(aarav) -> None:
    """The one absolute gate in §10.3.

    `count_to_10` sits at 0.55 — dead centre of the ZPD band and the highest
    scoring row in the table — and it is still not offered, because a child who
    finds five hard cannot be handed ten however good the arithmetic looks.
    """
    set_progress(aarav, "count_to_3", mastery=0.9, practised_days_ago=30)
    set_progress(aarav, "count_to_5", mastery=0.30, practised_days_ago=30)
    set_progress(aarav, "count_to_10", mastery=0.55, practised_days_ago=30)

    selection = engine.select_objective(aarav)

    assert selection is not None
    assert selection.code != "count_to_10"


def test_the_next_link_unlocks_once_the_last_one_is_roughly_in_hand(aarav) -> None:
    """`PREREQUISITE_MASTERY` is short of certainty, deliberately (§10.3)."""
    set_progress(aarav, "count_to_3", mastery=0.9, practised_days_ago=30)
    set_progress(aarav, "count_to_5", mastery=0.65, practised_days_ago=30)
    set_progress(aarav, "count_to_10", mastery=0.0)

    unlocked = [
        objective.code
        for objective in Objective.objects.all()
        if engine._unlocked(
            objective,
            {row.objective.code: row for row in LearningProgress.objects.filter(child=aarav)},
        )
    ]

    assert "count_to_10" in unlocked


# ---------------------------------------------------------------------------
# Spacing (§10.3's spaced retrieval)
# ---------------------------------------------------------------------------
def test_an_overdue_objective_is_revisited_over_one_at_the_edge(aarav) -> None:
    # In the band, not due, and long enough ago to carry no recency penalty.
    set_progress(aarav, "count_to_5", mastery=0.55, practised_days_ago=30, due_in_days=30)
    # Nearly mastered — a worse ZPD score — but a week past its review date.
    set_progress(aarav, "count_to_3", mastery=0.90, practised_days_ago=30, due_in_days=-8)

    selection = engine.select_objective(aarav)

    assert selection is not None
    assert selection.code == "count_to_3", "spacing is supposed to be able to win"


def test_the_same_objective_stops_winning_once_it_is_no_longer_due(aarav) -> None:
    """The other half of the test above: it is dueness doing the work, not luck."""
    set_progress(aarav, "count_to_5", mastery=0.55, practised_days_ago=30, due_in_days=30)
    set_progress(aarav, "count_to_3", mastery=0.90, practised_days_ago=30, due_in_days=30)

    selection = engine.select_objective(aarav)

    assert selection is not None
    assert selection.code == "count_to_5"


def test_an_objective_that_has_never_been_practised_is_not_treated_as_overdue(aarav) -> None:
    """Otherwise every untouched row would be permanently overdue and spacing
    would say nothing at all (`LearningProgress.is_due`)."""
    progress = set_progress(aarav, "count_to_5", mastery=0.0, due_in_days=None)
    progress.last_practiced_at = None
    progress.save()

    assert not progress.is_due()


def test_something_practised_moments_ago_is_pushed_down_the_list(aarav) -> None:
    """§10.3's "avoids repeating the same objective too often"."""
    set_progress(aarav, "count_to_5", mastery=0.55, practised_days_ago=0, due_in_days=30)
    set_progress(aarav, "more_fewer", mastery=0.55, practised_days_ago=10, due_in_days=30)
    set_progress(aarav, "count_to_3", mastery=0.9, practised_days_ago=10, due_in_days=30)

    selection = engine.select_objective(aarav)

    assert selection is not None
    assert selection.code == "more_fewer", "the one they did five minutes ago is not the one"


# ---------------------------------------------------------------------------
# Interleaving across sittings (§10.3) — criterion 2's "evidence across ≥2 sessions"
# ---------------------------------------------------------------------------
def test_two_sittings_in_a_row_do_not_practise_the_same_domain(aarav, fake_ai, enqueued) -> None:
    """Played, not stubbed: two real adventures, and the second must move on.

    This is the acceptance criterion's "evidence across ≥2 sessions" and it is
    deliberately end-to-end. The interleaving penalty reads
    `Adventure.primary_objective_id`, so a test that only called
    `select_objective` twice would never exercise the thing that writes it — and
    the whole term would be dead code that scored well in isolation.
    """
    first = orchestrator.create_adventure(aarav)
    assert first.primary_objective_id == "count_to_3"

    # The child played it and it went fine. That is what makes the second sitting
    # a *different* sitting rather than a repeat of an untouched skill model.
    set_progress(aarav, "count_to_3", mastery=0.35, practised_days_ago=0)
    orchestrator.end_adventure(first)

    second = orchestrator.create_adventure(aarav)

    assert second.primary_objective_id != first.primary_objective_id
    assert (
        Objective.objects.get(code=second.primary_objective_id).domain == Domain.VOCABULARY
    ), "the second sitting should reach across to the other domain"


def test_interleaving_is_a_preference_and_not_a_ban(aarav) -> None:
    """A domain penalty that could not be outvoted would strand a child.

    `count_to_5` here is in the band *and* overdue, in a domain we just used. It
    still wins, because §10.3 asks for a balance and not a rotation — and because
    a child mid-way through learning to count should not be sent off to colours
    for a week to satisfy a scheduling rule.
    """
    set_progress(aarav, "count_to_3", mastery=0.85, practised_days_ago=30)
    set_progress(aarav, "count_to_5", mastery=0.55, practised_days_ago=30, due_in_days=-8)

    interests = engine._interest_tokens(aarav)
    numeracy = Objective.objects.get(code="count_to_5")
    vocabulary = Objective.objects.get(code="name_colours")
    rows = {row.objective.code: row for row in LearningProgress.objects.filter(child=aarav)}

    scored = {
        objective.code: engine.score_objective(
            objective,
            rows.get(objective.code) or LearningProgress(child=aarav, objective=objective),
            interests=interests,
            last_domain=Domain.NUMERACY,
        )
        for objective in (numeracy, vocabulary)
    }

    assert scored["count_to_5"] > scored["name_colours"]


# ---------------------------------------------------------------------------
# Interest fit (§10.3), and its honest limits
# ---------------------------------------------------------------------------
def test_interest_overlap_does_not_fire_on_words_that_mean_nothing(aarav) -> None:
    """The first bug this term shipped with, kept as a test.

    A child whose interests included "the sea" matched every objective whose
    description contained the word "the" — which is all of them — so the tie-break
    term decided every selection. `_STOPWORDS` is what stops that, and without a
    test the next stopword-adjacent word would bring it straight back.
    """
    objective = Objective.objects.get(code="count_to_3")

    assert engine.interest_fit(objective, engine._tokens("the sea")) == 0.0
    assert engine.interest_fit(objective, engine._tokens("count")) == 1.0


def test_a_creature_named_in_a_description_does_not_decide_the_curriculum(aarav) -> None:
    """The second bug, and it reached a child's first-ever adventure.

    `name_colours` was seeded with "the green frog" as an example, and a
    frog-loving five-year-old was handed vocabulary instead of counting on the
    strength of one illustrative noun. Objective descriptions name the *skill*;
    the creatures belong to the story (`0002_seed_catalog`).
    """
    frogs = engine._tokens("frogs rivers dinosaurs")

    for objective in Objective.objects.all():
        assert engine.interest_fit(objective, frogs) == 0.0, objective.code


def test_a_child_with_no_interests_is_not_penalised(aarav) -> None:
    aarav.interests = []
    aarav.save(update_fields=["interests"])

    assert engine.select_objective(aarav) is not None
