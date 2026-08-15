"""
Turning a detected error into something a tutor would actually say.

Everything here is written to be spoken aloud to a five-to-seven year old, not
read on a screen. Short sentences, no jargon, name the sound rather than the
rule. The child is told what to notice and then asked to try again - a diagnosis
on its own ("you said watch instead of wash") teaches nothing.
"""

import random
from typing import List, Optional

from ..content import skills
from ..detection import STATUS_OMISSION, STATUS_SUBSTITUTION, WordResult
from .brief import Brief

# Errors clear-cut enough to stop a child on. Notably excludes "unclear".
COACHABLE = {STATUS_SUBSTITUTION, STATUS_OMISSION}

# Moments the tutor speaks at.
COACH = "coach"
COACH_AGAIN = "coach_again"
RETRY_SUCCESS = "retry_success"
GIVE_UP = "give_up"
REREAD = "reread"
PRAISE = "praise"

PRAISE_LINES = [
    "Nice reading!",
    "That was great.",
    "Lovely job.",
    "You read all of that.",
]

RETRY_SUCCESS_LINES = [
    "That's it!",
    "Yes! That's the one.",
    "Perfect.",
    "You got it.",
]

GIVE_UP_LINES = [
    "That one's tricky. We'll come back to it.",
    "Good try. Let's keep going.",
    "That's a hard one. Never mind.",
]

# Said once, at the end of a sitting. Warm, and finished - a child should be
# left wanting to come back rather than wondering why it stopped.
FAREWELL_LINES = [
    "That's all for today. You worked hard.",
    "All done. That was great reading.",
    "That's enough for today. Well done.",
]


def _pick(options: List[str]) -> str:
    return random.choice(options)


def choose_focus(words: List[WordResult]) -> Optional[WordResult]:
    """Which single error is worth stopping on?

    Only one, even when there are several - a tutor doesn't list every mistake,
    they pick the one worth teaching. Words that exercise a phonics skill come
    first; a missed "the" matters less than a missed "ship". Otherwise, earliest
    in the passage, so the correction follows the reading.

    "Unclear" words are deliberately excluded. That status comes from the
    engine's confidence score, which the spikes showed is unreliable - and in
    practice it fires on accented pronunciation. A child who says "paath" for
    "path" has decoded the word correctly; stopping them teaches that their
    accent is wrong, which is not what this product is for. We assess decoding,
    not accent, so coaching needs a clear substitution or omission.
    """
    errors = [w for w in words if w.status in COACHABLE and w.expected]
    if not errors:
        return None

    teaching = [w for w in errors if skills.skill_for_word(w.expected)]
    pool = teaching or errors
    return min(pool, key=lambda w: w.index)


def say(brief: Brief) -> str:
    """Render a Brief into words.

    The single place tutor speech is produced. Swapping templates for a language
    model means replacing this function and nothing else - the Brief already
    carries the session context a model would need.
    """
    if brief.moment == COACH:
        return _coach(brief)
    if brief.moment == COACH_AGAIN:
        return _coach_again(brief)
    if brief.moment == RETRY_SUCCESS:
        return _pick(RETRY_SUCCESS_LINES)
    if brief.moment == GIVE_UP:
        return _pick(GIVE_UP_LINES)
    if brief.moment == REREAD:
        return reread()
    return _pick(PRAISE_LINES)


def _coach(brief: Brief) -> str:
    """Set up a retry, in words a five year old can act on.

    Three rules, learned from reading the earlier version aloud and hearing how
    abstract it was:

    - Model the sound before asking for it. "I didn't hear the sh sound" asks a
      child to notice an absence; "listen - sh" gives them something to copy.
    - Never explain spelling. "S and h together say sh" is a fact about letters
      aimed at someone who can already read. They're being asked to say a
      sound, so say the sound.
    - End on the word, so the last thing they hear is the thing to repeat.
    """
    word = brief.word

    if brief.omitted:
        opening = f"You missed one. This word is {word}."
    elif brief.mastered_this_sound_before:
        opening = f"You know this one. {word}."
    elif brief.doing_well:
        opening = f"Nice. Just one word. {word}."
    else:
        opening = f"Let's try one word. {word}."

    if brief.sound:
        return f"{opening} Listen. {brief.sound}... {word}. Your turn. {word}."
    return f"{opening} Listen. {word}. Your turn. {word}."


def _coach_again(brief: Brief) -> str:
    """Second go. Slower and more sound, fewer words - repeating the same
    explanation louder doesn't help anyone."""
    if brief.sound:
        return f"Listen again. {brief.sound}... {brief.sound}... {brief.word}. Say it with me. {brief.word}."
    return f"Listen again. {brief.word}. Say it with me. {brief.word}."


def farewell() -> str:
    return _pick(FAREWELL_LINES)


def wind_down() -> str:
    """Ending early because it's gone hard, not because the work is done.

    Said after a run of struggle. It has to close warmly and without blame - a
    child who leaves feeling they failed doesn't come back, and coming back is
    the whole thing.
    """
    return _pick(
        [
            "Let's stop there for today. That was hard work and you kept going.",
            "That's enough for today. Some of those were really tricky.",
        ]
    )


def reread() -> str:
    return "Now read the whole thing again."


def confirmed() -> str:
    return "Much better."


def opening() -> str:
    return "Read this out loud for me."


def summary_for_focus(focus_word: WordResult) -> str:
    """Short line for the parent panel, describing what was heard."""
    if focus_word.status == STATUS_OMISSION:
        return f"Skipped “{focus_word.expected}”"
    if focus_word.detected and focus_word.detected.lower() != focus_word.expected:
        return f"Read “{focus_word.expected}” as “{focus_word.detected}”"
    return f"“{focus_word.expected}” was unclear"
