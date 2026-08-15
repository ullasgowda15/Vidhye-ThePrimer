"""
How fluently did they read, not just how accurately?

A child can get every word right and still be struggling - grinding out one
word at a time, with no sense of the sentence. Accuracy alone can't see that,
and it's the difference between a child who is decoding and one who is reading.

Words correct per minute is the measure every reading specialist uses, which
matters twice over: it's the right signal, and it's a number a parent or teacher
can compare against something outside this app.
"""

from dataclasses import dataclass
from typing import List, Optional

from .types import WordResult, read_acceptably

# Rough end-of-year expectations for oral reading fluency, in words correct per
# minute. Public benchmarks vary by a few words either way; these are used only
# to describe a child in plain language, never to grade them.
BENCHMARKS = {
    "kindergarten": 30,
    "grade_1": 60,
}

# Below this, a child is working word by word rather than reading a sentence.
LABOURED_WCPM = 25


@dataclass
class Fluency:
    words_correct: int
    seconds: float
    wcpm: Optional[float]

    @property
    def laboured(self) -> bool:
        """Reading one word at a time, whatever the accuracy."""
        return self.wcpm is not None and self.wcpm < LABOURED_WCPM

    def describe(self) -> str:
        """Plain language for a parent. No jargon, no grade labels."""
        if self.wcpm is None:
            return "not enough to measure"
        if self.wcpm < LABOURED_WCPM:
            return "working through it word by word"
        if self.wcpm < BENCHMARKS["kindergarten"]:
            return "reading steadily"
        if self.wcpm < BENCHMARKS["grade_1"]:
            return "reading smoothly"
        return "reading fluently"


def measure(words: List[WordResult]) -> Fluency:
    """Words correct per minute for one reading.

    Timed from the first word actually spoken to the last, not from when the
    recording started. A child who takes a breath before beginning shouldn't be
    marked as slower for it.
    """
    correct = sum(1 for w in words if w.expected and read_acceptably(w.status))

    spoken = [w for w in words if w.start is not None and w.end is not None]
    seconds = (max(w.end for w in spoken) - min(w.start for w in spoken)) if spoken else 0.0

    if seconds <= 0:
        return Fluency(words_correct=correct, seconds=0.0, wcpm=None)

    # A single word tells you nothing about fluency; two seconds of audio even
    # less. Reporting a rate from that would be noise dressed as a number.
    if correct < 3 or seconds < 1.5:
        return Fluency(words_correct=correct, seconds=seconds, wcpm=None)

    return Fluency(
        words_correct=correct,
        seconds=seconds,
        wcpm=round(correct / (seconds / 60.0), 1),
    )
