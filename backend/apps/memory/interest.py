"""The interest graph (PRODUCT.md §11.7, §2.4).

    a finished sitting  ->  topics the child reached for  ->  weights that fade

§11.7 asks for "a weighted, time-decaying graph of what the child loves, updated
from choices and reactions", and §2.4 says why it is worth a table of its own:
generic content produces generic attention, and the difference between a story a
four-year-old attends to completely and one they drift out of is usually a single
noun. This module is what turns a week of taps into that noun.

**What counts as loving something, and what merely counts as being there.** The
weights below are graded for the same reason `candidates.SPOKE` is: a child who
*named* a creature has told us more than one who *chose* the card it was on, and
both have told us more than a child who was simply in a beat where it appeared.
An ungraded graph would be dominated by whatever our own narration put on screen
most often, which is a graph of what we like.

**Decay is a function, not a job.** A weight is only ever meaningful as of
`last_reinforced_at`, and every read ages it to now (`current_weight`). The
alternative — a pass that rewrites every edge with its decayed value — was
rejected twice over: it would make `last_reinforced_at` say "when we last ran the
job" instead of "when this child last cared", which is the one thing the column is
for and the thing a parent's "this month: space, then dinosaurs, now ocean" view
(§11.7) is read off; and it would write the child's whole graph on every sitting
to record that nothing happened. So the task writes only what was reinforced, and
deletes only what has faded past the point of meaning anything.

**Nothing here is child-facing and nothing here is a verdict.** A topic that fades
is not a topic the child was wrong to like. The graph steers which world a story
is set in and which characters come back (`apps.story.worlds`); it never decides
whether a beat is allowed to happen, and it can never widen what is safe — the
fear ceiling and the avoid list are read by the safety layer on every generation
regardless of what this table says a child would enjoy (§11.6, §13).
"""

from __future__ import annotations

import datetime
import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.companion.models import Character
from apps.memory.models import InterestEdge, WorldPlace
from apps.profiles.models import Child
from apps.story.models import Beat, Choice, PlaySession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# What reinforcement is worth (§11.7's "choices and reactions")
# ---------------------------------------------------------------------------
#: The child said it — a `say` answer, the name they gave something. The strongest
#: signal there is and the same one `candidates.SPOKE` scores highest: a word a
#: four-year-old volunteers is theirs in a way nothing we put on a card is.
SPOKE = 0.5

#: The child picked the card this was on. §11.7's "choices" in one number, and the
#: workhorse of the graph — most sittings produce two or three of these and nothing
#: else.
CHOSE = 0.35

#: It was in a beat the child played through. Not nothing (they stayed, they looked
#: at it) and deliberately small: most of what is in a beat is there because *we*
#: put it there, and a graph built mostly of this would be a graph of our own
#: scenery.
ENCOUNTERED = 0.08

#: What a topic can reach. Weights saturate rather than growing without bound, so a
#: child who has chosen frogs forty times and dinosaurs twice still has both in the
#: graph — the ranking is "what do they love", not "what have we shown them most".
MAX_WEIGHT = 1.0

#: How long it takes an untouched interest to lose half its weight. A month, which
#: is the timescale §11.7's own example moves on ("this month: space, then
#: dinosaurs, now ocean animals") — long enough that a fortnight's holiday does not
#: erase a favourite, short enough that a phase which has genuinely passed stops
#: steering the stories.
HALF_LIFE_DAYS = 30.0

#: Below this an edge is not an interest any more, it is a row. Deleted at the next
#: update rather than kept forever: the graph is read on the story-start path, and
#: a table that only ever grows is a table that eventually costs a child latency.
FORGET_BELOW = 0.05

#: What a parent's onboarding answer is worth next to play (§17.2's "these are just
#: the cold-start"). High enough that a brand-new child's very first adventure is
#: already about dinosaurs, low enough that two sittings of choosing the sea
#: outrank it. Seeds do not decay — a parent's statement stands until the child's
#: own behaviour says otherwise.
SEED_WEIGHT = 0.4

#: How many topics a caller gets by default. The planner is the only consumer that
#: matters and it is building a prompt: past the top handful, every extra topic
#: dilutes the one the story should actually be about.
TOP_INTERESTS = 6

