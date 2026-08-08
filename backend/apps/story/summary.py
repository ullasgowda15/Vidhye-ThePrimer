"""The running summary that keeps a long story's prompt short (PRODUCT.md §8.6).

Continuity runs at three timescales. Within an adventure it is the story plan plus
the log of what the child has done — and the naive way to give a model that log is
to paste every beat so far into every request. That works for beat two and is a
cost bug by beat six: the prompt grows linearly, every beat costs more than the
one before it, and the last beat of an adventure — the warm close, the one the
child remembers — is the most expensive thing we generate.

So the live prompt carries a **bounded** summary instead: the last few beats
verbatim, and everything older folded into a single line. The bound is arithmetic,
not a hope — `SUMMARY_MAX_CHARS` is derived from the three numbers below and is
the same whether the adventure ran four beats or forty.

**What compaction throws away, and why that is the right trade.** The fold keeps
the opening — which establishes where the child is and who is with them — and then
fills backwards from the most recent, because the beats nearest the live window
are the ones the next beat has to be coherent *with*. The middle of a long
adventure is what gets dropped. That is a real loss and it is deliberate: the
detail worth keeping past one sitting belongs in long-term memory (Day 8), which
is retrieved and ranked rather than carried along, and paying to carry it in every
prompt would be paying twice for the worse copy.

The summary is **recomputed from the rows** rather than appended to. There are at
most a handful of beats in an adventure, so it is cheap, and it means the summary
cannot drift from the story it describes — a half-applied append is a companion
who remembers something that did not happen.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.story.models import Adventure, Beat, Choice

#: How many beats stay in the prompt word for word. Three is one full exchange of
#: cause and effect plus the beat before it, which is what a consequence has to be
#: written against; more is history, and history is what the fold is for.
SUMMARY_RECENT_BEATS = 3

#: The most one beat may contribute. Long enough for a whole opening sentence and
#: what the child chose, short enough that three of them are not a transcript.
SUMMARY_LINE_CHARS = 160

#: The whole of "everything before the recent window", in one line.
SUMMARY_HEAD_CHARS = 400

HEAD_PREFIX = "Earlier: "

#: The ceiling `compact_summary` cannot exceed, whatever the adventure did. This
#: is the property the whole module exists for, so it is a named number the test
#: can assert against rather than a claim in a docstring.
SUMMARY_MAX_CHARS = (
    len(HEAD_PREFIX) + SUMMARY_HEAD_CHARS + SUMMARY_RECENT_BEATS * (SUMMARY_LINE_CHARS + 1)
)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def compact_summary(adventure: Adventure) -> str:
    """The story so far, bounded (§8.6).

    Never longer than `SUMMARY_MAX_CHARS`, regardless of how many beats have been
    told. This is what `Adventure.running_summary` holds and what the assembler's
    `story_plan` layer renders as "Story so far".
    """
    entries = _entries(adventure)
    if not entries:
        return ""
    if len(entries) <= SUMMARY_RECENT_BEATS:
        return "\n".join(entries)

    older, recent = entries[:-SUMMARY_RECENT_BEATS], entries[-SUMMARY_RECENT_BEATS:]
    return "\n".join([_fold(older), *recent])


def refresh_running_summary(adventure: Adventure) -> str:
    """Recompute the summary and persist it. Returns the new text.

    Called after each beat is written and after each choice is recorded, because
    those are the two things that change what "the story so far" is.
    """
    summary = compact_summary(adventure)
    if summary != adventure.running_summary:
        adventure.running_summary = summary
        adventure.save(update_fields=["running_summary", "updated_at"])
    return summary


def _entries(adventure: Adventure) -> list[str]:
    """One line per beat, in order, each already clipped to its budget."""
    beats = adventure.beats.order_by("seq").prefetch_related("choices")
    child_name = adventure.child.first_name
    return [_entry(beat, child_name) for beat in beats]


def _entry(beat: Beat, child_name: str) -> str:
    """What one beat contributed to the story, and what the child did about it."""
    parts = [f"{beat.seq}. {_gist(beat.narration)}"]

    choice: Choice | None = next(iter(beat.choices.all()), None)
    if choice is not None and choice.chosen_key:
        label = _label_for(beat, choice)
        parts.append(f"{child_name} chose {label}.")
        if choice.assisted:
            # Carried because the *next* beat has to react to it warmly (§8.5),
            # not because it is a mark against the child.
            parts.append("They needed a hand, and got one.")

    return _clip(" ".join(parts), SUMMARY_LINE_CHARS)


def _label_for(beat: Beat, choice: Choice) -> str:
    """How the child's pick reads in a sentence.

    Prefers the words the companion actually said out loud over the internal key,
    because the summary is prose a model reads back and `follow_river` is not a
    thing anybody said.
    """
    option = beat.option(choice.chosen_key)
    label = str((option or {}).get("label") or "").strip()
    return f'"{label}"' if label else f"'{choice.chosen_key}'"


def _gist(narration: str) -> str:
    """The first sentence of a beat — what happened, without how it was told."""
    collapsed = " ".join(narration.split())
    first = _SENTENCE_END.split(collapsed, maxsplit=1)[0]
    return first or collapsed


def _fold(older: list[str]) -> str:
    """Everything before the recent window, in one line within `SUMMARY_HEAD_CHARS`.

    The opening is kept whatever else goes, because it is where the child is and
    who is with them; the rest is filled in backwards from the newest, because
    those are the beats the next one has to follow on from. Anything that does not
    fit between the two is dropped — see the module docstring on why that loss
    belongs to Day 8's memory rather than to this line.
    """
    opening = _clip(older[0], SUMMARY_HEAD_CHARS)
    budget = SUMMARY_HEAD_CHARS - len(opening)

    tail: list[str] = []
    for entry in reversed(older[1:]):
        cost = len(entry) + 1  # the separating space
        if cost > budget:
            break
        tail.append(entry)
        budget -= cost

    return HEAD_PREFIX + " ".join([opening, *reversed(tail)])


def _clip(text: str, limit: int) -> str:
    """`text`, no longer than `limit`, cut on a word boundary where there is one."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0] or text[: limit - 1]
    return f"{cut}…"
