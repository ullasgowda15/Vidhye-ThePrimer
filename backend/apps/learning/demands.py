"""Difficulty as something a story can actually ask (PRODUCT.md §8.9, §10.4).

`difficulty` is a float, and a float is not a story. §8.9 names the translation
exactly — "count to 3 vs. count to 10; a 2-choice vs. a 3-choice social dilemma;
a familiar vs. a novel word" — and this module is that sentence, written down.

Why it is a table of thresholds and not a prompt line saying "difficulty is 0.62,
use your judgement": a model handed a number invents its own scale, and the scale
it invents is not the one the mastery update reads back. The whole loop in §5.5
depends on the difficulty the child was *asked for* being the difficulty their
answer is evidence about, so the number has to become a countable thing —
`interaction.expected` is 5 — before it reaches the model, not after.

Everything here is pure: no model, no database, no clock. A demand is a function
of an objective and a number, which is what lets the mastery update reconstruct
one from a stored beat, and lets a test assert that two children got different
asks without generating a word.

**Nothing in this module is ever spoken to a child.** It produces constraints for
the writer — how many stepping stones there are — and the writer's job is to make
them stepping stones (§8.10).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from apps.learning.models import Domain

# ---------------------------------------------------------------------------
# The three shapes an ask can take
# ---------------------------------------------------------------------------
#: What `Beat.learning["objective_id"]` says when a beat practises nothing — a
#: transition, a plain decision point, or a safe fallback (§13.3).
#:
#: A sentinel rather than null because the column is a JSON blob a model fills in
#: and "the key is absent" would be indistinguishable from "the model forgot it".
#: It is the one value `engine.evidence_from` refuses to read evidence against, so
#: it is also, precisely, the line between a beat that exercised the objective and
#: a beat that was simply part of the story (§8.10).
NO_OBJECTIVE = "none"

#: Count the visible things. Answered on the number pad; `expected` is an int.
COUNT = "count"
#: Which group has more. Answered by tapping a card; `expected` is an option key.
COMPARE = "compare"
#: Which one is the <word>. Answered by tapping a card; `expected` is an option key.
WORD = "word"

#: Which shape each seeded objective takes.
#:
#: Keyed on the objective code rather than derived from the domain, because the
#: two numeracy shapes are genuinely different questions — "how many" and "which
#: is more" are not the same skill and are not answered with the same control.
#: An unknown code falls back to its domain's usual shape (`kind_for`), so a
#: catalog row added by a later migration still produces a playable beat rather
#: than a crash.
KIND_BY_CODE: dict[str, str] = {
    "count_to_3": COUNT,
    "count_to_5": COUNT,
    "count_to_10": COUNT,
    "more_fewer": COMPARE,
    "name_colours": WORD,
    "size_words": WORD,
    "feeling_words": WORD,
}

KIND_BY_DOMAIN: dict[str, str] = {
    Domain.NUMERACY: COUNT,
    Domain.VOCABULARY: WORD,
}

# ---------------------------------------------------------------------------
# The ladders
# ---------------------------------------------------------------------------
#: `(difficulty below this, how many things to count)`. §8.9's "count to 3 vs.
#: count to 10", as a lookup rather than a formula: the rungs a small child
#: actually climbs are not evenly spaced, and a linear map from a float would put
#: half the range between nine and ten.
COUNT_LADDER: tuple[tuple[float, int], ...] = (
    (0.25, 2),
    (0.45, 3),
    (0.65, 5),
    (0.85, 7),
    (1.01, 10),
)

#: `(difficulty below this, (the bigger group, how much bigger))`. Difficulty in
#: a comparison is carried by the *gap* as much as by the size: telling one from
#: three is a glance, telling eight from nine is counting both.
COMPARE_LADDER: tuple[tuple[float, tuple[int, int]], ...] = (
    (0.35, (3, 2)),
    (0.60, (5, 2)),
    (0.85, (7, 2)),
    (1.01, (9, 1)),
)

#: Above this, the word being practised should be one the child has not met in a
#: story yet — §8.9's "familiar vs. novel word".
NOVEL_WORD_FROM = 0.5

#: Below this — or whenever the child is being scaffolded — a decision point
#: offers two cards rather than three (§8.9, §14.5). Fewer things to hold is a
#: real reduction in difficulty and the only one available on a beat that has no
#: number in it.
THREE_CHOICES_FROM = 0.5

#: How far each counting objective's own ask may go, whatever the ladder says.
COUNT_CEILING: dict[str, int] = {
    "count_to_3": 3,
    "count_to_5": 5,
    "count_to_10": 10,
}

#: The smallest number that is still counting. The bottom rung of
#: `COUNT_LADDER`, named here because it is a claim about children rather than a
#: property of the table.
MIN_COUNT = 2


@dataclass(frozen=True, slots=True)
class Demand:
    """One concrete ask, derived from an objective and a difficulty.

    The fields are what a *beat* needs, not what the engine thinks: how many
    things are in the picture, how many cards are under it, and whether the
    companion is counting along. `story_prompts.learning_rule` turns this into
    the instruction; nothing else may.
    """

    objective_code: str
    domain: str
    difficulty: float
    kind: str
    #: COUNT: how many things to put in the scene. COMPARE: the bigger group.
    #: WORD: unused (0).
    quantity: int
    #: COMPARE only: how much bigger the bigger group is.
    gap: int
    #: How many cards a decision point on this beat may show (§14.5's 2-3).
    choice_count: int
    #: The companion counts along and the cards narrow. Not a smaller ask — the
    #: difficulty step has already done that (`demand_for`), §10.4.
    scaffold: bool
    #: The child found the last one easy, so this one reaches further (§10.4).
    stretch: bool
    #: WORD only: practise a word the child has not met yet.
    novel_word: bool

    @property
    def smaller(self) -> int:
        """COMPARE: the smaller of the two groups, never below one."""
        return max(1, self.quantity - self.gap)

    def summary(self) -> str:
        """One line for the learning-directive layer (§19.1).

        Written as what the *story* must contain, because that is the only form
        of it the writer can act on. "count_to_5 at 0.55" is a fact about our
        database; "five countable things, in the picture" is a fact about a
        riverbank.
        """
        if self.kind == COUNT:
            demand = (
                f"Somewhere in this adventure there are {self.quantity} of something, "
                "countable at a glance, and the child is invited to count them."
            )
        elif self.kind == COMPARE:
            demand = (
                f"Somewhere in this adventure two groups of things sit side by side — "
                f"{self.quantity} of one and {self.smaller} of the other — and the child "
                "is invited to say which has more."
            )
        else:
            familiarity = (
                "a word this child has probably not met yet"
                if self.novel_word
                else "an everyday word"
            )
            demand = (
                f"Somewhere in this adventure {familiarity} is used about something "
                "visible, so its meaning is obvious from the picture, and the child is "
                "invited to point at what it describes."
            )

        parts = [demand, f"Decision points offer {self.choice_count} options, not more."]
        if self.scaffold:
            parts.append(
                "The child needed a hand earlier in this sitting: the companion does it "
                "*with* them and they get there together. Company, never a step back."
            )
        elif self.stretch:
            parts.append("The child found the last one easy: reach a little further.")
        return " ".join(parts)


def kind_for(code: str, domain: str) -> str:
    """Which shape this objective's ask takes."""
    return KIND_BY_CODE.get(code) or KIND_BY_DOMAIN.get(domain, COUNT)