#: How much of a child's world one plan may be handed. Both are bounds on a
#: *prompt*, not on the tables: a family three years in has a large map and a large
#: cast, and §8.6's flat prompt size is not something they get to grow out of.
#: Newest/most-recent first, so what is offered is what is live.
KNOWN_PLACE_LIMIT = 6
RECURRING_CAST_LIMIT = 6

#: Entity namespaces (`apps.memory.entities`) that name a *subject* a child could
#: love. `companion` and `child` are excluded because they are in every sitting and
#: would win every ranking while saying nothing; `objective` is excluded because it
#: is our machinery and a child has no opinion about `count_to_5`.
TOPIC_NAMESPACES = frozenset({"character", "world", "place", "object", "topic"})

#: How long a topic may be. These are index terms, not sentences, and the column is
#: 64 characters wide.
MAX_TOPIC_LENGTH = 64

#: Words that name nothing on their own. Same job as `learning.engine._STOPWORDS`
#: and deliberately a separate list: that one filters a one-sentence objective
#: description, this one filters a three-word choice label ("Follow the sparkling
#: river"), and a shared list would end up being wrong for both.
#:
#: The second block is the vocabulary a *choice card* is written in, and it is here
#: because of what the graph looked like without it. One real sitting — the child
#: tapping "Follow the sparkling river" and "Ask the dragonflies" — produced a top
#: six of `river, frog, sparkling, follow, dragonflie, ask`: half of it verbs and
#: adjectives from our own card copy, sitting at the same weight as the nouns the
#: child actually reached for. It goes straight into the plan prompt ("This child
#: is currently in love with: … sparkling, follow, … ask") and it is what §11.7
#: promises a parent as "a lovely, true window". A topic has to be a *subject*.
#:
#: Kept deliberately short of the obvious next words. `sing`, `dance`, `play`,
#: `magic` and `secret` are card copy nine times out of ten and a child's genuine
#: love the tenth, and this list is the one place in the module where a mistake is
#: silent: a topic filtered here can never be reinforced, however often the child
#: reaches for it. When a word could be either, it stays in.
_STOPWORDS = frozenset(
    """and are but can for from her his into its let lets not one our out over say
    see the their them then there they this that with what when who why you your
    all any big has have how its more new now off old put too very want went
    where will yes back down here just like look make take come well along onto

    follow ask tell show help find give open close climb jump walk run wait stay
    hide peek listen touch call carry hold pull push reach follows
    sparkling shining glowing quiet loud warm cold soft slow fast little tiny huge
    lovely pretty bright dark tall wide round
    """.split()
)


