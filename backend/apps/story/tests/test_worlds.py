"""Every child a different world (PRODUCT.md §8.3, §2.4, §11.6, §15.1).

Day 11's acceptance criteria, in the order they are stated:

* the planner prefers high-interest worlds and reuses what the child knows (2),
* **two profiles produce structurally different adventures for the same
  objective** (3),
* a place persists and is returned to across two adventures (5),
* and the fear ceiling is respected in all of it (6).

The third one is why this file exists at all, and it is asserted structurally
rather than by eyeballing prose: the two children below are the same age, are given
the same objective, and are told a story by the *same scripted model*. Every
difference the tests find therefore comes from the personalisation layer and from
nowhere else — which is exactly the claim §2.4 makes and the one a screenshot of
two nice-looking stories could never prove.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.companion.models import Character
from apps.companion.service import create_companion
from apps.memory import interest
from apps.memory.models import WorldPlace
from apps.profiles.models import Child
from apps.story import orchestrator, worlds
from apps.story.ai.prompts import story as story_prompts

pytestmark = pytest.mark.django_db


def make_child(
    email: str,
    name: str,
    *,
    interests: list[str],
    ceiling: int = 2,
    age: int = 5,
) -> Child:
    parent = get_user_model().objects.create_user(email=email, password="storybook-primer-42")
    child = Child.objects.create(
        parent=parent,
        first_name=name,
        birthdate=datetime.date(datetime.date.today().year - age, 5, 4),
        interests=interests,
        intensity_ceiling=ceiling,
    )
    companion = create_companion(
        child=child,
        archetype="dragon",
        name="Pip",
        palette="ember",
        personality_preset="brave_spark",
    )
    companion.visual_descriptor = "a small round friendly dragon with warm ember scales"
    companion.save(update_fields=["visual_descriptor"])
    return child


@pytest.fixture(autouse=True)
def no_media_worker(monkeypatch: Any) -> None:
    """Nothing here is about pictures; keep the media task out of it."""
    monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)


@pytest.fixture
def dinosaur_child(db: Any) -> Child:
    return make_child("worlds-rex@example.com", "Rex", interests=["dinosaurs", "digging"])


@pytest.fixture
def ocean_child(db: Any) -> Child:
    """The same age as `dinosaur_child`, deliberately.

    Every difference between their two adventures has to come from what they love,
    not from their birthdays — otherwise "two profiles get different worlds" would
    be a test about the age band.
    """
    return make_child("worlds-nia@example.com", "Nia", interests=["the sea", "whales"])


# ---------------------------------------------------------------------------
# The registry's own invariants
# ---------------------------------------------------------------------------
def test_every_world_has_somewhere_the_gentlest_child_may_go() -> None:
    """The invariant `select_world` depends on (§11.6).

    Without it, a child on `intensity_ceiling=0` would eventually be handed a world
    whose every place is above their ceiling — and the failure would be a place
    chosen anyway, which is the one outcome this design exists to make impossible.
    """
    for world in worlds.WORLDS:
        assert any(place.intensity == 0 for place in world.places), world.key


def test_world_keys_are_stable_and_unique() -> None:
    """These strings cross the stack: they are the `data-world` palette keys (§15.1).

    A rename here silently repaints a child's screen in the default theme, so the
    list is written down in two places on purpose — here and in
    `frontend/src/design/worlds.ts` — and asserted in both.
    """
    keys = [world.key for world in worlds.WORLDS]

    assert keys == ["whispering_woods", "star_harbor", "coral_deep", "sunlit_canyon"]
    assert len(set(keys)) == len(keys)
    # Place names are the join between a `WorldPlace` row and its registry entry
    # (`worlds.world_of_place`), so they have to be unique across worlds too.
    names = [place.name for world in worlds.WORLDS for place in world.places]
    assert len(set(names)) == len(names)


# ---------------------------------------------------------------------------
# Criterion 2: the planner prefers what the child loves
# ---------------------------------------------------------------------------
def test_the_world_follows_the_interest_graph(dinosaur_child: Child) -> None:
    """A dinosaur child goes to the canyon, and stops going there when they move on.

    The second half is the part worth having a test for: an interest graph that
    could only ever add is a graph that pins a child to whatever they liked at
    three.
    """
    assert (
        worlds.select_world(dinosaur_child, interest.top_interests(dinosaur_child)).key
        == "sunlit_canyon"
    )

    for _ in range(4):
        interest.apply_reinforcement(dinosaur_child, {"whale": interest.SPOKE})

    assert (
        worlds.select_world(dinosaur_child, interest.top_interests(dinosaur_child)).key
        == "coral_deep"
    )


def test_a_child_we_know_nothing_about_is_shown_the_map(db: Any) -> None:
    """No interests, no graph: the tie-break is novelty, not a default forever.

    A child whose parent skipped the interests question would otherwise get the
    same world every night — which is the generic-content failure of §2.4 arriving
    by a side door.
    """
    quiet = make_child("worlds-quiet@example.com", "Sam", interests=[])
    first = worlds.select_world(quiet, interest.top_interests(quiet))
    worlds.record_visit(quiet, worlds.select_place(quiet, first), None)

    second = worlds.select_world(quiet, interest.top_interests(quiet))

    assert first.key == worlds.DEFAULT_WORLD.key
    assert second.key != first.key


def test_the_plan_request_carries_the_child_s_own_world(dinosaur_child: Child) -> None:
    """Criterion 2, at the prompt: interests, the place, and the cast they know."""
    Character.objects.create(
        child=dinosaur_child,
        key="juniper",
        name="Juniper",
        visual_descriptor="a long-necked dinosaur, dust-grey with cream freckles",
    )
    personalization = orchestrator.personalize(dinosaur_child)
    setting = personalization.setting

    request = story_prompts.plan_request(
        world=setting.world.name,
        place=setting.name,
        description=setting.description,
        mood=setting.mood,
        inhabitants=setting.inhabitants,
        returning=setting.returning,
        interests=personalization.interest_names,
        characters=personalization.character_names,
    )

    assert setting.name in request
    assert "dinosaur" in request
    assert "Juniper" in request
    assert story_prompts.DISCOVERY_RULE in request, "they have never been here"
    assert "The learning objective belongs inside the plot" in request, "still the base ask"


def test_the_rest_of_the_map_reaches_the_plan_too(ocean_child: Child, fake_ai: Any) -> None:
    """§8.3's map is a *map*: today's story can look toward somewhere real.

    The known place the child is not at today is offered as somewhere to point
    toward — which is what makes the close's "tomorrow, let's go and see…" a place
    they have actually been rather than a noun the model invented.
    """
    world = worlds.world_by_key("coral_deep")
    assert world is not None
    for _ in range(worlds.NOVELTY_AFTER_VISITS):
        worlds.record_visit(ocean_child, worlds.select_place(ocean_child, world), None)
    first_place = WorldPlace.objects.get(child=ocean_child).name

    orchestrator.create_adventure(ocean_child)
    plan_prompt = next(user for _, user in fake_ai.prompts if "Plan today's adventure" in user)

    assert story_prompts.KNOWN_PLACES_RULE.split("{")[0].strip() in plan_prompt
    assert first_place in plan_prompt.split("Elsewhere in this child's world")[1]


# ---------------------------------------------------------------------------
# Criterion 3: two profiles, one objective, two different adventures
# ---------------------------------------------------------------------------
def test_two_profiles_produce_structurally_different_worlds(
    dinosaur_child: Child, ocean_child: Child, fake_ai: Any
) -> None:
    """The day's headline claim, asserted on structure rather than on prose.

    Same age, same catalog, same scripted writer, same objective — and four things
    differ: the world, the place, who lives there, and the palette the child's
    screen wears. None of those is a swapped noun; each is a different branch of the
    planner having been taken.
    """
    rex = orchestrator.create_adventure(dinosaur_child)
    nia = orchestrator.create_adventure(ocean_child)

    assert (
        rex.primary_objective_id == nia.primary_objective_id
    ), "the point of the comparison is that only personalisation differs"
    assert rex.plan["world"] == "sunlit_canyon"
    assert nia.plan["world"] == "coral_deep"
    assert rex.plan["place"] != nia.plan["place"]
    assert rex.plan["mood"] != nia.plan["mood"]

    rex_place = WorldPlace.objects.get(child=dinosaur_child)
    nia_place = WorldPlace.objects.get(child=ocean_child)
    assert set(rex_place.inhabitants).isdisjoint(nia_place.inhabitants)

    # And it reaches the model: the two plan prompts are different documents.
    plans = [user for _, user in fake_ai.prompts if "Plan today's adventure" in user]
    assert len(plans) == 2
    assert plans[0] != plans[1]
    assert "dinosaur" in plans[0] and "sea" in plans[1]


def test_the_world_reaches_the_client_payload(ocean_child: Child, fake_ai: Any) -> None:
    """The palette is a string crossing the stack (§15.1); it has to be on the wire."""
    from apps.story.serializers import start_payload

    adventure = orchestrator.create_adventure(ocean_child)
    payload = start_payload(adventure, stream_url="/stream")

    assert payload["world"]["key"] == "coral_deep"
    assert payload["world"]["name"] == "the Coral Deep"
    assert payload["world"]["place"] == adventure.plan["place"]


# ---------------------------------------------------------------------------
# Criterion 5: a place persists across two adventures
# ---------------------------------------------------------------------------
def test_the_same_place_is_returned_to_and_is_the_same_place(
    ocean_child: Child, fake_ai: Any
) -> None:
    """§8.3's "today's forest is yesterday's forest", as two adventures.

    The second visit reuses the row rather than re-describing the place, and the
    prompt says so — a return the story does not *recognise* as a return is just a
    story that happens to reuse a noun.
    """
    first = orchestrator.create_adventure(ocean_child)
    orchestrator.end_adventure(first)
    second = orchestrator.create_adventure(ocean_child)

    assert second.plan["place"] == first.plan["place"]
    assert second.plan["place_id"] == first.plan["place_id"]
    assert second.plan["returning"] is True

    place = WorldPlace.objects.get(child=ocean_child, name=first.plan["place"])
    assert place.visit_count == 2
    assert str(place.first_seen_adventure_id) == str(first.pk)

    returning = [user for _, user in fake_ai.prompts if story_prompts.RETURNING_RULE in user]
    assert len(returning) == 1, "the second plan, and only the second"


def test_somewhere_known_is_left_behind_once_it_is_worn_out(ocean_child: Child) -> None:
    """Comfort first, but the map still grows (§8.3)."""
    world = worlds.world_by_key("coral_deep")
    assert world is not None

    for _ in range(worlds.NOVELTY_AFTER_VISITS):
        setting = worlds.select_place(ocean_child, world)
        worlds.record_visit(ocean_child, setting, None)
    first_place = setting.name

    fourth = worlds.select_place(ocean_child, world)

    assert fourth.returning is False
    assert fourth.name != first_place


def test_a_character_met_at_a_place_is_remembered_as_living_there(
    ocean_child: Child, fake_ai: Any
) -> None:
    """The other half of a return: the child comes back and Mo is still there (§8.3)."""
    adventure = orchestrator.create_adventure(ocean_child)
    fake_ai.queue_beat_response(_beat_introducing_mo())

    orchestrator.create_beat(adventure, seq=1)

    place = WorldPlace.objects.get(child=ocean_child, name=adventure.plan["place"])
    assert "Mo the octopus" in place.inhabitants

    # And it is offered to the next plan, so the reunion can be in the plot.
    world = worlds.world_by_key("coral_deep")
    assert world is not None
    assert "Mo the octopus" in worlds.select_place(ocean_child, world).inhabitants


# ---------------------------------------------------------------------------
# Criterion 6: the fear ceiling, in personalised content
# ---------------------------------------------------------------------------
def test_no_child_is_ever_taken_above_their_ceiling(db: Any) -> None:
    """§11.6, made structural: the too-much place is never offered, in any world.

    Exhaustive rather than illustrative — every world, every visit — because the
    failure this prevents is silent and lands on a three-year-old.
    """
    gentlest = make_child("worlds-gentle@example.com", "Ivy", interests=[], ceiling=0, age=3)

    for world in worlds.WORLDS:
        for _ in range(len(world.places) + 2):
            setting = worlds.select_place(gentlest, world)
            template = world.place_named(setting.name)
            assert template is not None, setting.name
            assert template.intensity == 0, f"{world.key}/{setting.name}"
            worlds.record_visit(gentlest, setting, None)


def test_a_lowered_ceiling_takes_a_place_back(ocean_child: Child) -> None:
    """A parent who lowers the ceiling has changed where their child may go.

    Not merely where they go *next*: a place already visited is still a place, and
    a revisit is exactly the path that would otherwise walk straight past the check.
    """
    world = worlds.world_by_key("coral_deep")
    assert world is not None
    dusk = next(place for place in world.places if place.intensity == 1)
    WorldPlace.objects.create(
        child=ocean_child,
        name=dusk.name,
        mood=dusk.mood,
        description=dusk.description,
        visit_count=1,
    )

    ocean_child.intensity_ceiling = 0
    ocean_child.save(update_fields=["intensity_ceiling"])

    assert worlds.select_place(ocean_child, world).name != dusk.name


def test_a_world_with_nowhere_gentle_enough_is_left_rather_than_entered(db: Any) -> None:
    """§11.6 outranks §8.3 when they disagree.

    Unreachable through `select_world`, which filters these out — this is the branch
    that catches a caller naming a world itself, and the assertion is that the answer
    is a *different world*, not a relaxed ceiling. A child sent somewhere else gets a
    story with a different backdrop; a child taken past their ceiling is the failure
    the whole design is arranged around.
    """
    gentlest = make_child("worlds-ivy2@example.com", "Ivy", interests=[], ceiling=0, age=3)
    steep = worlds.World(
        key="thunder_top",
        name="Thunder Top",
        mood="loud_and_high",
        topics=frozenset({"storm"}),
        places=(
            worlds.Place(
                name="the Very Top",
                mood="loud_and_high",
                description="somewhere far too big for today",
                inhabitants=(),
                intensity=3,
            ),
        ),
    )

    setting = worlds.select_place(gentlest, steep)

    assert setting.world.key != "thunder_top"
    template = setting.world.place_named(setting.name)
    assert template is not None and template.intensity == 0


def test_personalisation_cannot_widen_the_safety_policy(
    dinosaur_child: Child, fake_ai: Any
) -> None:
    """Loving something does not make it safe to show (§13, §11.6).

    The ceiling and the avoid list are still assembled from the profile on the
    personalised plan call, at the value the parent set — this is the assertion that
    would fail if a future "engagement" change ever read the interest graph into the
    safety layer.
    """
    dinosaur_child.intensity_ceiling = 1
    dinosaur_child.avoid_topics = ["volcanoes"]
    dinosaur_child.save(update_fields=["intensity_ceiling", "avoid_topics"])

    orchestrator.create_adventure(dinosaur_child)
    system = next(system for system, user in fake_ai.prompts if "Plan today's adventure" in user)

    assert "Intensity ceiling: 1 of 3" in system
    assert "volcanoes" in system


def _beat_introducing_mo() -> str:
    """A beat that invents one character, in the shape `ai.schemas.Beat` requires."""
    import json

    return json.dumps(
        {
            "beat_id": "b1",
            "narration": "A small octopus waves one careful arm from under the coral.",
            "scene": {
                "setting": "a warm lagoon",
                "illustration_prompt": "a small octopus under a coral ledge in clear water",
                "characters_present": ["pip", "mo"],
                "new_characters": [
                    {
                        "key": "mo",
                        "name": "Mo the octopus",
                        "visual_descriptor": (
                            "a small round octopus, pale lilac with darker freckles and one "
                            "curled arm held up"
                        ),
                    }
                ],
            },
            "interaction": {
                "type": "choose",
                "prompt_audio": "Shall we say hello?",
                "expected": None,
                "choices": [
                    {"key": "wave", "label": "Wave back", "icon_prompt": "a small waving arm"},
                    {"key": "wait", "label": "Sit down and wait", "icon_prompt": "warm white sand"},
                ],
            },
            "learning": {
                "objective_id": "count_to_3",
                "difficulty": 0.4,
                "skill_evidence_key": "greet_mo",
            },
            "emotion": "gentle_curious",
            "continuity_refs": ["character:mo"],
            "next_hint": "if the child waves -> Mo comes out",
        }
    )
