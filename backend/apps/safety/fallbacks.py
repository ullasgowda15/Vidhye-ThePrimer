"""Pre-vetted safe fallback beats (PRODUCT.md §13.3, §19.6).

> *"Fallbacks are safe by construction: if generation fails safety twice, we
> serve a **pre-vetted, human-reviewed fallback beat** so the experience degrades
> gracefully, never dangerously."*

**These are written by a person, not generated.** That is the entire point of the
file: the one path a child reaches when the generator has produced something
unsafe cannot itself run the generator. Every beat below was written by hand,
read, and is under version control; a change to one is a code review.

**A child must not be able to tell.** A fallback is a *beat*, not an apology and
not a degraded mode. It has narration, a scene to illustrate, a warm question and
two illustrated options, and it goes down the same pipeline as any other beat — it
is drawn, it is voiced, it is streamed, it is answerable, and answering it
generates a perfectly ordinary next beat. A child who lands on one has a slightly
quieter minute of story; they do not have an error.

**Nothing here is specific to a plot.** These have to follow *any* beat of *any*
adventure without contradicting it, so they are built out of the three things
always true: the child, the companion, and somewhere gentle to be. Note what they
never do — name a place from the plan, reference something that happened, or
promise something the next beat has to deliver.

**They are cheap on purpose.** The illustration prompts carry no plot and no
proper nouns, so the `(style, character, scene)` cache tuple (§16.6) is identical
for every child who shares a companion descriptor: the first fallback ever served
pays for the picture, and the rest are free. The narration is personalised, so the
*voice* line is not shared — that is the deliberate trade, since the child hearing
their own name is what makes this not read as canned.

Adding one is welcome; every entry must (a) end warm, (b) ask something with no
wrong answer, (c) name no location the story might contradict, (d) carry a
`tomorrow_hook` that names something **in its own template**, and (e) pass the
red-team suite's own policy check, which is asserted in the tests beside it.
"""

from __future__ import annotations

import hashlib
from typing import Any

from apps.learning.demands import NO_OBJECTIVE
from apps.profiles.models import AGE_BAND_OLDER, AGE_BAND_YOUNGER, Child
from apps.story.ai.schemas import Beat as BeatContract

#: The emotion a fallback carries unless the caller asks for another. Chosen
#: because it is the one tone that can follow *anything*: a beat that was blocked
#: for being too frightening and a beat that was blocked for being too odd both
#: resolve believably into a quiet, safe moment.
DEFAULT_EMOTION = "warm_calm"

#: Marker written into `learning.skill_evidence_key` so a fallback is
#: identifiable in the data forever after. The `Beat` row's `safety_status`
#: says the same thing, but this survives into any export of the beat contract.
FALLBACK_EVIDENCE_KEY = "safe_fallback"

#: `{child}` and `{companion}` are filled in; nothing else is. Illustration
#: prompts are deliberately free of both, so they hash identically across
#: children (§16.6) — see the module docstring.
_YOUNGER: tuple[dict[str, Any], ...] = (
    {
        "narration": (
            "I stop still and point at the grass. A tiny snail is going "
            "somewhere, very slowly. {child}, look how shiny its little shell is!"
        ),
        "setting": "a sunny patch of soft grass",
        "illustration_prompt": (
            "a small friendly snail with a shiny swirled shell crossing a sunny patch "
            "of soft green grass, a few round pebbles and one white daisy nearby, "
            "warm afternoon light"
        ),
        "prompt_audio": "Shall we wave to the snail, or watch it a bit longer?",
        "choices": (
            ("wave_hello", "Wave hello", "one open friendly hand waving, soft warm light"),
            (
                "watch_it_go",
                "Watch it go",
                "a shiny swirled snail shell on a green leaf, close up and gentle",
            ),
        ),
        "next_hint": "whatever the child picks -> the snail reaches a leaf and settles happily",
        "tomorrow_hook": "Tomorrow, let's come back and see if that snail found its leaf.",
    },
    {
        "narration": (
            "A warm little breeze comes past and I laugh. My ears wobble! "
            "{child}, the breeze wants to play with us."
        ),
        "setting": "an open meadow with the wind moving through it",
        "illustration_prompt": (
            "tall soft meadow grass bending in a gentle breeze, three round dandelion "
            "seeds floating in warm sunlight, a wide calm sky"
        ),
        "prompt_audio": "Shall we chase the floaty seeds, or lie down and watch the sky?",
        "choices": (
            (
                "chase_seeds",
                "Chase the floaty seeds",
                "three round white dandelion seeds drifting in warm light",
            ),
            (
                "watch_sky",
                "Watch the sky",
                "a wide soft blue sky with two small round clouds, seen from below",
            ),
        ),
        "next_hint": "whatever the child picks -> the breeze settles and everything is calm",
        "tomorrow_hook": "Tomorrow, let's come back and see if the breeze wants to play.",
    },
    {
        "narration": (
            "I find a smooth round stone and hold it up. It is warm from the "
            "sun. Feel that, {child}! The sun left it here for us."
        ),
        "setting": "a quiet bank of smooth sun-warmed stones",
        "illustration_prompt": (
            "a small pile of smooth round sun-warmed stones in soft cream and grey, "
            "one held up to catch golden light, moss growing between them"
        ),
        "prompt_audio": "Shall we keep the warm stone, or find one for a friend?",
        "choices": (
            (
                "keep_it",
                "Keep the warm stone",
                "one smooth cream-coloured pebble glowing in golden light",
            ),
            (
                "find_another",
                "Find one for a friend",
                "a scattering of smooth pebbles among soft green moss",
            ),
        ),
        "next_hint": "whatever the child picks -> the stone is admired and the moment stays gentle",
        "tomorrow_hook": "Tomorrow, let's come back and find another warm stone.",
    },
)

