"""The teaching ladder, and the grapheme segmentation underneath it.

The ladder is the thing that makes this a tutor rather than a test: each rung is
a *different kind* of help and asks less of the child than the one before. These
tests pin the two properties that carry that — where a child enters, and that the
last two rungs do not ask for a performance.
"""

from __future__ import annotations

import pytest

from apps.reading.engine.content import graphemes
from apps.reading.engine.tutoring import teaching


# ---------------------------------------------------------------------------
# Where a child enters the ladder
# ---------------------------------------------------------------------------
def test_a_child_who_knows_the_sound_gets_a_nudge() -> None:
    """Over-helping is its own insult.

    A child who has read this sound correctly before does not need it explained
    from scratch; a reminder is enough.
    """
    assert teaching.first_rung(knows_sound=True, laboured=False) == teaching.NUDGE


def test_a_child_meeting_the_sound_for_the_first_time_is_modelled_to() -> None:
    assert teaching.first_rung(knows_sound=False, laboured=False) == teaching.MODEL


def test_a_laboured_reader_is_given_the_word_however_well_they_know_it() -> None:
    """Reading word by word means they are already at their limit.

    `laboured` beats `knows_sound`, because asking for more work from a child
    who is grinding is how a session ends early.
    """
    assert teaching.first_rung(knows_sound=True, laboured=True) == teaching.MODEL


# ---------------------------------------------------------------------------
# Climbing down it
# ---------------------------------------------------------------------------
def test_each_failure_steps_down_to_a_different_kind_of_help() -> None:
    """Not the same hint twice. That was the thing the ladder replaced."""
    rung = teaching.NUDGE
    seen = [rung]
    for _ in range(4):
        rung = teaching.next_rung(rung)
        seen.append(rung)

    assert seen == teaching.LADDER


def test_the_bottom_rung_holds() -> None:
    """Past `release` there is nowhere kinder to go, so it stays there."""
    assert teaching.next_rung(teaching.RELEASE) == teaching.RELEASE


def test_an_unknown_rung_falls_back_to_modelling() -> None:
    """A persisted rung from an older release must not crash a session."""
    assert teaching.next_rung("something_else") == teaching.MODEL


# ---------------------------------------------------------------------------
# What the tutor actually says
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rung", [teaching.NUDGE, teaching.MODEL, teaching.SEGMENT])
def test_the_early_rungs_ask_the_child_to_try(rung: str) -> None:
    move = teaching.move(rung, "ship", "digraph_sh")

    assert move.expects_attempt is True
    assert "ship" in move.say


@pytest.mark.parametrize("rung", [teaching.TOGETHER, teaching.RELEASE])
def test_the_last_two_rungs_expect_nothing_back(rung: str) -> None:
    """Asking a child to perform after two failures adds pressure at the worst
    moment, so the loop must not listen for an answer here."""
    move = teaching.move(rung, "ship", "digraph_sh")

    assert move.expects_attempt is False


def test_a_nudge_names_the_sound_when_the_skill_has_one() -> None:
    move = teaching.move(teaching.NUDGE, "ship", "digraph_sh")

    assert "sh" in move.say
    assert move.say.rstrip().endswith("Your turn.")


def test_a_nudge_still_works_for_a_skill_with_no_single_sound() -> None:
    """The blends have no `sound` — there is no one noise "st" makes.

    Naming an empty sound would produce "This one has the  sound", so the move
    falls back to just giving them the word.
    """
    move = teaching.move(teaching.NUDGE, "stop", "blends_s")

    assert "sound" not in move.say
    assert "stop" in move.say


def test_an_omission_is_named_as_one() -> None:
    """ "You missed one" is different from "one word" — the child skipped it
    rather than misread it, and pretending otherwise is confusing."""
    move = teaching.move(teaching.NUDGE, "ship", "digraph_sh", omitted=True)

    assert move.say.startswith("You missed one.")


def test_a_word_with_no_skill_still_gets_a_teaching_move() -> None:
    """`skill_id` is None for sight words, and the ladder must not crash on one."""
    move = teaching.move(teaching.NUDGE, "was", None)

    assert "was" in move.say


# ---------------------------------------------------------------------------
# Segmentation — sounding out the way a child is taught to
# ---------------------------------------------------------------------------
def test_a_digraph_is_one_chunk() -> None:
    """A child taught to say "ess-aitch-i-p" has been taught something wrong."""
    assert graphemes.segment("ship") == ["sh", "i", "p"]


def test_a_blend_is_two_chunks() -> None:
    """Blends are two sounds run together; digraphs are one sound. The teaching
    move differs, so the segmentation has to."""
    assert graphemes.segment("flash") == ["f", "l", "a", "sh"]


def test_the_longest_matching_group_wins() -> None:
    assert graphemes.segment("match") == ["m", "a", "tch"]


def test_silent_e_is_named_rather_than_sounded() -> None:
    """Sounding the e teaches the opposite of the rule it illustrates."""
    assert graphemes.silent_e_in("cake") is True

    spoken = graphemes.sound_out("cake")
    assert "The e is quiet." in spoken
    assert spoken.endswith("cake")


def test_a_word_without_silent_e_is_just_its_chunks() -> None:
    assert graphemes.sound_out("ship") == "sh. i. p"


def test_encouragement_is_earned_rather_than_automatic() -> None:
    """Praise for everything is praise for nothing — children work out very
    quickly that it means the adult is not listening."""
    assert teaching.encouragement(1) is None
    assert teaching.encouragement(2) is None
    assert teaching.encouragement(3) == "Three in a row."
    assert teaching.encouragement(5) == "You're on a roll."
    assert teaching.encouragement(10) is not None
