"""Turning story rows into short-term memory events (PRODUCT.md §11.2, §5.4).

`stm` is a store and knows nothing about stories. This module is the translation
layer: it reads a `Beat` or a `Choice` and decides what about it is worth holding
in working memory for the next couple of hours. It is the *only* place that
mapping lives, so "what does the consolidation job get to see" has one answer.

Three rules shape what goes in.

**Signals, not text.** The narration is already persisted on the beat, and the
consolidation job reads it from there. What STM adds is the stuff that exists
nowhere else once the sitting closes: how long the child hovered, how many times
they tried, whether the companion helped, what order it all happened in. A beat's
first clause goes in as an *anchor* so the log reads as a story rather than as a
column of ids, and nothing longer than that does (§13.8).

**Child-initiated first.** §11.4 weighs "things the child chose/said/named"
heaviest, and §11.5 makes them first-class memory types. So anything the child
themselves produced — a spoken answer, a name, a branch they picked — is both
appended to the Redis log *and* mirrored onto the session row, which is the copy
that survives a Redis restart. Everything else lives in Redis alone.

**Never on the critical path's failure list.** Every function here returns `None`
and swallows what `stm` swallows. It is called from `orchestrator._persist_beat`
and `orchestrator.record_choice`, both of which are between a child and their
story, and the enrichment is worth strictly less than the beat.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from apps.memory import stm
from apps.story.models import Beat, Choice, PlaySession

logger = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")

#: Interaction types whose answer is the child's own words rather than a tap on
#: something we offered. These are the §11.5 cases — a name, a wish, an answer
#: nobody put on a card — and they are what "child-initiated" most strongly means.
_UTTERANCE_TYPES = frozenset({"say"})


def anchor(narration: str) -> str:
    """The first clause of a beat, as a handle on what happened.

    Same idea as `story.summary._gist` and deliberately not shared with it: that
    one builds prose a model reads back inside a live prompt, and this one labels
    a log entry. Coupling them would mean a change to how prompts read silently
    changing what we remember.
    """
    collapsed = " ".join(narration.split())
    first = _SENTENCE_END.split(collapsed, maxsplit=1)[0]
    return (first or collapsed)[: stm.MAX_TEXT_CHARS]


def beat_recorded(beat: Beat) -> None:
    """Record that a beat was told (§11.2's "every beat")."""
    session = _session_of(beat.adventure)
    if session is None:
        return

    interaction = beat.interaction or {}
    learning = beat.learning or {}
    scene = beat.scene or {}
    new_characters = [
        str(entry.get("key") or entry.get("name") or "")
        for entry in (scene.get("new_characters") or [])
        if isinstance(entry, dict)
    ]

    stm.record(
        session.pk,
        stm.BEAT,
        adventure=str(beat.adventure_id),
        beat=str(beat.pk),
        seq=beat.seq,
        anchor=anchor(beat.narration),
        emotion=beat.emotion,
        asks=str(interaction.get("type") or "none"),
        objective=str(learning.get("objective_id") or ""),
        evidence_key=str(learning.get("skill_evidence_key") or ""),
        # Both halves of the entity picture: what the beat leaned on, and who it
        # invented. The second is how a recurring character becomes an `entity`
        # memory rather than a face the child never hears of again (§11.5).
        refs=list(beat.continuity_refs or []),
        introduced=[key for key in new_characters if key],
        safety=beat.safety_status,
    )
    stm.mirror(session, stm.BEAT)


def choice_recorded(beat: Beat, choice: Choice) -> None:
    """Record what the child did, and how it went for them (§11.2, §11.4).

    Called on the created path only. A double-tap is not a second decision — it is
    the same decision arriving twice (`orchestrator.record_choice`) — and counting
    it as repetition would inflate the salience of whichever beat had the biggest
    tap target.
    """
    session = _session_of(beat.adventure)
    if session is None:
        return

    option = beat.option(choice.chosen_key) or {}
    label = str(option.get("label") or "")
    asked = str((beat.interaction or {}).get("type") or "none")
    spoken = _spoken_answer(asked, choice.value)

    stm.record(
        session.pk,
        stm.CHOICE,
        adventure=str(beat.adventure_id),
        beat=str(beat.pk),
        seq=beat.seq,
        asks=asked,
        chosen_key=choice.chosen_key,
        chosen_label=label,
        # The child's own answer, as they gave it. The one piece of genuinely
        # child-authored text in the log, and the reason `guard_child_utterance`
        # has already seen it by the time we get here (§13.10).
        value=None if choice.value is None else str(choice.value),
        # Dwell. §11.2 names it, and it is the signal that separates "thought
        # about it" from "tapped the first thing" — an input to difficulty (§10.4)
        # and to how much a moment mattered.
        dwell_ms=choice.latency_ms,
        retries=choice.retries,
        assisted=choice.assisted,
        options_offered=len(beat.options),
    )

    # The mirror gets the counters always and the highlight only when the child
    # actually originated something. A tap on "carry on" is a fact about the
    # sitting; naming the dragon is a fact about the child.
    highlight = ""
    if spoken:
        highlight = f"said: {spoken}"
    elif label and len(beat.options) > 1:
        highlight = f"chose: {label}"
    stm.mirror(
        session,
        stm.CHOICE,
        highlight=highlight,
        retries=choice.retries or 0,
        assisted=1 if choice.assisted else 0,
        dwell_ms=choice.latency_ms or 0,
    )


def session_closing(session: PlaySession | None, *, adventure_id: Any, reason: str) -> None:
    """Mark the end of the sitting in the log, just before consolidation runs.

    Recorded rather than inferred because `ended_reason` is a wellbeing signal
    (§13.6) and the consolidation prompt should be able to tell a warm close from
    a child who wandered off — the same session, the same beats, and two quite
    different things to remember about it.
    """
    if session is None:
        return
    stm.record(
        session.pk,
        stm.SESSION_END,
        adventure=str(adventure_id),
        reason=reason,
    )
    stm.mirror(session, stm.SESSION_END)


def _spoken_answer(asked: str, value: Any) -> str:
    """The child's answer when it was words rather than a tap, else `""`."""
    if asked not in _UTTERANCE_TYPES or value is None:
        return ""
    text = str(value).strip()
    return text[: stm.MAX_TEXT_CHARS] if text else ""


def _session_of(adventure: Any) -> PlaySession | None:
    """The sitting an adventure belongs to, or `None`.

    Nullable by design (§17.2): a session row may be pruned by a retention job
    long before the adventure stops mattering. No session means no STM key, which
    means consolidation for that adventure runs on the persisted rows alone —
    a smaller bundle, not a broken one.
    """
    try:
        return adventure.session
    except Exception:  # pragma: no cover - a lazy FK on a deleted row
        return None
