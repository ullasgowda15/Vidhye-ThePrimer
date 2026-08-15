"""
How a tutor helps, and in what order.

The loop used to hint, then hint again slightly louder, then give up. That isn't
teaching - it's testing with encouragement. A tutor sitting beside a child who
can't read "ship" doesn't repeat themselves; they change technique. They say the
word. Then they break it apart. Then they say it together. Each rung asks less
of the child than the one before, and each is a different kind of help.

The ladder here is the standard gradual-release sequence used in reading
instruction: I do it, we do it, you do it. What's adaptive about it is where a
child enters - one who has this sound already gets a nudge, one who is lost gets
modelled to immediately.
"""

from dataclasses import dataclass
from typing import List, Optional

from ..content import graphemes, skills

# The rungs, in order of how much support they give.
NUDGE = "nudge"  # name the sound, ask them to try
MODEL = "model"  # say the whole word, they echo it
SEGMENT = "segment"  # break it into sounds and blend
TOGETHER = "together"  # say it in unison, no performance
RELEASE = "release"  # let it go, warmly

LADDER = [NUDGE, MODEL, SEGMENT, TOGETHER, RELEASE]


@dataclass
class Move:
    """One teaching act: what the tutor says, and what it expects back."""

    rung: str
    say: str
    # Whether the child is expected to respond. `together` and `release` don't
    # ask for a solo attempt, so the loop shouldn't listen for one.
    expects_attempt: bool = True


def first_rung(*, knows_sound: bool, laboured: bool) -> str:
    """Where to start helping, given what we know about this child.

    A child who has read this sound correctly before doesn't need it explained
    from scratch; a reminder is enough, and over-helping is its own insult. A
    child who is reading word by word is already at their limit, so start by
    just giving them the word rather than asking for more work.
    """
    if laboured:
        return MODEL
    if knows_sound:
        return NUDGE
    return MODEL


def next_rung(current: str) -> str:
    """Step down to more support."""
    try:
        return LADDER[min(LADDER.index(current) + 1, len(LADDER) - 1)]
    except ValueError:
        return MODEL


def move(rung: str, word: str, skill_id: Optional[str], *, omitted: bool = False) -> Move:
    """Turn a rung into the words a five year old hears.

    Written to be spoken, not read: short sentences, the word last so it's the
    freshest thing in their ear, and never any mention of spelling. They're
    being asked to make a sound, so the tutor makes the sound.
    """
    skill = skills.get(skill_id) if skill_id else None
    sound = skill["sound"] if skill and skill["sound"] else None

    if rung == NUDGE:
        opening = "You missed one." if omitted else "One word."
        if sound:
            return Move(rung, f"{opening} This one has the {sound} sound. {word}. Your turn.")
        return Move(rung, f"{opening} {word}. Your turn.")

    if rung == MODEL:
        opening = "You missed one." if omitted else "Let's try one word."
        return Move(rung, f"{opening} Listen. {word}. Now you say it. {word}.")

    if rung == SEGMENT:
        parts = graphemes.sound_out(word)
        return Move(rung, f"Let's break it up. {parts}. Now put it together. {word}.")

    if rung == TOGETHER:
        # Said in unison - we're not judging this attempt, so don't listen for
        # one. Asking a child to perform after two failures adds pressure at
        # exactly the wrong moment.
        return Move(rung, f"Say it with me. {word}... {word}. Good.", expects_attempt=False)

    return Move(rung, f"That one's tricky. We'll come back to it.", expects_attempt=False)


def echo_prompt(text: str) -> str:
    """Tutor reads the sentence first; the child listens, then echoes.

    Echo reading is how you teach a sentence that's beyond a child's independent
    reach - they hear the phrasing before they attempt it. Without this the
    tutor only ever tests, which is the single biggest thing that was missing.
    """
    return f"Listen to me read it first. {text} ... Now you read it."


def encouragement(streak: int) -> Optional[str]:
    """Something warm when it's earned, and nothing when it isn't.

    Praise for everything is praise for nothing; children work out very quickly
    that it means the adult isn't listening.
    """
    if streak == 3:
        return "Three in a row."
    if streak == 5:
        return "You're on a roll."
    if streak and streak % 10 == 0:
        return "That's a lot of good reading."
    return None
