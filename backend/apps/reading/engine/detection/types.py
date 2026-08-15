"""Shared types for reading-error detection."""

from dataclasses import dataclass
from typing import Optional

# What happened to a single expected word.
STATUS_OK = "ok"
STATUS_UNCLEAR = "unclear"  # matched, but the engine wasn't confident - a soft signal, not a verdict
STATUS_SUBSTITUTION = "substitution"  # said a different word
STATUS_OMISSION = "omission"  # skipped it
STATUS_EXTRA = "extra"  # said something that isn't in the passage (stutter, filler, self-correction)

# Counts as having read the word. "Unclear" is included on purpose: it means the
# engine's confidence was low, which in practice fires on accented pronunciation
# as much as on genuine errors. A child reading in their own accent has decoded
# the word, and must not be marked down for it.
ACCEPTABLE = {STATUS_OK, STATUS_UNCLEAR}


def read_acceptably(status: str) -> bool:
    return status in ACCEPTABLE


@dataclass
class WordResult:
    """One expected word, and what the child actually did with it."""

    index: int
    expected: str
    detected: Optional[str]
    status: str
    confidence: Optional[float]
    slow: bool  # took much longer than the surrounding words
    paused_before: bool  # long silence before this word
    missed_sound: Optional[str] = None  # the specific phoneme that didn't come out
    # Where in the recording this word was spoken. Kept because fluency is a
    # question about time, not accuracy - a child can read every word correctly
    # and still be grinding through one at a time.
    start: Optional[float] = None
    end: Optional[float] = None

    def to_dict(self):
        # Engines hand back numpy scalars, which JSON can't encode. Coerce here
        # so nothing leaks past the detection boundary.
        return {
            "index": int(self.index),
            "expected": self.expected,
            "detected": self.detected,
            "status": self.status,
            "confidence": None if self.confidence is None else float(self.confidence),
            "slow": bool(self.slow),
            "paused_before": bool(self.paused_before),
            "missed_sound": self.missed_sound,
        }