_OLDER: tuple[dict[str, Any], ...] = (
    {
        "narration": (
            "I crouch down and go very quiet. Under the fern there is a "
            "line of tiny ants, all carrying something, all going the same way. "
            "Look, {child} — I think they're taking it home. "
            "Everyone is doing their bit."
        ),
        "setting": "the cool green shade beneath a fern",
        "illustration_prompt": (
            "a neat line of small friendly ants each carrying a crumb or a seed, "
            "walking beneath the cool green shade of a curling fern, dappled light on "
            "the soft earth"
        ),
        "prompt_audio": "Shall we follow the ants home, or leave them a crumb to find?",
        "choices": (
            (
                "follow_them",
                "Follow the ants home",
                "a winding trail through soft earth under green ferns",
            ),
            (
                "leave_a_crumb",
                "Leave them a crumb",
                "one golden crumb resting on a broad green leaf in dappled light",
            ),
        ),
        "next_hint": (
            "whatever the child picks -> the ants get their crumb home and everyone is pleased"
        ),
        "tomorrow_hook": "Tomorrow, let's come back and see if the ants are still busy.",
    },
    {
        "narration": (
            "The light has gone gold and long, the way it does near the end of an "
            "afternoon. I sit down beside you, {child}, and let out a happy sigh. "
            "This is my favourite bit of the day. The quiet bit."
        ),
        "setting": "a slope of grass in late golden afternoon light",
        "illustration_prompt": (
            "a gentle grassy slope in long golden late-afternoon light, two soft "
            "shadows stretching across it, a single tree at the top, calm and warm"
        ),
        "prompt_audio": "Shall we listen to what we can hear, or name the colours in the sky?",
        "choices": (
            (
                "listen_close",
                "Listen to the quiet",
                "tall grass in golden light with one small bird perched on a stem",
            ),
            (
                "name_colours",
                "Name the sky colours",
                "a soft evening sky in bands of peach, gold and pale blue",
            ),
        ),
        "next_hint": (
            "whatever the child picks -> the quiet is enjoyed together and nothing is hurried"
        ),
        "tomorrow_hook": (
            "Tomorrow, let's come back when the light goes golden again, and "
            "have the quiet bit together."
        ),
    },
    {
        "narration": (
            "I have found a puddle, and the puddle has found the sky. The whole "
            "sky is in it, upside down, clouds and all. {child}, "
            "we are standing on the sky!"
        ),
        "setting": "a still puddle on a path, reflecting the sky",
        "illustration_prompt": (
            "a still round puddle on a soft earth path perfectly reflecting a pale blue "
            "sky and two round clouds, small green weeds at its edge, gentle daylight"
        ),
        "prompt_audio": "Shall we count the clouds in the puddle, or step around it carefully?",
        "choices": (
            (
                "count_clouds",
                "Count the clouds",
                "two round white clouds reflected in still water",
            ),
            (
                "step_around",
                "Step around it",
                "a soft earth path curving gently around a small still puddle",
            ),
        ),
        "next_hint": (
            "whatever the child picks -> the reflection ripples and settles, and all is calm"
        ),
        "tomorrow_hook": "Tomorrow, let's come back and see if the puddle still has the sky in it.",
    },
)

