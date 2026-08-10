"""Proposing what a sitting might be worth remembering (PRODUCT.md §11.4, §11.5).

Consolidation has two candidate sources and this is the deterministic one. It
reads the persisted beats and choices plus the STM log and proposes typed
candidates; `apps.memory.prompts` asks a model for the ones that need reading
between the lines. Both are then scored by `apps.memory.salience`, which is what
keeps "how much did this matter" one answer rather than two.

**Why a rules half at all, when there is a model half.** Because the memories
§11.5 calls first-class are exactly the ones a model is worst at and rules are
best at: *named the dragon Sparkle*, *chose to help the lost frog*, *counted to
three unaided*. Those are facts sitting in columns — a `say` answer, a
`chosen_key`, a `Character` row with a canonical descriptor — and asking a
language model to rediscover them from a summary would be paying a provider to be
less reliable than a `for` loop. The model earns its call on the other half: the
emotional shape of the sitting, the thing that was funny, the moment a child
softened. So the rules floor is never empty and never depends on a network, and
the model adds what it is actually good at.

**Where the facts come from, and in which order.** STM (§11.2) is the primary
source for the *shape* of the sitting — the ordered log, what the child said, why
it ended — and the persisted rows are the floor underneath it. That direction
matters: the log is what §11.1 asks us to build memory from, and a Redis outage
must degrade the bundle rather than empty it. In this architecture the rows carry
most of the same signals (dwell, retries and assists are columns on `Choice`
because §8.9 needs them for difficulty), which is a happy redundancy rather than
a reason to skip either — the rows survive Redis, and the log survives the
retention job that will eventually prune the rows (§17.5).

**Nothing here decides anything.** Every function returns proposals. The threshold
and the cap live in `salience`, so the question "why do we have this memory" has
one place to look.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from apps.companion.models import Character
from apps.memory import stm
from apps.memory.models import MemoryType
from apps.memory.salience import Candidate, Signals
from apps.story.models import Adventure, Beat, Choice, PlaySession

logger = logging.getLogger(__name__)

#: How strongly each kind of act counts as "the child initiated this" (§11.4).
#:
#: Graded rather than boolean, because the difference between a child *authoring*
#: something and a child *picking* something is the difference between the two
#: best memory types we have. Naming a creature is theirs in a way that tapping
#: the left card of two is not, and tapping the only card there was is not an act
#: of choosing at all.
SPOKE = 1.0
CHOSE_A_BRANCH = 0.6
ANSWERED = 0.4
CARRIED_ON = 0.0

#: The child met someone the world will remember and re-draw (`companion.Character`).
#: Not authored by them, but not ours either — they were there when it happened.
MET_SOMEONE = 0.5

#: Emotion words, as `(intensity, valence)`. `Beat.emotion` is a free-text
#: snake_case compound from the generator ("playful_curious", "tender_brave"), so
#: this is a lookup over its parts rather than an enum: the model may reasonably
#: coin a compound we have not seen, and an unknown word must read as "some
#: feeling, unremarkable" rather than as no feeling at all.
_EMOTION_WORDS: dict[str, tuple[float, float]] = {
    "delighted": (0.9, 0.9),
    "delight": (0.9, 0.9),
    "joyful": (0.9, 0.9),
    "joy": (0.9, 0.9),
    "triumphant": (0.9, 0.9),
    "proud": (0.9, 0.8),
    "excited": (0.8, 0.7),
    "brave": (0.8, 0.6),
    "awed": (0.8, 0.6),
    "awe": (0.8, 0.6),
    "grateful": (0.7, 0.7),
    "tender": (0.6, 0.6),
    "wonder": (0.6, 0.5),
    "wondering": (0.6, 0.5),
    "surprised": (0.6, 0.3),
    "kind": (0.5, 0.6),
    "curious": (0.5, 0.4),
    "playful": (0.4, 0.5),
    "warm": (0.4, 0.6),
    "puzzled": (0.4, 0.0),
    "content": (0.3, 0.4),
    "cosy": (0.3, 0.5),
    "cozy": (0.3, 0.5),
    "calm": (0.2, 0.3),
    "gentle": (0.2, 0.3),
    "sleepy": (0.2, 0.2),
    # The negative half. Present and weighted high on *intensity* because §11.6
    # needs them: a fear or a wobble is exactly what the engine has to know in
    # order to be gentle about it next time. Valence is what keeps it from being
    # used as if it were a happy memory.
    "worried": (0.7, -0.5),
    "frustrated": (0.7, -0.5),
    "lonely": (0.8, -0.6),
    "sad": (0.8, -0.7),
    "scared": (0.9, -0.8),
    "afraid": (0.9, -0.8),
    "frightened": (0.9, -0.8),
}

#: An emotion we do not recognise. Some feeling, not much of it.
_UNKNOWN_EMOTION = (0.35, 0.1)

#: An `emotional_moment` is only worth a row when there was real feeling in it.
#: Below this the emotion is scenery, and the beat is already remembered (or not)
#: as an event.
EMOTION_FLOOR = 0.6

#: How many chosen labels must share a topic word before it reads as a
#: preference rather than a coincidence (§11.7's interest graph, in miniature).
PREFERENCE_REPEATS = 2

_WORD = re.compile(r"[a-z][a-z']+")

#: Words a choice label is full of and a preference is never about.
_LABEL_NOISE = frozenset(
    """the a an and or to of with for at on in go going lets let's shall we you your
    my me it its them that this here there way path one two three little big new old
    ask asking help helping look looking see seeing take taking try trying""".split()
)


def from_session(
    session: PlaySession, *, log: list[dict[str, Any]] | None = None
) -> list[Candidate]:
    """Every deterministic candidate this sitting produced.

    Ordered by nothing in particular — `salience.select` ranks them — and
    deliberately generous: proposing is cheap, and the threshold is where
    selectivity lives. What must *not* happen here is a moment that never gets
    proposed, because nothing downstream can recover it.

    `log` is the STM log, passed in by `consolidation` so that both halves of the
    pass see the same one; fetched here when a caller has not got it already.
    """
    child_name = session.child.first_name
    adventures = list(session.adventures.all())
    log = stm.events(session.pk) if log is None else log

    proposals: list[Candidate] = []
    for adventure in adventures:
        proposals.extend(_from_adventure(adventure, child_name, log))
    return proposals


def _from_adventure(
    adventure: Adventure, child_name: str, log: list[dict[str, Any]]
) -> list[Candidate]:
    beats = list(adventure.beats.order_by("seq").prefetch_related("choices"))
    entity_counts = _entity_recurrence(beats)

    proposals: list[Candidate] = []
    proposals.extend(_acts(adventure, beats, child_name, entity_counts))
    proposals.extend(_feelings(adventure, beats, child_name))
    proposals.extend(_cast(adventure, beats, child_name, entity_counts))
    proposals.extend(_preferences(adventure, beats, child_name))

    if log:
        # STM's only unique contribution to the rules half: the log knows the
        # sitting was abandoned rather than closed, which changes what the last
        # beat *meant*. Recorded as a signal on nothing in particular, so it is
        # logged rather than remembered — a child wandering off is a wellbeing
        # fact for §13.6 and not a memory about them.
        ended = next(
            (event for event in reversed(log) if event.get("event") == stm.SESSION_END), None
        )
        if ended and ended.get("reason") not in (None, "", "completed"):
            logger.info(
                "memory.consolidate_partial_sitting adventure=%s reason=%s",
                adventure.pk,
                ended.get("reason"),
            )
    return proposals


# ---------------------------------------------------------------------------
# What the child did
# ---------------------------------------------------------------------------
def _acts(
    adventure: Adventure,
    beats: list[Beat],
    child_name: str,
    entity_counts: Counter[str],
) -> list[Candidate]:
    """One candidate per thing the child actually did (§11.4's child-initiated).

    A `say` answer becomes an `event` in the child's own words; a branch pick
    becomes an `event` about the path they took; a counted or tapped answer
    becomes a `skill_moment`, because what is memorable about it is the *doing*
    rather than the branch. A tap that only carried the story on becomes a
    candidate too, scored at zero on the factor that matters — proposed so the
    threshold is what rejects it, rather than a rule here quietly deciding a whole
    class of moment is never worth remembering.
    """
    proposals: list[Candidate] = []

    for beat in beats:
        choice = next(iter(beat.choices.all()), None)
        if choice is None:
            continue

        interaction = beat.interaction or {}
        asks = str(interaction.get("type") or "none")
        option = beat.option(choice.chosen_key) or {}
        label = str(option.get("label") or "").strip()
        answer = "" if choice.value is None else str(choice.value).strip()
        intensity, valence = _emotion_of(beat.emotion)
        entities = _entities_for(beat, extra=_objective_ref(beat))
        repetition = _repetition(entities, entity_counts, len(beats))

        if asks == "say" and answer:
            # The child's own words. The highest-value thing in the session and
            # the reason §11.5 exists — "named the dragon Sparkle" is this branch.
            proposals.append(
                Candidate(
                    type=MemoryType.EVENT,
                    content=f'{child_name} said "{answer}" — {_anchor(beat)}',
                    entities=entities,
                    valence=valence,
                    signals=Signals(
                        child_initiated=SPOKE,
                        emotional_intensity=intensity,
                        skill_milestone=_milestone(beat, choice, asks),
                        repetition=repetition,
                    ),
                    source_adventure_id=adventure.pk,
                )
            )
            continue

        if asks in {"count", "tap"}:
            proposals.append(
                Candidate(
                    type=MemoryType.SKILL_MOMENT,
                    content=_skill_sentence(child_name, beat, choice, asks, answer),
                    entities=entities,
                    valence=valence,
                    signals=Signals(
                        child_initiated=ANSWERED,
                        emotional_intensity=intensity,
                        skill_milestone=_milestone(beat, choice, asks),
                        repetition=repetition,
                    ),
                    source_adventure_id=adventure.pk,
                )
            )
            continue

        offered = len(beat.options)
        proposals.append(
            Candidate(
                type=MemoryType.EVENT,
                content=(
                    f'{child_name} chose "{label}" — {_anchor(beat)}'
                    if label
                    else f"{child_name} carried on — {_anchor(beat)}"
                ),
                entities=entities,
                valence=valence,
                signals=Signals(
                    child_initiated=CHOSE_A_BRANCH if offered > 1 else CARRIED_ON,
                    emotional_intensity=intensity,
                    skill_milestone=_milestone(beat, choice, asks),
                    repetition=repetition,
                ),
                source_adventure_id=adventure.pk,
            )
        )

    return proposals


def _skill_sentence(child_name: str, beat: Beat, choice: Choice, asks: str, answer: str) -> str:
    """How a skill moment reads back. Warm, and never a grade (§8.5).

    There is no "got it wrong" phrasing available here on purpose. A child who
    answered four when there were three frogs *counted*, which is the thing worth
    remembering, and a memory that recorded it as a failure would come back to
    them months later as one.
    """
    helped = " with a little help" if choice.assisted or choice.retries else " all by themselves"
    if asks == "count" and answer:
        return f"{child_name} counted {answer}{helped} — {_anchor(beat)}"
    return f"{child_name} worked it out{helped} — {_anchor(beat)}"


def _milestone(beat: Beat, choice: Choice, asks: str) -> float:
    """How much of a skill milestone this was, 0-1 (§11.4).

    Highest when the child did it unaided and it matched what the beat expected;
    still substantial when it took them a couple of tries or a hand from the
    companion, because *that* is the growth moment and §11.8's trajectory is built
    out of them. Zero when nothing was being practised — a branch pick is not a
    skill, however deliberate.
    """
    if asks not in {"count", "say", "tap"}:
        return 0.0

    expected = (beat.interaction or {}).get("expected")
    unaided = not choice.assisted and not choice.retries
    matched = (
        expected is not None
        and choice.value is not None
        and str(expected).strip().lower() == str(choice.value).strip().lower()
    )

    if unaided and matched:
        return 1.0
    if unaided:
        return 0.7
    return 0.55


# ---------------------------------------------------------------------------
# How it felt
# ---------------------------------------------------------------------------
def _feelings(adventure: Adventure, beats: list[Beat], child_name: str) -> list[Candidate]:
    """The one beat of this adventure with real feeling in it, if there was one.

    *One*, not every beat above the floor. Every beat carries an emotion by
    contract, so proposing each of them would hand the salience pass a dozen
    near-identical "was delighted" candidates and rely on the diversity filter to
    undo it. Picking the peak here is cheaper and says the true thing: an
    adventure has an emotional high point, and that is what a friend remembers
    about it (§8.8's arc).
    """
    scored = [(beat, *_emotion_of(beat.emotion)) for beat in beats if beat.emotion]
    above = [entry for entry in scored if entry[1] >= EMOTION_FLOOR]
    if not above:
        return []

    beat, intensity, valence = max(above, key=lambda entry: (entry[1], abs(entry[2])))
    readable = beat.emotion.replace("_", " ")
    return [
        Candidate(
            type=MemoryType.EMOTIONAL_MOMENT,
            content=f"{child_name} was {readable} — {_anchor(beat)}",
            entities=_entities_for(beat),
            valence=valence,
            signals=Signals(
                # The feeling is the child's even though the beat is ours, but it
                # is not an *act* — so this scores between carrying on and
                # choosing, and lives or dies on its emotional intensity.
                child_initiated=ANSWERED if beat.choices.all() else CARRIED_ON,
                emotional_intensity=intensity,
            ),
            source_adventure_id=adventure.pk,
        )
    ]


# ---------------------------------------------------------------------------
# Who they met (§11.5's relationships)
# ---------------------------------------------------------------------------
def _cast(
    adventure: Adventure,
    beats: list[Beat],
    child_name: str,
    entity_counts: Counter[str],
) -> list[Candidate]:
    """Characters this adventure introduced, as `entity` memories (§11.5).

    Read from `companion.Character` rather than from the beats' `new_characters`
    blobs, because a `Character` row is the stronger claim: it has a canonical
    visual descriptor, which means the illustration pipeline can draw this
    creature the same way again. A memory about someone we can redraw is a
    relationship; a memory about a name we cannot picture is trivia.

    A character the child *named* — their spoken answer contains the name — is
    marked as theirs, which is the single most reciprocity-rich memory the product
    can hold (§9.3).
    """
    introduced = list(
        Character.objects.filter(child=adventure.child, first_seen_adventure=adventure)
        .exclude(visual_descriptor="")
        .order_by("created_at")
    )
    if not introduced:
        return []

    spoken = " ".join(
        str(choice.value).lower()
        for beat in beats
        for choice in beat.choices.all()
        if choice.value is not None
    )

    proposals: list[Candidate] = []
    for character in introduced:
        named_by_child = bool(character.name) and character.name.lower() in spoken
        # `role` is blank on every row today — `ai.schemas.CastMember` has no such
        # field, so nothing fills it. Read anyway rather than hard-coding the bare
        # name, because "Bramble the lantern-keeper" is what this memory should say
        # the day the generated cast member carries a role.
        role = f" the {character.role}" if character.role else ""
        proposals.append(
            Candidate(
                type=MemoryType.ENTITY,
                content=(
                    f"{child_name} named {character.name}{role}."
                    if named_by_child
                    else f"{child_name} met {character.name}{role}."
                ),
                entities=[f"character:{character.key}"],
                # Meeting someone is a warm thing by default in this product —
                # there are no hostile characters (§13.2's absolute rules).
                valence=0.5,
                signals=Signals(
                    child_initiated=SPOKE if named_by_child else MET_SOMEONE,
                    emotional_intensity=0.5,
                    repetition=_repetition(
                        [f"character:{character.key}"], entity_counts, len(beats)
                    ),
                ),
                source_adventure_id=adventure.pk,
            )
        )
    return proposals


# ---------------------------------------------------------------------------
# What they keep coming back to (§11.7)
# ---------------------------------------------------------------------------
def _preferences(adventure: Adventure, beats: list[Beat], child_name: str) -> list[Candidate]:
    """A `preference` when the same topic was chosen more than once.

    The seed of the interest graph, and deliberately the weakest inference in this
    module: it fires only on repetition *within* one sitting, from words the child
    picked rather than words we narrated. Guessing at a preference from a single
    tap is how a companion ends up confidently telling a child they love something
    they touched once, which is worse than not knowing.
    """
    chosen = [
        str((beat.option(choice.chosen_key) or {}).get("label") or "")
        for beat in beats
        for choice in beat.choices.all()
        if choice.chosen_key
    ]
    topics = Counter(
        word
        for label in chosen
        for word in _WORD.findall(label.lower())
        if word not in _LABEL_NOISE and len(word) > 3
    )

    proposals: list[Candidate] = []
    for topic, count in topics.most_common():
        if count < PREFERENCE_REPEATS:
            break
        proposals.append(
            Candidate(
                type=MemoryType.PREFERENCE,
                content=f"{child_name} keeps choosing anything to do with {topic}.",
                entities=[f"topic:{topic}"],
                valence=0.6,
                signals=Signals(
                    child_initiated=CHOSE_A_BRANCH,
                    emotional_intensity=0.4,
                    repetition=min(1.0, count / max(1, len(chosen))),
                ),
                source_adventure_id=adventure.pk,
            )
        )
    return proposals


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _anchor(beat: Beat) -> str:
    """The beat's first clause — what happened, without how it was told."""
    from apps.memory.capture import anchor

    return anchor(beat.narration)


def _entities_for(beat: Beat, *, extra: list[str] | None = None) -> list[str]:
    """What a beat was about, as namespaced refs, deduplicated in order.

    `continuity_refs` is already in this shape (`companion:pip`, `world:river`) and
    has been written on every beat since Day 5 for exactly this moment — the
    column's docstring says so. Reusing it rather than re-deriving entities from
    the narration means the memory graph and the continuity the beats were
    generated against agree with each other.
    """
    refs = [str(ref) for ref in (beat.continuity_refs or []) if str(ref).strip()]
    refs += extra or []
    seen: dict[str, None] = {}
    for ref in refs:
        seen.setdefault(ref[:120], None)
    return list(seen)


def _objective_ref(beat: Beat) -> list[str]:
    """The objective this beat practised, so Day 10 can find its evidence."""
    objective = str((beat.learning or {}).get("objective_id") or "").strip()
    return [f"objective:{objective}"] if objective else []


def _entity_recurrence(beats: list[Beat]) -> Counter[str]:
    """How many beats each entity appeared in — the repetition factor's input."""
    counts: Counter[str] = Counter()
    for beat in beats:
        for ref in set(_entities_for(beat)):
            counts[ref] += 1
    return counts


def recurrence(session: PlaySession) -> tuple[Counter[str], int]:
    """`(entity counts, beats)` across the whole sitting.

    Public because the model half needs the same denominator the rules half uses:
    a proposal about the character who was in every beat should score its
    repetition the same way whichever source proposed it. Returned as a pair
    rather than a ratio because `_repetition` needs the beat count to normalise
    against, and a sitting with no beats must not divide by zero.
    """
    counts: Counter[str] = Counter()
    beats = 0
    for adventure in session.adventures.all():
        rows = list(adventure.beats.all())
        beats += len(rows)
        counts.update(_entity_recurrence(rows))
    return counts, beats


def repetition_of(entities: list[str], counts: Counter[str], beats: int) -> float:
    """`_repetition`, for the model half. Same denominator, same answer."""
    return _repetition(entities, counts, beats)


def _repetition(entities: list[str], counts: Counter[str], beats: int) -> float:
    """How much of the sitting this candidate's subject ran through, 0-1.

    The companion is deliberately *not* excluded even though it appears in every
    beat. It genuinely is the most repeated entity in every session, and the
    factor is only a tenth of the score — down-weighting the one character who is
    always there would be tuning the input to get an output we liked.
    """
    if not entities or beats <= 0:
        return 0.0
    best = max((counts.get(ref, 0) for ref in entities), default=0)
    return min(1.0, best / beats)


def _emotion_of(emotion: str) -> tuple[float, float]:
    """`(intensity, valence)` for a free-text emotion label.

    Compound labels ("playful_curious") take the strongest intensity among their
    parts and the valence of whichever part felt most strongly — a beat that is
    both playful and frightened is a frightened beat, and averaging would report
    it as neutral.
    """
    parts = [part for part in re.split(r"[^a-z]+", (emotion or "").lower()) if part]
    known = [_EMOTION_WORDS[part] for part in parts if part in _EMOTION_WORDS]
    if not known:
        return _UNKNOWN_EMOTION
    intensity = max(entry[0] for entry in known)
    valence = max(known, key=lambda entry: abs(entry[1]))[1]
    return intensity, valence
