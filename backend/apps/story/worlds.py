"""The child's world: which one, and where in it (PRODUCT.md §8.3, §15.1, §11.6).

§8.3 asks for "a persistent, growing map of places, each with mood, inhabitants
and history", entered by either revisiting somewhere known — continuity, comfort,
repetition-with-variation — or discovering somewhere new. This module is the
half of that which decides; `memory.WorldPlace` is the half that remembers.

**Worlds are ours, places are the child's.** The four worlds below are a shipped
registry: named, moodled, written once, reviewed for safety, and each paired with
a palette the child's screen actually wears (§15.1, `frontend/src/design/worlds`).
The *places* inside them become rows the moment a child goes there, and from then
on they belong to that child — visited, revisited, populated by whoever they met.
That split is deliberate and it is what makes any of this testable: which world a
child gets is a pure function of what they love, and a reviewer can read it.

**Why a registry rather than asking the model for a world.** Three reasons, and
the first is the one that would have bitten us. A palette has to be a key the CSS
knows (`data-world="coral_deep"`); a model asked to name a world answers "The
Glimmering Tidepools", which is lovely and matches no stylesheet, so the theme
layer would silently fall back to default on most stories. The second is §11.6:
a place's intensity has to be *known* before the story is planned, not judged
afterwards — a fear ceiling that is enforced by reading back what a model invented
is a ceiling enforced one beat too late. The third is that a generated world
cannot be *returned to*, because nothing generated the same way twice is the same
place, which is precisely the thing §8.3 exists to guarantee.

**What is personalised, then, if the worlds are fixed?** The selection, which is
where §2.4's bet actually lives: a child who loves the sea gets the Coral Deep and
a child who loves dinosaurs gets the Sunlit Canyon, on the same objective, with
different palettes, different places, different inhabitants and a different plan
generated inside each. Two profiles produce two structurally different adventures
without a model being asked to be personal — and everything the model *does*
invent inside that frame is personal on top of it.

**The fear ceiling is a filter here, not a note in the prompt.** Every place
carries the gentlest ceiling it is suitable at, and `select_place` never offers one
above the child's (§11.6). The safety layer still states the ceiling on every
generation, as it always has; this makes it structural as well — the too-much place
is not somewhere the model was asked not to go, it is somewhere it was never given.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.memory.interest import Topic
from apps.memory.models import WorldPlace
from apps.profiles.models import Child
from apps.profiles.settings import settings_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Place:
    """One place a child can arrive at, before it is anybody's.

    `intensity` is the gentlest `Child.intensity_ceiling` this place is suitable
    at, and it is the whole of §11.6 in one integer: a dusk-lit hollow with
    fireflies is a lovely place for a five-year-old on ceiling 2 and too much for a
    three-year-old on 0, and the difference has to be decided here rather than
    hoped for in a prompt.
    """

    name: str
    mood: str
    description: str
    inhabitants: tuple[str, ...]
    intensity: int = 0


@dataclass(frozen=True, slots=True)
class World:
    """One of the shipped worlds (§8.3), and the palette it wears (§15.1)."""

    key: str
    name: str
    mood: str
    #: The vocabulary this world answers to, in `interest.topic_tokens`' shape —
    #: lowercase, singular. Matched against the child's ranked interests, so every
    #: word in here is a claim that a child who loves this word belongs in this
    #: world.
    topics: frozenset[str]
    places: tuple[Place, ...]

    def place_named(self, name: str) -> Place | None:
        return next((place for place in self.places if place.name == name), None)


#: The shipped worlds. Four rather than §15.1's three, because the founder test is
#: "dinosaurs vs. ocean" and a dinosaur-loving child landing in a forest because
#: the forest is the nearest thing we built is exactly the generic-content failure
#: §2.4 describes. Each is warm, none is harsh or overstimulating (§15.1), and each
#: has at least one place a child on the gentlest ceiling can be taken to — an
#: invariant `select_place` depends on and `test_worlds` asserts.
WORLDS: tuple[World, ...] = (
    World(
        key="whispering_woods",
        name="the Whispering Woods",
        mood="dappled_and_green",
        topics=frozenset(
            """forest wood tree frog fox rabbit badger hedgehog deer squirrel owl bird
            mushroom moss leaf fern river stream pond bug beetle bee flower garden
            honey nest burrow animal""".split()
        ),
        places=(
            Place(
                name="the Hollow Oak",
                mood="dappled_and_green",
                description=(
                    "an enormous old oak with a round doorway in its trunk, a rope ladder "
                    "to a platform in the branches, and afternoon light coming through the "
                    "leaves in coins"
                ),
                inhabitants=("the moss frogs", "a very slow snail called Pebble"),
            ),
            Place(
                name="the Ribbon River",
                mood="bright_and_babbling",
                description=(
                    "a shallow, chattering river with flat stepping stones across it, "
                    "dragonflies over the reeds and a wooden jetty someone built long ago"
                ),
                inhabitants=("the blue dragonflies", "a family of otters"),
            ),
            Place(
                name="the Lantern Glade",
                mood="hushed_and_glowing",
                description=(
                    "a round clearing at dusk where hundreds of fireflies hang in the air "
                    "like slow lamps, and the grass is warm from the day"
                ),
                inhabitants=("the fireflies", "a sleepy owl called Tuft"),
                # Dusk, and a place where the light has gone soft. Delightful at 1,
                # and not where a three-year-old on the gentlest ceiling is taken.
                intensity=1,
            ),
        ),
    ),
    World(
        key="star_harbor",
        name="Star Harbor",
        mood="hushed_and_starlit",
        topics=frozenset(
            """star space rocket moon planet sky night comet astronaut spaceship ship
            sail boat harbour harbor telescope cloud kite balloon light lantern
            dream""".split()
        ),
        places=(
            Place(
                name="the Moonlit Jetty",
                mood="hushed_and_starlit",
                description=(
                    "a wooden jetty that floats on a harbour of quiet water, with little "
                    "boats bobbing at their ropes and the whole sky full of stars close "
                    "enough to point at"
                ),
                inhabitants=("the harbour lamps", "a ferryman gull called Bo"),
            ),
            Place(
                name="the Kite Deck",
                mood="bright_and_breezy",
                description=(
                    "a wide sunlit deck above the harbour where the wind is always steady "
                    "and dozens of kites are tied along the railing, waiting"
                ),
                inhabitants=("the kite-keepers", "a small brass telescope on a stand"),
            ),
            Place(
                name="the Comet Steps",
                mood="deep_and_shining",
                description=(
                    "a staircase of glowing stones that climbs out over the dark water "
                    "toward the sky, one step lighting as you reach it"
                ),
                inhabitants=("the step-lights", "a comet that comes back every evening"),
                intensity=1,
            ),
        ),
    ),
    World(
        key="coral_deep",
        name="the Coral Deep",
        mood="cool_and_shimmering",
        topics=frozenset(
            """sea ocean water fish whale dolphin shell turtle crab wave swim mermaid
            coral reef kelp seaweed octopus pearl sand beach tide starfish
            seahorse""".split()
        ),
        places=(
            Place(
                name="the Shell Garden",
                mood="cool_and_shimmering",
                description=(
                    "a warm shallow lagoon where the sand is white and every shell is a "
                    "different colour, laid out in rows by somebody very tidy"
                ),
                inhabitants=("the hermit crabs", "a tidy old turtle called Marigold"),
            ),
            Place(
                name="the Singing Kelp",
                mood="green_and_swaying",
                description=(
                    "a forest of tall kelp that leans with the current and hums very "
                    "quietly, with sunlight coming down through it in long stripes"
                ),
                inhabitants=("the silver fish", "a shy octopus called Mo"),
            ),
            Place(
                name="the Lantern Reef",
                mood="deep_and_glowing",
                description=(
                    "a reef further down where the water turns blue-dark and the coral "
                    "itself glows softly, so nothing is ever properly dark"
                ),
                inhabitants=("the glowing coral", "a whale who sings the way back up"),
                intensity=1,
            ),
        ),
    ),
    World(
        key="sunlit_canyon",
        name="the Sunlit Canyon",
        mood="warm_and_wide",
        topics=frozenset(
            """dinosaur dino fossil bone rock stone canyon cliff desert sand cactus
            lizard dig digger egg footprint mountain train truck wheel dust
            explorer""".split()
        ),
        places=(
            Place(
                name="the Big Bone Steps",
                mood="warm_and_wide",
                description=(
                    "a wide sandstone shelf where enormous old bones lie in the rock like "
                    "steps, warm to sit on, with the whole canyon golden below"
                ),
                inhabitants=("the little striped lizards", "a long-necked dinosaur called Juniper"),
            ),
            Place(
                name="the Echo Rocks",
                mood="bright_and_ringing",
                description=(
                    "a bend in the canyon where every sound comes back to you twice, and "
                    "somebody has painted small handprints all along the low wall"
                ),
                inhabitants=("the echo", "a family of round grey dinosaurs who hum"),
            ),
            Place(
                name="the Rumble Ridge",
                mood="tall_and_humming",
                description=(
                    "a high ridge where the ground buzzes very gently under your feet and "
                    "you can see the herd moving far below, like slow hills"
                ),
                inhabitants=("the herd", "a watchful crested dinosaur called Ash"),
                intensity=1,
            ),
        ),
    ),
)

#: Where a child with nothing in their graph and nothing in their seeds goes.
#: The gentlest and greenest of the four, and the one closest to the base tokens,
#: so an unpersonalised story still looks like The Primer rather than like a
#: fallback (§15.1).
DEFAULT_WORLD = WORLDS[0]

#: How many visits a place gets before novelty outweighs comfort (§8.3).
#:
#: Three, and the direction is the point: for this age repetition *is* the
#: pleasure, so the default is to go back somewhere known and the discovery has to
#: be earned. One would make every other story a new place, which is a world that
#: never becomes familiar; never discovering would make the map stop growing, which
#: is the other half of §8.3.
NOVELTY_AFTER_VISITS = 3

#: How many names one place carries into a prompt. A place with a cast of twenty is
#: a place whose description no longer fits in the plan request, and the ones a
#: child remembers are the ones they met first.
MAX_INHABITANTS = 8


@dataclass(frozen=True, slots=True)
class Setting:
    """Where this adventure happens, as the planner and the client both need it.

    One object rather than a `WorldPlace` row, because the plan is built *before*
    the adventure exists and therefore before a first visit can be recorded — and
    because a revisit and a discovery have to be told apart in the prompt (§8.3's
    comfort against novelty) long after both have become rows.
    """

    world: World
    name: str
    mood: str
    description: str
    inhabitants: tuple[str, ...]
    #: True when the child has been here before. What turns "arrive somewhere new"
    #: into "come back to somewhere they know" in the plan request.
    returning: bool
    #: The `WorldPlace` row, when there is one already. `None` on a discovery, until
    #: `record_visit` writes it.
    place_id: Any = None

    def as_plan_fields(self) -> dict[str, Any]:
        """What goes on `Adventure.plan`, so every later reader agrees.

        The palette the child's screen wears, the place the beats are set in, and
        the row the inhabitants are written back to — all read off the adventure
        rather than recomputed, because recomputing would let a child's screen and
        a child's story disagree about where they are the moment the graph moves.
        """
        return {
            "world": self.world.key,
            "world_name": self.world.name,
            "place": self.name,
            "place_id": str(self.place_id) if self.place_id else "",
            "mood": self.mood,
            "returning": self.returning,
        }


def world_by_key(key: str) -> World | None:
    return next((world for world in WORLDS if world.key == key), None)


def world_of_place(name: str) -> World | None:
    """Which world a known place belongs to, by name.

    §17.2's `world_place` has no world column and this does not add one: the place
    names are ours and are unique across the registry, so the join is a lookup. A
    row whose name we no longer recognise (we renamed a place between releases) is
    still a real place the child remembers — `select_place` keeps it, and this
    returning `None` is how it is treated as belonging to wherever they are.
    """
    return next((world for world in WORLDS if world.place_named(name) is not None), None)


# ---------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------
def world_score(world: World, interests: Sequence[Topic]) -> float:
    """How much of what this child loves this world is made of.

    A sum over matching topics rather than a count, so a child whose strongest
    interest is the sea outranks a child who once tapped a boat — and so the graph's
    weights, which are the whole of §11.7, actually decide something.
    """
    return sum(topic.weight for topic in interests if topic.name in world.topics)


def select_world(child: Child, interests: Sequence[Topic]) -> World:
    """The world this child's next adventure is set in (§8.3, §2.4).

    Highest score wins. A tie — including the very common tie at zero, which is
    every child before their first sitting has taught us anything — is broken by
    *fewest places visited in that world*, so a child we know nothing about is
    shown the map over time instead of being parked in the default forever, and
    then by registry order, so the whole thing stays deterministic.

    A world with nowhere this child may safely go is not selectable at all
    (`_reachable`). It cannot happen with the shipped registry — every world has an
    intensity-0 place — and it is checked rather than assumed because the day
    somebody adds a world of dusk-lit places is the day a three-year-old would
    otherwise be sent to one.
    """
    ceiling = settings_for(child).intensity_ceiling
    reachable = [world for world in WORLDS if _reachable(world, ceiling)]
    if not reachable:
        logger.error("story.no_world_for_ceiling child=%s ceiling=%s", child.pk, ceiling)
        return DEFAULT_WORLD

    visits = _visits_by_world(child)
    chosen = max(
        reachable,
        key=lambda world: (
            world_score(world, interests),
            -visits.get(world.key, 0),
            -WORLDS.index(world),
        ),
    )
    # The *count*, not the topics (§13.8, the audit's F-7). This line used to
    # write a child's uuid next to the three things they love, on every adventure
    # start, into the one store no delete path reaches — while the disclosure said
    # raw activity logs are kept briefly and discarded. The count and the world key
    # carry the whole operational signal: they say whether the selection had
    # anything to go on and which way it went. `manage.py memories --child <id>`
    # is where a person who genuinely needs the topics reads them, from the
    # database, which a deletion does reach.
    logger.info(
        "story.world_selected child=%s world=%s score=%.2f interests=%d",
        child.pk,
        chosen.key,
        world_score(chosen, interests),
        len(interests),
    )
    return chosen


def select_place(child: Child, world: World) -> Setting:
    """Somewhere in that world to arrive at — known or new (§8.3, §11.6).

    Comfort first, and that ordering is a claim about three-to-seven-year-olds
    rather than a coin toss: going back to the Hollow Oak and finding it *the same*
    is the experience this table exists to produce, and it is worth more at this age
    than novelty is. So a known place is reused until it has been visited
    `NOVELTY_AFTER_VISITS` times and there is somewhere new to go.

    Nothing above the child's ceiling is ever offered, on either branch — including
    the revisit branch, because a parent who lowers the ceiling has changed where
    their child may go, not merely where they may go next.
    """
    ceiling = settings_for(child).intensity_ceiling
    known = [row for row in _known_places(child, world) if _intensity_of(row) <= ceiling]
    unseen = [
        place
        for place in world.places
        if place.intensity <= ceiling and not any(row.name == place.name for row in known)
    ]

    familiar = known[0] if known else None
    if familiar is not None:
        if familiar.visit_count < NOVELTY_AFTER_VISITS:
            return _revisit(world, familiar)
        if not unseen:
            # Every place in this world is known *and* worn out, and there is
            # nowhere new inside the ceiling. Going back is still a better answer
            # than inventing somewhere we cannot promise to draw the same way twice
            # — but it must be somewhere *else* they know, not the place they were
            # last time.
            #
            # `known[-1]` rather than `known[0]`: the list is newest-visited first,
            # so this is the place they have not seen for longest. Taking the head
            # instead is a fixed point — the visit we are about to record puts that
            # same row back at the head — and a child with a strong interest graph
            # never leaves the world (`world_score` wins outright every sitting, so
            # `select_world`'s novelty tie-break never fires for them). The result
            # was a map that stopped moving: every adventure after the ninth in the
            # same place, and, because discovery walks the registry in order, always
            # the *most intense* place in that world. §8.3 asks for
            # repetition-with-variation; this is the variation.
            logger.info("story.world_exhausted child=%s world=%s", child.pk, world.key)
            return _revisit(world, known[-1])

    if not unseen:
        # Nowhere known and nowhere new: this world holds nothing at or below the
        # child's ceiling. `select_world` does not allow that, so this is reachable
        # only by a caller naming a world itself — and the answer is to leave the
        # world rather than to relax the ceiling. §11.6 outranks §8.3: a child sent
        # somewhere else is a story with a different backdrop, and a child taken past
        # their ceiling is the failure the whole design is arranged around.
        logger.warning("story.world_unreachable child=%s world=%s", child.pk, world.key)
        return _elsewhere(child, exclude=world)

    return _discover(world, unseen[0])


def _elsewhere(child: Child, *, exclude: World) -> Setting:
    """The gentlest place in any other world this child may go (§11.6).

    Last resort, and it still obeys the ceiling. If even that is empty the ceiling
    itself is out of range — a value no parent-facing control can produce — and the
    gentlest place in the registry is both the safest answer available and something
    an operator needs to see in the log.
    """
    ceiling = settings_for(child).intensity_ceiling
    candidates = [
        (world, place)
        for world in WORLDS
        if world is not exclude
        for place in world.places
        if place.intensity <= ceiling
    ]
    if not candidates:
        logger.error("story.ceiling_out_of_range child=%s ceiling=%s", child.pk, ceiling)
        candidates = [
            (world, place)
            for world in WORLDS
            for place in world.places
            if place.intensity == min(item.intensity for item in world.places)
        ]

    world, place = min(candidates, key=lambda pair: (pair[1].intensity, WORLDS.index(pair[0])))
    return _discover(world, place)


def _revisit(world: World, row: WorldPlace) -> Setting:
    """Somewhere the child already knows, described by the row rather than the registry."""
    return Setting(
        world=world,
        name=row.name,
        mood=row.mood,
        description=row.description,
        inhabitants=tuple(str(name) for name in row.inhabitants or []),
        returning=True,
        place_id=row.pk,
    )


def _discover(world: World, discovered: Place) -> Setting:
    """Somewhere new, straight from the registry. Becomes a row at `record_visit`."""
    return Setting(
        world=world,
        name=discovered.name,
        mood=discovered.mood,
        description=discovered.description,
        inhabitants=discovered.inhabitants,
        returning=False,
    )


def setting_for(child: Child, interests: Sequence[Topic]) -> Setting:
    """The world and the place, in one call. What the planner asks for."""
    return select_place(child, select_world(child, interests))


# ---------------------------------------------------------------------------
# Remembering
# ---------------------------------------------------------------------------
def record_visit(child: Child, setting: Setting, adventure: Any) -> WorldPlace:
    """Write the visit down (§8.3, §17.2 `world_place`).

    Called once, when the adventure row exists, so `first_seen_adventure` can point
    at the story that found the place. `get_or_create` on `(child, name)` rather
    than a bare create: a discovery and a revisit are the same write here, and the
    unique constraint is what settles two adventures started at once.

    **A known place is never re-described.** The registry's text is only ever used
    to *create* the row; after that the row is what the place is, exactly as
    `cast.remember` leaves a character's descriptor alone on its second appearance.
    That is what makes yesterday's forest today's forest even after we edit the
    registry (§8.3).
    """
    now = timezone.now()
    try:
        with transaction.atomic():
            place, created = WorldPlace.objects.get_or_create(
                child=child,
                name=setting.name[:120],
                defaults={
                    "mood": setting.mood[:32],
                    "description": setting.description,
                    "inhabitants": list(setting.inhabitants),
                    "first_seen_adventure": adventure,
                },
            )
    except IntegrityError:
        # Two adventures for one child racing on the same discovery. The constraint
        # settles it; the earlier row is the place.
        existing = WorldPlace.objects.filter(child=child, name=setting.name[:120]).first()
        if existing is None:
            raise
        place, created = existing, False

    place.visit_count += 1
    place.last_visited_at = now
    place.save(update_fields=["visit_count", "last_visited_at", "updated_at"])

    logger.info(
        "story.place_visited child=%s place=%s world=%s visits=%d discovered=%d",
        child.pk,
        place.name,
        setting.world.key,
        place.visit_count,
        int(created),
    )
    return place


def remember_inhabitants(adventure: Any, characters: Iterable[Any]) -> None:
    """Note that these characters live where this adventure is set (§8.3).

    The other half of what makes a revisit feel like a return: the child comes back
    to the Hollow Oak and Bramble is still there, because the beat that invented
    Bramble wrote him into the oak's inhabitants and the next plan is told who
    should be waiting. Bounded, deduplicated, and never re-ordered — the first
    creatures a place had are the ones it is known for.

    Silent on every failure mode that is not a bug: an adventure with no place (a
    plan written before this landed), a place row that has been deleted, a beat with
    no new characters. None of them is worth failing a beat's transaction over.
    """
    names = [str(getattr(character, "name", "")).strip() for character in characters or []]
    names = [name for name in names if name]
    if not names:
        return

    place_id = str((adventure.plan or {}).get("place_id") or "")
    if not place_id:
        return

    place = WorldPlace.objects.filter(pk=place_id, child_id=adventure.child_id).first()
    if place is None:
        return

    existing = [str(item) for item in place.inhabitants or []]
    merged = list(dict.fromkeys([*existing, *names]))[:MAX_INHABITANTS]
    if merged == existing:
        return

    place.inhabitants = merged
    place.save(update_fields=["inhabitants", "updated_at"])
    logger.info("story.place_inhabitants place=%s count=%d", place.pk, len(merged))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _reachable(world: World, ceiling: int) -> bool:
    """Is there anywhere in this world a child on this ceiling may go? (§11.6)"""
    return any(place.intensity <= ceiling for place in world.places)


def _known_places(child: Child, world: World) -> list[WorldPlace]:
    """This child's rows for places in this world, most recently visited first."""
    names = [place.name for place in world.places]
    return list(
        WorldPlace.objects.filter(child=child, name__in=names).order_by("-last_visited_at", "name")
    )


def _intensity_of(place: WorldPlace) -> int:
    """The shipped intensity of a known place, or the gentlest reading of one we
    no longer hold a template for.

    Zero for an unrecognised name, and that is safe rather than lax: every shipped
    place is written to be suitable for a three-year-old, and `intensity` ranks
    which are gentler still. A row we cannot place is one a child has already been
    to happily, and refusing to take them back would break the continuity §8.3 is
    for in order to re-decide something already decided.
    """
    world = world_of_place(place.name)
    template = world.place_named(place.name) if world is not None else None
    return template.intensity if template is not None else 0


def _visits_by_world(child: Child) -> dict[str, int]:
    """How many visits this child has made to each world, for the novelty tie-break."""
    counts: dict[str, int] = {}
    for row in WorldPlace.objects.filter(child=child):
        world = world_of_place(row.name)
        if world is not None:
            counts[world.key] = counts.get(world.key, 0) + max(1, row.visit_count)
    return counts
