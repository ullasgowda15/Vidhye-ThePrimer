"""
How is this child doing *right now*?

Mastery says what they know over weeks. This says whether the last five minutes
are going well, which is a different question and drives different decisions.

A tutor who keeps pushing a child that has failed four times running isn't
devoted, they're not paying attention. Equally, a tutor who models every
sentence to a child who is reading fluently is wasting their time and slightly
insulting them. Both are failures to read the room, and neither is visible in a
mastery table.
"""

from dataclasses import dataclass

# Struggling this many passages in a row means the material is too hard right
# now - not that the child is failing.
STRUGGLING_STREAK = 3

# Enough consecutive clean reads to stop offering help that isn't needed.
FLOWING_STREAK = 2

# After this much struggle, wind the session down early rather than grinding on.
GIVE_UP_STREAK = 5


@dataclass
class Rapport:
    """The shape of the last few minutes."""

    clean_streak: int = 0  # passages read with no coaching needed
    struggle_streak: int = 0  # passages that needed coaching
    laboured_reads: int = 0  # readings slow enough to be word-by-word
    passages_done: int = 0

    def record(self, needed_help: bool, laboured: bool) -> None:
        if needed_help:
            self.struggle_streak += 1
            self.clean_streak = 0
        else:
            self.clean_streak += 1
            self.struggle_streak = 0

        if laboured:
            self.laboured_reads += 1
        self.passages_done += 1

    @property
    def flowing(self) -> bool:
        """Reading well enough that help would just get in the way."""
        return self.clean_streak >= FLOWING_STREAK

    @property
    def struggling(self) -> bool:
        """Enough consecutive difficulty that the next passage should be
        modelled first rather than tested."""
        return self.struggle_streak >= STRUGGLING_STREAK

    @property
    def should_wind_down(self) -> bool:
        """Time to finish warmly.

        A child who has struggled five passages running has stopped learning and
        started failing. Ending early leaves them willing to come back, which
        matters more than one more attempt.
        """
        return self.struggle_streak >= GIVE_UP_STREAK

    def wants_modelling(self, *, new_skill: bool, weak_skill: bool) -> bool:
        """Should the tutor read this one first?

        Model when the child is meeting a sound for the first time, when the
        passage targets something they're weak at, or when the last few minutes
        have gone badly. Those are the three moments where hearing it read
        properly is worth more than being asked to perform it.

        Not when they're flowing - they've earned the space, and reading to a
        child who is reading fine is just getting in the way.

        `weak_skill` matters more than it looks. Without it, modelling only
        fired for a brand new sound or a three-passage collapse, which meant a
        child with any history essentially never heard the tutor read at all -
        the teaching half of the loop was unreachable in normal use.
        """
        if self.flowing:
            return False
        return new_skill or weak_skill or self.struggling
