"""
Did they understand it, or only say it?

Everything else in this engine measures decoding - whether the sounds on the
page came out of the child's mouth correctly. A child can pass all of it and
have taken nothing from the sentence. Reading is decoding *and* meaning, and
until now we only checked the first half.

The check is deliberately small: one literal question about the sentence they
just read, answered in a word or two. That's what comprehension instruction
looks like at this age - "who sat on the mat?" - not inference or opinion.

**Why this is checkable at all.** Open-ended answers would need a language model
to judge, which is slow and fallible. But a literal question about a
seven-word sentence has an answer that is *a word from that sentence*. So we
know what we're listening for, which is exactly the situation the detection
engine is built for. The same known-text alignment that catches a misread word
catches a right answer.
"""

import re
from dataclasses import dataclass
from typing import List, Optional

# Words that carry no meaning on their own, so a child who says only these
# hasn't answered. Kept short - the goal is to spot "um, the" not to police
# phrasing.
_EMPTY = {
    "the", "a", "an", "um", "uh", "er", "and", "is", "it", "he", "she", "they",
    "i", "dont", "know", "yes", "no",
}


@dataclass
class Answer:
    """What the child said, and whether it contained the answer."""

    correct: bool
    said: str
    expected: str
    attempted: bool  # did they say anything meaningful at all?


def words_in(text: str) -> List[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z']+", text)]


def check(said: str, expected: str) -> Answer:
    """Was the expected word somewhere in what they said?

    Deliberately loose. A child asked "who sat on the mat?" might say "cat",
    "the cat", "it was the cat" or "cat!" - all correct, and insisting on an
    exact match would fail a child who understood perfectly. We're testing
    comprehension, not phrasing.

    Simple plural and past-tense endings are tolerated for the same reason.
    """
    heard = words_in(said)
    target = expected.lower().strip()

    if not heard or all(w in _EMPTY for w in heard):
        return Answer(correct=False, said=said, expected=expected, attempted=False)

    correct = any(_same_word(w, target) for w in heard)
    return Answer(correct=correct, said=said, expected=expected, attempted=True)


def _same_word(said: str, target: str) -> bool:
    if said == target:
        return True
    # "cats" for "cat", "jumped" for "jump" - the meaning is there.
    for a, b in ((said, target), (target, said)):
        if a.startswith(b) and a[len(b):] in ("s", "es", "d", "ed", "ing"):
            return True
    return False


def should_ask(*, has_question: bool, read_cleanly: bool, struggling: bool) -> bool:
    """Is this a good moment to ask what the sentence meant?

    Only after a clean read. A child who has just fought their way through the
    decoding had all their attention on the words - asking what it meant is
    testing something they had no capacity left for, and failing it teaches
    them that reading is a series of tests they lose.

    So comprehension is asked of children who are reading comfortably. That
    isn't avoiding the hard case; it's asking the question when the answer
    means something.
    """
    return has_question and read_cleanly and not struggling


def ask(question: str) -> str:
    return question


def respond(answer: Answer) -> str:
    """What the tutor says back.

    Never drilled. A wrong answer gets told the answer and moves on, because
    comprehension isn't fixed by being asked again harder - it's fixed by
    reading more, with someone pointing things out. Turning it into a second
    test would just add a way to fail.
    """
    if answer.correct:
        return "That's right."
    if not answer.attempted:
        return f"It was {answer.expected}. Let's keep going."
    return f"Good try. It was {answer.expected}."
