"""Did they understand it, or only decode it?

The design claim being tested is that this needs no language model: a literal
question about a seven-word sentence has an answer that is a word from that
sentence, so checking it is the same known-text problem the detector already
solves. What that leaves to get right is the *looseness* — and the gate.
"""

from __future__ import annotations

from apps.reading.engine.tutoring import comprehension


# ---------------------------------------------------------------------------
# Checking the answer
# ---------------------------------------------------------------------------
def test_the_bare_word_is_right() -> None:
    assert comprehension.check("fish", "fish").correct is True


def test_a_sentence_around_the_word_is_right() -> None:
    """ "the cat", "it was the cat" and "cat!" are all the same understanding.

    Insisting on an exact match would fail a child who understood perfectly. We
    are testing comprehension, not phrasing.
    """
    for said in ("the fish", "it was a fish", "a fish!", "I think fish"):
        assert comprehension.check(said, "fish").correct is True, said


def test_simple_endings_are_tolerated() -> None:
    assert comprehension.check("cats", "cat").correct is True
    assert comprehension.check("jumped", "jump").correct is True


def test_the_check_is_case_insensitive() -> None:
    assert comprehension.check("Fish", "fish").correct is True


def test_a_different_answer_is_wrong_but_attempted() -> None:
    answer = comprehension.check("a dog", "fish")

    assert answer.correct is False
    assert answer.attempted is True


def test_saying_nothing_is_not_the_same_as_getting_it_wrong() -> None:
    """A child who has gone quiet has not answered incorrectly.

    The two need different responses and the parent view counts them
    differently, so filler-only replies are `attempted=False` rather than a
    wrong answer.
    """
    for said in ("", "um", "uh, the", "I dont know"):
        answer = comprehension.check(said, "fish")
        assert answer.correct is False, said
        assert answer.attempted is False, said


def test_a_near_miss_is_not_quietly_accepted() -> None:
    """The looseness has a limit — "dish" is not "fish".

    Prefix tolerance is for endings, so a word that merely resembles the answer
    must still be wrong or the check would pass everything.
    """
    assert comprehension.check("dish", "fish").correct is False


# ---------------------------------------------------------------------------
# When to ask at all
# ---------------------------------------------------------------------------
def test_asked_only_after_a_clean_read() -> None:
    """A child who just fought through the decoding had no attention left for
    meaning. Failing a question they had no capacity for teaches them that
    reading is a series of tests they lose."""
    assert comprehension.should_ask(has_question=True, read_cleanly=True, struggling=False) is True
    assert (
        comprehension.should_ask(has_question=True, read_cleanly=False, struggling=False) is False
    )


def test_never_asked_of_a_struggling_child() -> None:
    assert comprehension.should_ask(has_question=True, read_cleanly=True, struggling=True) is False


def test_a_passage_with_no_question_is_never_asked_about() -> None:
    """Decoding practice works fine without one, so a missing question is a
    normal state rather than a gap to fill with something invented."""
    assert (
        comprehension.should_ask(has_question=False, read_cleanly=True, struggling=False) is False
    )


# ---------------------------------------------------------------------------
# What the tutor says back
# ---------------------------------------------------------------------------
def test_a_wrong_answer_is_told_the_answer_and_moved_past() -> None:
    """Never drilled. Comprehension is not fixed by being asked again harder —
    a retry would only add a way to fail."""
    reply = comprehension.respond(comprehension.check("a dog", "fish"))

    assert "fish" in reply
    assert "?" not in reply


def test_silence_gets_the_answer_without_the_consolation() -> None:
    """ "Good try" to a child who said nothing is a comment on a thing that did
    not happen."""
    reply = comprehension.respond(comprehension.check("", "fish"))

    assert "fish" in reply
    assert "Good try" not in reply


def test_a_right_answer_is_simply_confirmed() -> None:
    assert comprehension.respond(comprehension.check("fish", "fish")) == "That's right."