_POOL: dict[str, tuple[dict[str, Any], ...]] = {
    AGE_BAND_YOUNGER: _YOUNGER,
    AGE_BAND_OLDER: _OLDER,
}


def pool_for(age_band: str) -> tuple[dict[str, Any], ...]:
    """The vetted beats for an age band, falling back to the gentlest one.

    An unrecognised band resolves to the younger pool for the same reason
    `Child.age_band` clamps downward: the bands only ever loosen as they go up,
    so the conservative direction is the safe failure.
    """
    return _POOL.get(age_band, _YOUNGER)


def _select(child: Child, pool: tuple[dict[str, Any], ...], salt: str) -> dict[str, Any]:
    """Pick one, deterministically, without a clock or a random seed.

    Deterministic because a fallback that varies between two identical calls is a
    fallback that cannot be tested and cannot be reproduced from an incident row.
    Keyed on the child *and* a salt from the caller — so two fallbacks inside one
    adventure are different beats rather than the same paragraph twice, while a
    replay of the *same* position is the same beat the child already heard.
    """
    digest = hashlib.sha256(f"{child.pk}:{salt}".encode()).hexdigest()
    return pool[int(digest[:8], 16) % len(pool)]


def safe_fallback_beat(
    child: Child,
    *,
    emotion: str = DEFAULT_EMOTION,
    seq: int = 1,
    is_final: bool = False,
) -> BeatContract:
    """One pre-vetted warm beat, personalised, as the generator's contract (§13.3).

    Returns the *contract*, not a database row: this module has no business
    writing story state, and the orchestrator persists it through the same
    `_persist_beat` every generated beat goes through — which is what makes a
    fallback identical to a normal beat in the database, in the stream, in the
    media pipeline and on the child's screen.

    `is_final` drops the question. The warm close asks nothing (§8.8), and
    `orchestrator.normalise_choices` would strip the options anyway — doing it
    here too means the *narration* stops asking as well, rather than a child
    hearing a question with nothing to answer it with.
    """
    template = _select(child, pool_for(child.age_band), salt=str(seq))
    companion_name = _companion_name(child)
    fill = {"child": child.first_name, "companion": companion_name}

    narration = template["narration"].format(**fill)
    if is_final:
        # The hook comes from the *selected* template. It used to be one fixed
        # sentence about a snail appended to whichever beat was drawn, so five
        # closes out of six promised a child something they had never seen — the
        # Day-5 misattribution class, landing in the one beat whose whole job is
        # to make tomorrow worth coming back for (§8.8). A close that names the
        # thing the child was actually just looking at is the entire point.
        narration = (
            f"{narration} 'Thank you for today, {child.first_name},' says {companion_name}. "
            f"'{template['tomorrow_hook']}'"
        )

    interaction: dict[str, Any] = (
        {"type": "none", "prompt_audio": None, "expected": None, "choices": None}
        if is_final
        else {
            "type": "choose",
            "prompt_audio": template["prompt_audio"],
            "expected": None,
            "choices": [
                {"key": key, "label": label, "icon_prompt": icon}
                for key, label, icon in template["choices"]
            ],
        }
    )

    return BeatContract.model_validate(
        {
            "beat_id": f"b{seq}",
            "narration": narration,
            "scene": {
                "setting": template["setting"],
                "illustration_prompt": template["illustration_prompt"],
                # The companion and nobody else. A fallback must not introduce a
                # character, because the beat after it would then be expected to
                # remember someone the story never actually met (§8.2).
                "characters_present": ["companion"],
                "new_characters": [],
            },
            "interaction": interaction,
            "learning": {
                # A fallback teaches nothing, and says so in the one field the
                # Learning Engine reads: `engine.evidence_from` refuses to draw
                # evidence from a beat marked this way, so a degraded sitting is a
                # sitting with less evidence rather than one that taught us
                # something false about the child (§10.5).
                "objective_id": NO_OBJECTIVE,
                "difficulty": 0.0,
                "skill_evidence_key": FALLBACK_EVIDENCE_KEY,
            },
            "emotion": emotion,
            "continuity_refs": [],
            "next_hint": template["next_hint"],
        }
    )


def _companion_name(child: Child) -> str:
    from django.core.exceptions import ObjectDoesNotExist

    try:
        return child.companion.name
    except ObjectDoesNotExist:
        # Never "the companion is missing" — a child with no companion cannot
        # reach a story at all (`views.CompanionRequired`), so this is only ever
        # a test or a half-deleted account.
        return "Your friend"