_Rung = TypeVar("_Rung")


def _rung(ladder: Sequence[tuple[float, _Rung]], difficulty: float) -> _Rung:
    """The first rung this difficulty falls below, or the top one."""
    for threshold, value in ladder:
        if difficulty < threshold:
            return value
    return ladder[-1][1]


def demand_for(
    code: str,
    domain: str,
    difficulty: float,
    *,
    scaffold: bool = False,
    stretch: bool = False,
) -> Demand:
    """The concrete ask for one objective at one difficulty (§8.9).

    `scaffold` and `stretch` are the *within-session* adaptation of §10.4 and they
    arrive already decided (`engine.adapt`): this function does not look at
    evidence, it only applies the consequence.

    **Scaffolding does not shrink the count**, and that is worth being explicit
    about because it looks like an omission. The size of the ask has *already*
    come down — a struggle steps `engine.ADAPT_STEP` off the difficulty, and the
    difficulty is what indexes the ladder. Taking another one off here would drop
    a child from five things to two for one wobble, which is a beat that reads as
    a demotion to anyone watching. What scaffolding adds is company: the companion
    counts along, and the decision points narrow to two cards. Company first, then
    a smaller ask (§8.5, §10.4).

    A stretch has already been paid for in the difficulty too, and is passed
    through only so the writer can be told why it is reaching further.
    """
    difficulty = min(max(float(difficulty), 0.0), 1.0)
    kind = kind_for(code, domain)
    choice_count = 3 if difficulty >= THREE_CHOICES_FROM and not scaffold else 2

    quantity, gap, novel = 0, 0, False
    if kind == COUNT:
        quantity = _rung(COUNT_LADDER, difficulty)
        # The objective's own ceiling wins over the ladder: `count_to_3` at a high
        # difficulty asks for three things, not seven. The objective names what is
        # being practised; difficulty only says how hard to make *that*.
        quantity = max(MIN_COUNT, min(quantity, COUNT_CEILING.get(code, quantity)))
    elif kind == COMPARE:
        quantity, gap = _rung(COMPARE_LADDER, difficulty)
        if scaffold:
            # A wider gap, not a smaller world: "which pile is bigger" is easiest
            # when the answer is obvious from across the room.
            gap = min(quantity - 1, gap + 1)
    else:
        novel = difficulty >= NOVEL_WORD_FROM and not scaffold

    return Demand(
        objective_code=code,
        domain=domain,
        difficulty=difficulty,
        kind=kind,
        quantity=quantity,
        gap=gap,
        choice_count=choice_count,
        scaffold=scaffold,
        stretch=stretch and not scaffold,
        novel_word=novel,
    )
