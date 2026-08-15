"""Which phonics skill a word exercises.

This is the join between a missed word and everything downstream — mastery,
selection, and what the companion says. It is a table of regexes, which means the
interesting cases are all about *ordering* and *exceptions* rather than about
whether a pattern matches.
"""

from __future__ import annotations

import pytest

from apps.reading.engine.content import skills


@pytest.mark.parametrize(
    ("word", "skill_id"),
    [
        ("cat", "cvc_short_a"),
        ("bin", "cvc_short_i"),
        ("dog", "cvc_short_o"),
        ("wash", "digraph_sh"),
        ("chip", "digraph_ch"),
        ("path", "digraph_th"),
        ("flag", "blends_l"),
        ("stop", "blends_s"),
        ("cake", "vce_long_a"),
        ("rain", "vowel_team_ai"),
    ],
)
def test_every_skill_is_reachable_from_a_word(word: str, skill_id: str) -> None:
    assert skills.skill_for_word(word) == skill_id


def test_the_most_specific_pattern_wins() -> None:
    """ "ship" is a digraph word, not a short-i word.

    Both patterns match it. Coaching a child on the i in "ship" when the thing
    they missed was the sh teaches them to look at the wrong part of the word,
    so `_MATCH_ORDER` puts digraphs above short vowels and this is what that is
    for.
    """
    assert skills.skill_for_word("ship") == "digraph_sh"
    assert skills.skill_for_word("chop") == "digraph_ch"


def test_sight_words_are_not_teaching_targets() -> None:
    """ "the" matches the th pattern and must still return None.

    Its vowel is irregular, so it is taught by memorisation. Stopping a child to
    sound it out teaches them a rule that does not apply to the word in front of
    them — which is worse than not stopping them at all.
    """
    assert skills.skill_for_word("the") is None
    assert skills.skill_for_word("was") is None
    assert skills.skill_for_word("said") is None


def test_decodable_th_words_are_still_targets() -> None:
    """The sight-word exception is per word, not per pattern.

    Excluding "the" must not cost us "this" and "path", or the th digraph would
    become unteachable — which is the obvious wrong way to implement the rule
    above.
    """
    assert skills.skill_for_word("this") == "digraph_th"
    assert skills.skill_for_word("path") == "digraph_th"


def test_words_that_exercise_nothing_return_none() -> None:
    """Function words are not failures to classify — they are not targets.

    A missed "on" is not a phonics error worth a teaching move, and the loop
    relies on this returning None to prefer a missed "ship" over it.
    """
    assert skills.skill_for_word("on") is None
    assert skills.skill_for_word("") is None


def test_matching_is_case_insensitive() -> None:
    """Words arrive capitalised at the start of a sentence."""
    assert skills.skill_for_word("Ship") == "digraph_sh"
    assert skills.skill_for_word("THE") is None


def test_the_sequence_is_the_ufli_teaching_order() -> None:
    """`in_sequence` is what a child walks when nothing is weak.

    Ordering is the curriculum: short vowels before digraphs before blends
    before long-vowel patterns. A sort that fell back to alphabetical would put
    silent e second and look entirely plausible while teaching backwards.
    """
    order = skills.in_sequence()

    assert len(order) == len(skills.SKILLS) == 10
    assert order[0] == "cvc_short_a"
    assert order[-1] == "vowel_team_ai"
    assert order == sorted(order, key=lambda s: skills.SKILLS[s]["ufli_order"])


def test_every_skill_carries_what_coaching_needs() -> None:
    """A skill with no name or no hint produces a teaching move that says nothing.

    `sound` is allowed to be empty — the blends deliberately have none, because
    there is no single sound to name for "st" — and `teaching.move` falls back to
    naming the word when it is missing.
    """
    for skill_id, skill in skills.SKILLS.items():
        assert skill["name"], skill_id
        assert skill["hint"], skill_id
        assert skill["common_core"], skill_id
        assert skill["pattern"], skill_id
