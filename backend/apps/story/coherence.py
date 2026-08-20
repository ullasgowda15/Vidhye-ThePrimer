"""Does a counting beat agree with itself, before a child is ever asked? (§8.5, §8.9)

**The bug this exists to prevent.** A counting beat makes three claims that have
to be the same claim: the engine picks a quantity (`Demand.quantity`), the writer
sets `interaction.expected`, and the writer describes the things in
`scene.illustration_prompt`. Nothing checked that those three agreed, so a beat
could ask "how many frogs?" with `expected` of 2 while its own illustration
prompt asked for three frogs. The child counts three, the companion had two in
mind, and the one number the child can actually see is the wrong one.

That is worse than an ordinary defect. §8.5 promises no fail states, and the
promise is kept by never telling a child they are wrong — but a question whose
visible answer disagrees with the recorded one still confuses the child, and it
still writes false evidence into §5.5's difficulty loop. The child is then
*re-levelled* on the strength of an answer that was right about the picture in
front of them. A wrong number is a bad beat; a wrong number that moves the
difficulty is a bad beat that keeps costing.

**Why the check is on the words and not on the pixels.** The obvious guard is to
look at the rendered image and count. It cannot work here, and the reason is in
`streaming.py`: the `beat` event carries the question and is emitted the moment
the text validates, with `scene.status` still `pending`. The illustration arrives
afterwards, as a separate `media` frame. So by the time there are pixels to
count, the child has already been asked. Gating the question on a vision call
would mean making a three-year-old wait for two model calls instead of one, which
is the exact latency §4.9 forbids. What *can* be made airtight before the child
sees anything is that the beat does not contradict itself, so that is what this
does.

**The residual risk this does not close, stated plainly.** Diffusion models
cannot reliably render an exact number of anything. A prompt that correctly says
"exactly 7 frogs" can still come back with six. This module guarantees that the
question, the expected answer and the *instruction given to the illustrator* are
one number; it cannot guarantee the illustrator obeyed. See `COUNT_LADDER` — the
top rungs ask for 7 and 10, and no amount of prompt discipline makes those
dependable. That is a product decision, flagged rather than silently patched.

**The contract is `exactly N`.** `COUNT_RULE` and `COMPARE_RULE` require the
illustration prompt to spell the quantity as the literal phrase `exactly 3
(three)`. A required literal is what makes this checkable without natural-language
parsing: "three frogs by two stones" is ambiguous to a regex and unambiguous to
nobody, whereas `exactly 3` either is or is not present. The phrasing is also good
for the image model, which reads a digit more reliably than a number word — hence
both.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from apps.learning import demands as learning_demands
from apps.story.ai.schemas import InteractionType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from apps.learning.demands import Demand
    from apps.story.ai.schemas import Beat as BeatContract

#: Problem codes. Names, not sentences, because they are logged as a category and
#: read as a rate — the sentence a regeneration is given lives in `constraint_for`.
EXPECTED_MISMATCH = "count_expected_mismatch"
EXPECTED_NOT_A_NUMBER = "count_expected_not_a_number"
ILLUSTRATION_SILENT = "count_illustration_silent"
ILLUSTRATION_CONFLICT = "count_illustration_conflict"
EXPECTED_NOT_A_CHOICE = "compare_expected_not_a_choice"

#: How a quantity may be written after `exactly`. Digits and words both, because
#: the rule asks for `exactly 3 (three)` and a writer that drops one half has
#: still said the number — refusing that would be pedantry with a rewrite attached.
_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

#: `exactly 3`, `exactly three`. Deliberately anchored on the literal word so this
#: never has to guess whether a bare numeral in a prompt is a count of the thing
#: being counted or part of the scenery.
_EXACTLY = re.compile(
    r"\bexactly\s+(\d{1,2}|" + "|".join(_WORDS) + r")\b",
    re.IGNORECASE,
)


def stated_quantities(illustration_prompt: str) -> set[int]:
    """Every quantity the prompt commits to with `exactly`.

    A set rather than a list: `exactly 3 (three) frogs` says one number twice, and
    a comparison legitimately says two different ones.
    """
    found: set[int] = set()
    for raw in _EXACTLY.findall(illustration_prompt or ""):
        token = raw.lower()
        value = _WORDS.get(token)
        if value is None:
            try:
                value = int(token)
            except ValueError:  # pragma: no cover - the pattern cannot produce this
                continue
        found.add(value)
    return found


def count_problems(contract: BeatContract, demand: Demand | None) -> tuple[str, ...]:
    """What is wrong with this beat's counting contract, if anything.

    Empty tuple means the three claims agree. Only ever called for the beat the
    engine actually asked to be a counting beat — a writer that answered a
    counting demand with a `choose` has not broken *this* contract, and the
    objective simply goes unexercised (`learning_tag_for` already records that
    honestly).
    """
    if demand is None or demand.kind != learning_demands.COUNT:
        return ()
    if contract.interaction.type != InteractionType.COUNT:
        return ()

    problems: list[str] = []
    expected = contract.interaction.expected

    # Catches the writer answering in words ("three"). It cannot catch a bool:
    # `expected` is typed `int | str | None`, and Pydantic has already coerced a
    # `True` to `1` by the time a contract exists, so there is no branch to write.
    if not isinstance(expected, int):
        problems.append(EXPECTED_NOT_A_NUMBER)
    elif expected != demand.quantity:
        problems.append(EXPECTED_MISMATCH)

    stated = stated_quantities(contract.scene.illustration_prompt)
    if not stated:
        problems.append(ILLUSTRATION_SILENT)
    elif stated != {demand.quantity}:
        problems.append(ILLUSTRATION_CONFLICT)

    return tuple(problems)


def compare_problems(contract: BeatContract, demand: Demand | None) -> tuple[str, ...]:
    """The same question for a "which group has more" beat.

    Two groups, so two quantities, and the answer is a choice key rather than a
    number. The key cannot be checked for *meaning* here — whether `left_pile`
    really is the bigger one is a fact about the sentence, not about the data — but
    it can be checked for existing, which is the failure that would leave the child
    tapping a card the beat has no record of.
    """
    if demand is None or demand.kind != learning_demands.COMPARE:
        return ()
    if contract.interaction.type != InteractionType.CHOOSE:
        return ()

    problems: list[str] = []
    stated = stated_quantities(contract.scene.illustration_prompt)
    wanted = {demand.quantity, demand.smaller}
    if not stated:
        problems.append(ILLUSTRATION_SILENT)
    elif stated != wanted:
        problems.append(ILLUSTRATION_CONFLICT)

    keys = {option.key for option in contract.interaction.choices or ()}
    expected = contract.interaction.expected
    if not isinstance(expected, str) or expected not in keys:
        problems.append(EXPECTED_NOT_A_CHOICE)

    return tuple(problems)


def problems_for(contract: BeatContract, demand: Demand | None) -> tuple[str, ...]:
    """Both checks, for the one call site that does not care which kind it is."""
    return count_problems(contract, demand) + compare_problems(contract, demand)


#: The problems where the beat disagrees with *itself*, and which therefore must
#: never reach a child: the question asks for one number and the picture was
#: commissioned with another, or the answer is not the kind of thing the child can
#: give. There is no reading of these that is merely unlucky.
CONTRADICTIONS = frozenset(
    {
        EXPECTED_MISMATCH,
        EXPECTED_NOT_A_NUMBER,
        ILLUSTRATION_CONFLICT,
        EXPECTED_NOT_A_CHOICE,
    }
)


def contradictions(problems: Sequence[str]) -> tuple[str, ...]:
    """The subset worth spending a regeneration — and a repair — on.

    `ILLUSTRATION_SILENT` is deliberately *not* in it, and the distinction is the
    whole calibration of this module. A prompt that never says how many to draw is
    a beat carrying a risk: the illustrator will draw some number, and it may well
    be the right one. A prompt that says four while the question says three is a
    beat carrying a certainty. Treating those the same way costs a child their
    counting beat every time a writer merely forgot the literal — the objective
    is dropped, the difficulty loop gets no evidence, and all of it to fix
    something that might not have been broken.

    So silence is logged and shipped, and disagreement is regenerated and, if it
    survives that, repaired.
    """
    return tuple(code for code in problems if code in CONTRADICTIONS)


#: The header a coherence regeneration carries. Same shape as
#: `story_prompts.safety_constraint`: it names the defect and never quotes the
#: text, so the retry is not handed the mistake to copy.
_HEADER = (
    "Your previous attempt was not used. The counting in it did not agree with "
    "itself, and a child would have been asked a question whose answer they could "
    "not see. Fix exactly this and change nothing else about the story:"
)

_GUIDANCE: dict[str, str] = {
    EXPECTED_NOT_A_NUMBER: (
        "interaction.expected must be a plain whole number for a counting beat — "
        "not a word, not a string, not null."
    ),
    EXPECTED_MISMATCH: (
        "interaction.expected must be the exact quantity you were asked for above. "
        "It is the number the child will see in the picture, so it is not yours to "
        "adjust."
    ),
    ILLUSTRATION_SILENT: (
        "scene.illustration_prompt must state the quantity as the literal phrase "
        "'exactly N (word)' — for example 'exactly 3 (three) round green frogs "
        "sitting in a row on a log'. Without that phrase the illustrator is not "
        "told how many to draw."
    ),
    ILLUSTRATION_CONFLICT: (
        "scene.illustration_prompt states a different quantity from the one the "
        "child is being asked about. Every 'exactly N' in the prompt must be the "
        "quantity you were asked for, and there must be no other group of the same "
        "kind of thing anywhere in the scene."
    ),
    EXPECTED_NOT_A_CHOICE: (
        "interaction.expected must be the key of one of the options you listed in "
        "interaction.choices."
    ),
}


def constraint_for(problems: Sequence[str]) -> str:
    """The extra instruction a coherence regeneration carries.

    Deduplicated and ordered by `_GUIDANCE` rather than by the order the problems
    were found, so two beats with the same defects get byte-identical constraints
    and the retry is reproducible.
    """
    seen = [code for code in _GUIDANCE if code in set(problems)]
    return "\n\n".join([_HEADER, *(_GUIDANCE[code] for code in seen)])


def align_to_illustration(contract: BeatContract) -> BeatContract | None:
    """Last resort: make the question agree with the picture it will be shown with.

    Reached only when regeneration has been tried and the writer is still
    contradicting itself. The choice is between two bad options, and this is the
    less bad one: the illustrator has been told a number, the child will see that
    number, so the *question* is the thing to move.

    What this costs is honest and bounded — the beat exercises the objective at a
    slightly different quantity than the engine chose, and `learning_tag_for`
    records the difficulty that was asked for, not the one delivered. What it buys
    is that the child is never asked to count to a number that is not in front of
    them.

    Returns `None` when the illustration prompt commits to no single quantity, in
    which case there is nothing to align *to* and the caller demotes the beat
    instead.
    """
    stated = stated_quantities(contract.scene.illustration_prompt)
    if len(stated) != 1:
        return None
    quantity = next(iter(stated))
    interaction = contract.interaction.model_copy(update={"expected": quantity})
    return contract.model_copy(update={"interaction": interaction})


def demote(contract: BeatContract) -> BeatContract:
    """Strip the interaction entirely, leaving a beat that is only a story.

    The floor of the whole mechanism. A beat with `type: none` is a beat the child
    watches and hears, which every part of the client already handles — it is what
    a purely narrative beat looks like. Losing one learning ask is a real cost and
    a small one; asking a child to count something that is not there is neither.
    """
    interaction = contract.interaction.model_copy(
        update={"type": InteractionType.NONE, "expected": None, "choices": None}
    )
    return contract.model_copy(update={"interaction": interaction})