@dataclass(frozen=True, slots=True)
class Topic:
    """One weighted interest, as the planner reads it."""

    name: str
    weight: float


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one update did. Returned for the Celery result row and for tests."""

    status: str
    reinforced: int = 0
    forgotten: int = 0

    def __str__(self) -> str:
        return f"{self.status} reinforced={self.reinforced} forgotten={self.forgotten}"


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------
#: Plural endings where dropping the trailing "s" alone leaves a non-word:
#: "foxes" -> "foxe", "beaches" -> "beache". Two letters come off instead.
#: Deliberately not `-ses`, which would take "horses" to "hors".
_PLURAL_ES = ("ches", "shes", "sses", "xes", "zes")


def _singular(word: str) -> str:
    """A five-year-old's plural, crudely undone.

    Three rules, and each one exists because dropping the bare "s" produces a
    string no person would recognise:

    * **"ss" is kept** — "moss" must not become "mos", "grass" not "gras".
    * **"-ies" becomes "-y"** — this is the one that mattered most in practice.
      Without it a child who loves *dragonflies, butterflies, bunnies, puppies,
      ponies* has topics called `dragonflie`, `butterflie`, `bunnie`, `puppie`,
      `ponie`, none of which matches a world's vocabulary, a parent's seed word, or
      anything worth showing a parent as what their child loves (§11.7).
    * **"-ches/-shes/-sses/-xes/-zes" lose both letters** — "foxes" is a fox.

    Still crude on purpose: a stemming dependency for a table whose keys are words
    a five-year-old would use is a dependency for nothing, and the cost of a miss is
    one interest counted twice under two spellings, which fades on its own.
    """
    if len(word) <= 3 or not word.endswith("s") or word.endswith("ss"):
        return word
    if len(word) > 4 and word.endswith("ies"):
        return f"{word[:-3]}y"
    if len(word) > 4 and word.endswith(_PLURAL_ES):
        return word[:-2]
    return word[:-1]


def topic_tokens(text: str) -> frozenset[str]:
    """The subjects named in a phrase, as graph topics.

    Lowercase, letters only, crudely singularised (`_singular`), three letters or
    more, and never one of `_STOPWORDS` — which filters function words *and* the
    verbs and adjectives a choice card is written in, because a topic has to name a
    subject rather than describe one.
    """
    letters = "".join(char if char.isalpha() else " " for char in text.casefold())
    words = (_singular(word) for word in letters.split())
    return frozenset(
        word[:MAX_TOPIC_LENGTH] for word in words if len(word) >= 3 and word not in _STOPWORDS
    )


def topics_from_entities(refs: Iterable[str]) -> frozenset[str]:
    """Graph topics from `Memory.entities` / `Beat.continuity_refs` keys.

    `character:moss_frog` becomes `{"moss", "frog"}`: the namespace decides whether
    the ref is a subject at all (`TOPIC_NAMESPACES`) and the value supplies the
    words. Split rather than kept whole because the planner matches topics against a
    world's vocabulary and against a parent's seed word, and "moss_frog" would match
    neither while plainly being about frogs.
    """
    found: set[str] = set()
    for raw in refs or []:
        namespace, separator, value = str(raw).strip().partition(":")
        if not separator or namespace.strip().casefold() not in TOPIC_NAMESPACES:
            continue
        found |= topic_tokens(value.replace("_", " "))
    return frozenset(found)


# ---------------------------------------------------------------------------
# Reading the graph
# ---------------------------------------------------------------------------
def current_weight(edge: InterestEdge, *, now: datetime.datetime | None = None) -> float:
    """`edge.weight` aged to now (§11.7's "time-decaying").

    Exponential with a half-life rather than a linear slide, because forgetting is
    not a countdown: an interest three months untouched should be faint, not
    negative, and one touched yesterday should be barely diminished.
    """
    now = now or timezone.now()
    days = max(0.0, (now - edge.last_reinforced_at).total_seconds() / 86_400.0)
    return edge.weight * math.pow(0.5, days / HALF_LIFE_DAYS)


def graph(child: Child, *, now: datetime.datetime | None = None) -> dict[str, float]:
    """Every live topic for this child, aged to now. Play only — no seeds."""
    now = now or timezone.now()
    return {
        edge.topic: weight
        for edge in InterestEdge.objects.filter(child=child)
        if (weight := current_weight(edge, now=now)) >= FORGET_BELOW
    }


def top_interests(
    child: Child,
    *,
    limit: int = TOP_INTERESTS,
    now: datetime.datetime | None = None,
) -> tuple[Topic, ...]:
    """What this child loves right now, strongest first (§11.7).

    **The seeds and the graph are one ranking, not two.** A child with no history
    still gets a personalised world on their first adventure, because
    `Child.interests` enters at `SEED_WEIGHT`; a child with a history gets a
    ranking their own play has reordered, because a topic they keep choosing
    saturates above anything a parent typed. Summing rather than taking the larger
    of the two is what makes the *confirmed* seed — the parent said dinosaurs and
    the child keeps choosing them — outrank the seed nobody has acted on, which is
    the ordering a parent would expect and the one §2.4 is about.

    Ties break on the topic, so two children with identical evidence get identical
    stories and the whole selection stays testable.
    """
    weights: dict[str, float] = dict(graph(child, now=now))
    for seed in child.interests or []:
        for token in topic_tokens(str(seed)):
            weights[token] = min(MAX_WEIGHT, weights.get(token, 0.0) + SEED_WEIGHT)

    ranked = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    return tuple(Topic(name=name, weight=weight) for name, weight in ranked[:limit])


def selector_tokens(child: Child, *, limit: int = TOP_INTERESTS) -> frozenset[str]:
    """The interest vocabulary the Learning Engine's tie-break reads (§10.3).

    This is the seam Day 10 left named and inert. `engine.interest_fit` used to
    tokenise `Child.interests` — a text box a parent filled in once — so the term
    could only ever say what was true at onboarding. It now reads the live ranking,
    which means the same function answers differently for the same child a month
    later, which is the whole point of there being a graph.

    Kept as a plain set because the term it feeds is a *tie-break* and is documented
    as one (`INTEREST_WEIGHT`): what the graph changes is which words are in the
    set, not how much the term is allowed to swing a selection. Curriculum belongs
    at the child's edge (§10.3); the interest graph's load-bearing job is which
    world the edge is dressed in (§8.3).
    """
    return frozenset(topic.name for topic in top_interests(child, limit=limit))


# ---------------------------------------------------------------------------
# Writing the graph
# ---------------------------------------------------------------------------
def reinforcement_from(session: PlaySession) -> dict[str, float]:
    """What one sitting says the child loves, as `{topic: strength}`.

    Read off **persisted rows** rather than the Redis log, deliberately. The log is
    the right source for the *shape* of a sitting (`candidates`), but this needs
    exactly three things — what was chosen, what was said, and what was in the beats
    — and all three survive a Redis outage: the first and third are columns on
    `Choice` and `Beat`, and the second is on `PlaySession.stm_digest`, which is the
    mirror §11.2 keeps precisely so the child-initiated moments cannot be lost. An
    interest graph that quietly stopped updating whenever Redis was down would be
    invisible for weeks and unrecoverable afterwards.

    A topic reinforced twice in one sitting counts once, at its strongest reading:
    the child choosing the frog card twice is one afternoon about frogs, and letting
    a sitting stack would let one long session outweigh a month of short ones.
    """
    strengths: dict[str, float] = {}

    def note(topics: Iterable[str], strength: float) -> None:
        for topic in topics:
            if strength > strengths.get(topic, 0.0):
                strengths[topic] = strength

    adventures = list(session.adventures.all())
    if not adventures:
        return {}

    beats = {
        beat.pk: beat
        for beat in Beat.objects.filter(adventure__in=adventures).order_by("adventure", "seq")
    }
    for beat in beats.values():
        # Everything the child sat through. The weakest signal, applied first so a
        # stronger reading of the same topic below simply replaces it.
        note(topics_from_entities(beat.continuity_refs), ENCOUNTERED)
        note(topic_tokens(str((beat.scene or {}).get("setting") or "")), ENCOUNTERED)

    for choice in Choice.objects.filter(adventure__in=adventures).select_related("beat"):
        beat = beats.get(choice.beat_id) or choice.beat
        option = beat.option(choice.chosen_key) or {}
        # The **label** of the card they took — the words the companion said out
        # loud, which is the whole of what a pre-reader chose between. Not the cards
        # they left (those are ours, not theirs), and deliberately not the card's
        # `icon_prompt`: that is a sentence written for an image model, and its
        # scenery ("between mossy stones", "under warm light") would arrive at the
        # same strength as the noun the child actually reached for.
        note(topic_tokens(str(option.get("label") or "")), CHOSE)
        # What they answered in words. A number says nothing about what they love.
        if isinstance(choice.value, str):
            note(topic_tokens(choice.value), SPOKE)

    # What the child said or named, mirrored onto the session row by `stm.mirror`.
    # The highest-value strings a sitting produces (§11.4) and the reason this is
    # not simply a scan of the beats.
    for highlight in _spoken(session.stm_digest):
        note(topic_tokens(highlight), SPOKE)

    return strengths


def _spoken(digest: Mapping[str, Any] | None) -> list[str]:
    """The child's own words out of the STM mirror, as plain phrases.

    `memory.capture.choice_recorded` writes highlights as `"said: sparkle"` and
    `"chose: Follow the river"`; only the first kind is read here, because the
    chosen labels are already covered — precisely — by the `Choice` scan above, and
    reading them twice would let the mirror's bounded tail decide which of a
    sitting's choices counted.

    Tolerant of a shape that is not the current one. This is a JSON column written
    by another module, and an interest update that raised on an unfamiliar digest
    would cost the whole graph for that sitting to gain nothing.
    """
    said: list[str] = []
    for item in (digest or {}).get("highlights") or []:
        if not isinstance(item, str):
            continue
        label, _, words = item.partition(": ")
        if label.strip().casefold() in {"said", "named"} and words.strip():
            said.append(words)
    return said


@transaction.atomic
def apply_reinforcement(
    child: Child,
    strengths: Mapping[str, float],
    *,
    now: datetime.datetime | None = None,
) -> tuple[int, int]:
    """Raise the topics a sitting reinforced, forget the ones that have faded.

    Returns `(reinforced, forgotten)`.

    Each reinforced edge is **aged first, then raised**: the stored number means
    "this much, as of that timestamp", so adding to it without ageing it would let
    a topic touched once a year climb forever. Saturating rather than clamping at
    the end (`+ boost * (1 - weight)`) is what keeps a lifelong favourite and a new
    obsession distinguishable — the tenth frog choice moves the number a little, the
    first dinosaur choice moves it a lot.

    Rows are locked for the read-modify-write, because two sittings finishing at
    once is a tablet and a phone with one child on them, and the second would
    otherwise compute from a weight the first had already replaced.
    """
    now = now or timezone.now()
    reinforced = 0

    for topic in sorted(strengths):
        boost = strengths[topic]
        edge, created = InterestEdge.objects.select_for_update().get_or_create(
            child=child, topic=topic[:MAX_TOPIC_LENGTH], defaults={"weight": 0.0}
        )
        aged = 0.0 if created else current_weight(edge, now=now)
        edge.weight = min(MAX_WEIGHT, aged + boost * (MAX_WEIGHT - aged))
        # `last_reinforced_at` is `auto_now`, so saving *is* the timestamp.
        edge.save(update_fields=["weight", "last_reinforced_at", "updated_at"])
        reinforced += 1

    forgotten = 0
    for edge in InterestEdge.objects.filter(child=child).exclude(topic__in=set(strengths)):
        if current_weight(edge, now=now) < FORGET_BELOW:
            edge.delete()
            forgotten += 1

    return reinforced, forgotten


def update_from_session(session: PlaySession, *, now: datetime.datetime | None = None) -> Outcome:
    """Fold one sitting into the child's interest graph (§11.7).

    The Day-11 sibling of `consolidation.consolidate_session` and
    `learning.update_from_adventures`: same input (a finished sitting), same
    posture (off the child's lifecycle, nobody waiting), same idempotency gate on
    the session row. What it adds is the third thing a sitting is evidence about —
    not what happened and not what the child can do, but what they want more of.

    Returns an `Outcome` rather than raising: a sitting whose interests could not be
    read is a slightly less personal story tomorrow, and there is nothing here worth
    failing a task over.
    """
    if session.interests_updated_at is not None:
        logger.info("memory.interests_noop session=%s reason=already", session.pk)
        return Outcome("already")

    now = now or timezone.now()
    strengths = reinforcement_from(session)
    reinforced, forgotten = apply_reinforcement(session.child, strengths, now=now)

    session.interests_updated_at = now
    session.save(update_fields=["interests_updated_at", "updated_at"])

    if not strengths:
        # Stamped anyway, for the same reason `consolidated_at` is: "we looked and
        # there was nothing" is a finished update, and leaving it null would make
        # this a permanent retry on a sitting with nothing in it.
        logger.info("memory.interests_empty session=%s child=%s", session.pk, session.child_id)
        return Outcome("empty", 0, forgotten)

    logger.info(
        "memory.interests session=%s child=%s reinforced=%d forgotten=%d top=%s",
        session.pk,
        session.child_id,
        reinforced,
        forgotten,
        ",".join(topic.name for topic in top_interests(session.child, limit=3, now=now)) or "-",
    )
    return Outcome("updated", reinforced, forgotten)


def places_for(child: Child, *, limit: int = KNOWN_PLACE_LIMIT) -> list[WorldPlace]:
    """This child's known places, most recently visited first (§8.3).

    Here rather than on the model so the planner has one import for "everything
    this child's world already contains", and so the ordering — comfort first — is
    stated once.
    """
    return list(WorldPlace.objects.filter(child=child).order_by("-last_visited_at", "name")[:limit])


def characters_for(child: Child, *, limit: int = RECURRING_CAST_LIMIT) -> list[Character]:
    """The recurring cast the planner may bring back (§8.2).

    Only characters we hold a canonical appearance for, because a name the planner
    uses is a promise the illustration pipeline can draw the same creature it drew
    last time (`companion.cast.refs_for`). Newest first, which is the same ordering
    `orchestrator._known_characters` uses for the beat prompt — the plan and the
    beats must not disagree about who this child knows.
    """
    return list(
        Character.objects.filter(child=child)
        .exclude(visual_descriptor="")
        .order_by("-created_at")[:limit]
    )
